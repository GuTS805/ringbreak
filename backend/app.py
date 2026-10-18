"""RINGBREAK local API. Recommendations are for human review only."""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()  # before backend modules read FALKOR_* settings

from .agent import InvestigationError, llm_from_env
from .agent import investigate as run_investigation
from .core import CASES
from .graph import graph, list_reviews, ping, seed
from .graph import save_review as store_review

app = FastAPI(title="RINGBREAK", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("WEB_ORIGIN", "http://localhost:3000")],
                   allow_methods=["GET", "POST"], allow_headers=["*"])


class Review(BaseModel):
    case_id: str
    account: str | None = None
    action: str
    rationale: str = Field(max_length=1000)
    evidence_ids: list[str] = Field(max_length=20)


@app.get("/health")
def health():
    try:
        ping()
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
        return run_investigation(graph(), case_id, llm=llm_from_env())
    except InvestigationError as exc:
        raise HTTPException(500, str(exc))
    except Exception as exc:
        raise HTTPException(503, f"FalkorDB unavailable: {type(exc).__name__}")


@app.get("/reviews/{case_id}")
def reviews(case_id: str):
    if case_id not in CASES:
        raise HTTPException(404, "unknown case")
    try:
        return list_reviews(graph(), case_id)
    except Exception as exc:
        raise HTTPException(503, f"FalkorDB unavailable: {type(exc).__name__}")


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
    review_id = store_review(graph(), review.case_id, review.account, review.action, review.rationale.strip(),
                             review.evidence_ids, datetime.now(timezone.utc).isoformat())
    return {"id": review_id, "status": "human_review_recommendation", "synthetic_demo": True}
