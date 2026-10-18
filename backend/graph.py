"""All investigation evidence is read from FalkorDB, never from fixtures."""
import os
from .core import CASES, INITIAL, TRANSFERS

GRAPH_NAME = os.getenv("FALKOR_GRAPH", "ringbreak_demo")


def graph():
    from falkordb import FalkorDB
    return FalkorDB(host=os.getenv("FALKOR_HOST", "localhost"),
                    port=int(os.getenv("FALKOR_PORT", "6379"))).select_graph(GRAPH_NAME)


def seed(g):
    # MERGE on stable IDs makes repeat runs idempotent.
    for account, balance in INITIAL.items():
        g.query("MERGE (a:Account {id:$id}) SET a.initial_paise=$balance", {"id": account, "balance": balance})
    for case_id, item in CASES.items():
        g.query("MERGE (c:Case {id:$id}) SET c.title=$title, c.flagged=$flagged",
                {"id": case_id, "title": item["title"], "flagged": item["flagged"]})
    for tx in TRANSFERS:
        params = {"id": tx.id, "case_id": tx.case_id, "source": tx.source,
                  "target": tx.target, "paise": tx.paise, "at_ms": tx.at_ms,
                  "device": tx.device, "kind": tx.kind}
        g.query("""MATCH (s:Account {id:$source}), (d:Account {id:$target})
MERGE (t:Transaction {id:$id})
SET t.case_id=$case_id, t.paise=$paise, t.at_ms=$at_ms, t.device=$device, t.kind=$kind
MERGE (s)-[:SENT]->(t) MERGE (t)-[:RECEIVED_BY]->(d)
MERGE (s)-[r:TRANSFER {tx_id:$id}]->(d)
SET r.case_id=$case_id, r.paise=$paise, r.at_ms=$at_ms, r.kind=$kind""", params)
    return {"cases": len(CASES), "accounts": len(INITIAL), "transfers": len(TRANSFERS)}


def case_transfers(g, case_id: str, limit: int = 80):
    if case_id not in CASES or not 1 <= limit <= 100:
        raise ValueError("invalid case or limit")
    rows = g.ro_query("""MATCH (s:Account)-[:SENT]->(t:Transaction)-[:RECEIVED_BY]->(d:Account)
WHERE t.case_id=$case_id
RETURN t.id, s.id, d.id, t.paise, t.at_ms, t.device, t.kind
ORDER BY t.at_ms, t.id LIMIT $limit""", {"case_id": case_id, "limit": limit}, timeout=3000).result_set
    return [dict(zip(("id", "source", "target", "paise", "at_ms", "device", "kind"), row)) for row in rows]


def projected_transfers(g, case_id: str, limit: int = 80):
    if case_id not in CASES or not 1 <= limit <= 100:
        raise ValueError("invalid case or limit")
    rows = g.ro_query("""MATCH (s:Account)-[r:TRANSFER]->(d:Account)
WHERE r.case_id=$case_id
RETURN r.tx_id, s.id, d.id, r.paise, r.at_ms, r.kind
ORDER BY r.at_ms, r.tx_id LIMIT $limit""", {"case_id": case_id, "limit": limit}, timeout=3000).result_set
    return [dict(zip(("id", "source", "target", "paise", "at_ms", "kind"), row)) for row in rows]


def shared_device(g, device: str, limit: int = 20):
    if not device or not 1 <= limit <= 20:
        raise ValueError("invalid device or limit")
    rows = g.ro_query("""MATCH (a:Account)-[:SENT]->(t:Transaction)
WHERE t.device=$device RETURN a.id, t.id ORDER BY t.at_ms LIMIT $limit""",
                      {"device": device, "limit": limit}, timeout=3000).result_set
    return [{"account": a, "tx_id": t} for a, t in rows]


def time_paths(transfers, flagged_id, max_hops=3):
    """Bounded ordered traversal over graph-returned transfer projection."""
    if not 1 <= max_hops <= 4:
        raise ValueError("max_hops must be 1..4")
    start = next((t for t in transfers if t["id"] == flagged_id), None)
    if start is None:
        return []
    paths = []
    def visit(node, at_ms, used, accounts):
        if len(used) >= max_hops:
            return
        for tx in transfers:
            if tx["source"] != node or tx["at_ms"] <= at_ms or tx["target"] in accounts:
                continue
            chain = used + [tx["id"]]
            paths.append({"accounts": accounts + [tx["target"]], "tx_ids": chain,
                          "last_ms": tx["at_ms"], "cashout": tx["kind"] == "cashout"})
            visit(tx["target"], tx["at_ms"], chain, accounts + [tx["target"]])
    visit(start["target"], start["at_ms"], [start["id"]], [start["source"], start["target"]])
    return paths[:100]
