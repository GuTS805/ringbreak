"""RINGBREAK local API. Recommendations are for human review only."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .core import CASES, compare
from .graph import case_transfers, graph, projected_transfers, seed, shared_device, time_paths

app = FastAPI(title="RINGBREAK", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("WEB_ORIGIN", "http://localhost:3000")],
                   allow_methods=["GET", "POST"], allow_headers=["*"])
DB_PATH = Path(os.getenv("REVIEW_DB", "reviews.sqlite3"))


def connection():
    db = sqlite3.connect(DB_PATH)
    db.execute("""CREATE TABLE IF NOT EXISTS reviews
    (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, account TEXT,
     action TEXT NOT NULL, rationale TEXT NOT NULL, evidence_json TEXT NOT NULL,
     created_utc TEXT NOT NULL)""")
    db.commit()
    return db


class Review(BaseModel):
    case_id: str
    account: str | None = None
    action: str
    rationale: str
    evidence_ids: list[str]


@app.get("/health")
def health():
    try:
        graph().ro_query("MATCH (n) RETURN count(n) LIMIT 1", timeout=2000)
        return {"ok": True, "graph": "connected"}
    except Exception as exc:
        raise HTTPException(503, f"FalkorDB unavailable: {type(exc).__name__}")


@app.post("/seed")
def seed_api():
    try:
        return {"synthetic_demo": True, **seed(graph())}
    except Exception as exc:
        raise HTTPException(503, f"FalkorDB unavailable: {type(exc).__name__}")


@app.get("/cases")
def cases():
    return [{"id": key, **value} for key, value in CASES.items()]


@app.get("/cases/{case_id}")
def investigate(case_id: str):
    if case_id not in CASES:
        raise HTTPException(404, "unknown case")
    try:
        g = graph()
        txs = case_transfers(g, case_id)
        projection = projected_transfers(g, case_id)
    except Exception as exc:
        raise HTTPException(503, f"FalkorDB unavailable: {type(exc).__name__}")
    if {x["id"] for x in txs} != {x["id"] for x in projection}:
        raise HTTPException(500, "graph transaction/projection mismatch")
    flagged = CASES[case_id]["flagged"]
    paths = time_paths([{**x, "device": next((t["device"] for t in txs if t["id"] == x["id"]), "")}
                        for x in projection], flagged)
    flag = next((t for t in txs if t["id"] == flagged), None)
    if flag is None:
        raise HTTPException(500, "flagged transaction missing from graph")
    device_evidence = shared_device(g, flag["device"])
    downstream = [p for p in paths if p["cashout"]]
    candidate_ids = sorted({account for p in downstream for account in p["accounts"][1:-1]})
    comparisons = [compare(case_id, account, flag["at_ms"] + 1) for account in candidate_ids]
    comparisons.sort(key=lambda x: (-x["simulated_difference_now"], x["account"]))
    evidence_ids = sorted({i for p in downstream for i in p["tx_ids"]})
    suggestion = (f"Review a simulated outbound hold on {comparisons[0]['account']}. "
                  f"Time-ordered graph path cites {', '.join(evidence_ids)}. "
                  f"Simulated cash-out difference: {comparisons[0]['simulated_difference_now']} paise."
                  if comparisons else "No time-ordered cash-out path found. Do not recommend a hold on this evidence.")
    return {"synthetic_demo": True, "case": {"id": case_id, **CASES[case_id]},
            "transfers": txs, "projection": projection, "paths": paths,
            "shared_device": device_evidence, "candidates": comparisons,
            "evidence_ids": evidence_ids, "recommendation": suggestion}


@app.get("/reviews/{case_id}")
def reviews(case_id: str):
    if case_id not in CASES:
        raise HTTPException(404, "unknown case")
    with connection() as db:
        rows = db.execute("SELECT id,account,action,rationale,evidence_json,created_utc FROM reviews WHERE case_id=? ORDER BY id DESC",
                          (case_id,)).fetchall()
    return [{"id": r[0], "account": r[1], "action": r[2], "rationale": r[3],
             "evidence_ids": json.loads(r[4]), "created_utc": r[5]} for r in rows]


@app.post("/reviews")
def save_review(review: Review):
    if review.case_id not in CASES or review.action not in ("recommend_hold", "no_hold", "needs_review"):
        raise HTTPException(400, "invalid case or action")
    investigation = investigate(review.case_id)
    valid_ids = {t["id"] for t in investigation["transfers"]}
    if not set(review.evidence_ids) <= valid_ids or len(review.rationale.strip()) < 10:
        raise HTTPException(400, "invalid evidence or rationale")
    if review.action == "recommend_hold" and (not review.account or
            review.account not in {c["account"] for c in investigation["candidates"]} or
            not review.evidence_ids):
        raise HTTPException(400, "hold requires a ranked account and cited evidence")
    with connection() as db:
        cur = db.execute("INSERT INTO reviews (case_id,account,action,rationale,evidence_json,created_utc) VALUES (?,?,?,?,?,?)",
                         (review.case_id, review.account, review.action, review.rationale,
                          json.dumps(review.evidence_ids), datetime.now(timezone.utc).isoformat()))
        db.commit()
    return {"id": cur.lastrowid, "status": "human_review_recommendation", "synthetic_demo": True}
