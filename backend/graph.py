"""All investigation evidence is read from FalkorDB, never from fixtures."""
import os
import secrets
import time
from .core import CASES, INITIAL, TRANSFERS

GRAPH_NAME = os.getenv("FALKOR_GRAPH", "ringbreak_demo")
TIMEOUT_MS = 3000

# Every read query the investigator may run. The agent loop can only reach these.
CYPHER = {
    "case_transfers": """MATCH (s:Account)-[:SENT]->(t:Transaction)-[:RECEIVED_BY]->(d:Account)
WHERE t.case_id=$case_id
RETURN t.id, s.id, d.id, t.paise, t.at_ms, t.device, t.kind
ORDER BY t.at_ms, t.id LIMIT $limit""",
    "projected_transfers": """MATCH (s:Account)-[r:TRANSFER]->(d:Account)
WHERE r.case_id=$case_id
RETURN r.tx_id, s.id, d.id, r.paise, r.at_ms, r.kind
ORDER BY r.at_ms, r.tx_id LIMIT $limit""",
    # {hops} is an integer validated to 1..3 before formatting; everything else is a parameter.
    "ordered_paths": """MATCH (:Account)-[:SENT]->(f:Transaction {{id:$flagged}})-[:RECEIVED_BY]->(start:Account)
MATCH p=(start)-[:TRANSFER*1..{hops}]->(:Account)
WITH f, p, relationships(p) AS rs
WHERE all(r IN rs WHERE r.case_id = f.case_id AND r.at_ms > f.at_ms)
  AND all(i IN range(0, size(rs) - 2) WHERE rs[i].at_ms < rs[i + 1].at_ms)
RETURN [r IN rs | r.tx_id], [n IN nodes(p) | n.id], [r IN rs | r.kind], rs[size(rs) - 1].at_ms
LIMIT $limit""",
    "shared_device": """MATCH (f:Transaction {id:$flagged})-[:VIA]->(dev:Device)<-[:VIA]-(t:Transaction)<-[:SENT]-(a:Account)
RETURN dev.id, a.id, t.id, t.case_id
ORDER BY t.at_ms, t.id LIMIT $limit""",
    "account_group_wcc": """CALL algo.WCC({nodeLabels: ['Account'], relationshipTypes: ['TRANSFER']})
YIELD node, componentId
WITH componentId, collect(node.id) AS members
WHERE $account IN members
RETURN componentId, members""",
    "account_group_fallback": """MATCH (a:Account {id:$account})-[:TRANSFER*1..4]-(b:Account)
RETURN DISTINCT b.id LIMIT $limit""",
}


def client():
    from falkordb import FalkorDB
    return FalkorDB(host=os.getenv("FALKOR_HOST", "localhost"),
                    port=int(os.getenv("FALKOR_PORT", "6379")),
                    username=os.getenv("FALKOR_USERNAME") or None,
                    password=os.getenv("FALKOR_PASSWORD") or None,
                    socket_connect_timeout=3)


def graph():
    return client().select_graph(GRAPH_NAME)


def ping():
    """Server reachability. A read query would fail on a graph that is not seeded yet."""
    return client().connection.ping()


def seed(g):
    for statement in ("CREATE INDEX FOR (a:Account) ON (a.id)",
                      "CREATE INDEX FOR (t:Transaction) ON (t.id)",
                      "CREATE INDEX FOR (d:Device) ON (d.id)"):
        try:
            g.query(statement)
        except Exception:  # index already exists on a repeat seed
            pass
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
MERGE (dev:Device {id:$device}) MERGE (t)-[:VIA]->(dev)
MERGE (s)-[r:TRANSFER {tx_id:$id}]->(d)
SET r.case_id=$case_id, r.paise=$paise, r.at_ms=$at_ms, r.kind=$kind""", params)
    return {"cases": len(CASES), "accounts": len(INITIAL), "transfers": len(TRANSFERS), "graph_counts": counts(g)}


def counts(g):
    """Read back what is actually stored, so seed results come from the graph."""
    nodes = g.ro_query("MATCH (n) RETURN labels(n)[0], count(n)", timeout=TIMEOUT_MS).result_set
    edges = g.ro_query("MATCH ()-[r]->() RETURN type(r), count(r)", timeout=TIMEOUT_MS).result_set
    return {"nodes": {k: v for k, v in nodes}, "edges": {k: v for k, v in edges}}


def _check(case_id, limit, upper=100):
    if case_id not in CASES or not 1 <= limit <= upper:
        raise ValueError("invalid case or limit")


def case_transfers(g, case_id: str, limit: int = 80):
    _check(case_id, limit)
    rows = g.ro_query(CYPHER["case_transfers"], {"case_id": case_id, "limit": limit}, timeout=TIMEOUT_MS).result_set
    return [dict(zip(("id", "source", "target", "paise", "at_ms", "device", "kind"), row)) for row in rows]


def projected_transfers(g, case_id: str, limit: int = 80):
    _check(case_id, limit)
    rows = g.ro_query(CYPHER["projected_transfers"], {"case_id": case_id, "limit": limit}, timeout=TIMEOUT_MS).result_set
    return [dict(zip(("id", "source", "target", "paise", "at_ms", "kind"), row)) for row in rows]


def ordered_paths(g, flagged_id: str, max_hops: int = 3, limit: int = 100):
    """Time-ordered downstream paths traversed inside FalkorDB.

    max_hops counts the flagged transaction, so the variable-length pattern is 1..max_hops-1.
    Paths that revisit an account are dropped to match the bounded DFS verifier.
    """
    if not 2 <= max_hops <= 4 or not 1 <= limit <= 100:
        raise ValueError("max_hops must be 2..4 and limit 1..100")
    flag = g.ro_query("""MATCH (s:Account)-[:SENT]->(f:Transaction {id:$id})-[:RECEIVED_BY]->(d:Account)
RETURN s.id, d.id""", {"id": flagged_id}, timeout=TIMEOUT_MS).result_set
    if not flag:
        return []
    source = flag[0][0]
    rows = g.ro_query(CYPHER["ordered_paths"].format(hops=int(max_hops) - 1),
                      {"flagged": flagged_id, "limit": limit}, timeout=TIMEOUT_MS).result_set
    paths = []
    for tx_ids, accounts, kinds, last_ms in rows:
        accounts = [source] + list(accounts)
        if len(set(accounts)) != len(accounts):
            continue
        paths.append({"accounts": accounts, "tx_ids": [flagged_id] + list(tx_ids),
                      "last_ms": last_ms, "cashout": kinds[-1] == "cashout"})
    return sorted(paths, key=lambda p: (p["last_ms"], p["tx_ids"]))


def shared_device(g, flagged_id: str, limit: int = 20):
    if not flagged_id or not 1 <= limit <= 20:
        raise ValueError("invalid transaction or limit")
    rows = g.ro_query(CYPHER["shared_device"], {"flagged": flagged_id, "limit": limit}, timeout=TIMEOUT_MS).result_set
    return [{"device": d, "account": a, "tx_id": t, "case_id": c} for d, a, t, c in rows]


def account_group(g, account: str, limit: int = 50):
    """Accounts weakly connected to `account` over TRANSFER edges.

    Uses FalkorDB's algo.WCC; if the server build lacks it, falls back to a bounded
    undirected variable-length match and says so in `method`.
    """
    if not account or not 1 <= limit <= 50:
        raise ValueError("invalid account or limit")
    try:
        rows = g.ro_query(CYPHER["account_group_wcc"], {"account": account}, timeout=TIMEOUT_MS).result_set
        members = sorted(rows[0][1])[:limit] if rows else [account]
        return {"method": "algo.WCC", "component_id": rows[0][0] if rows else None, "members": members}
    except Exception as exc:
        rows = g.ro_query(CYPHER["account_group_fallback"], {"account": account, "limit": limit},
                          timeout=TIMEOUT_MS).result_set
        return {"method": f"bounded_match ({type(exc).__name__} on algo.WCC)", "component_id": None,
                "members": sorted({account, *(r[0] for r in rows)})[:limit]}


def time_paths(transfers, flagged_id, max_hops=3):
    """Bounded ordered DFS over graph-returned projection rows; verifies the Cypher traversal."""
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
    return sorted(paths[:100], key=lambda p: (p["last_ms"], p["tx_ids"]))


def save_review(g, case_id, account, action, rationale, evidence_ids, created_utc):
    """Append a human review as (:Review)-[:CITES]->(:Transaction). Reviews are never updated."""
    review_id = f"R{int(time.time() * 1000)}-{secrets.token_hex(2)}"
    g.query("""CREATE (r:Review {id:$id, case_id:$case_id, account:$account, action:$action,
    rationale:$rationale, evidence_ids:$evidence, created_utc:$created})
WITH r UNWIND $evidence AS tx_id
MATCH (t:Transaction {id:tx_id}) CREATE (r)-[:CITES]->(t)""",
            {"id": review_id, "case_id": case_id, "account": account or "", "action": action,
             "rationale": rationale, "evidence": list(evidence_ids), "created": created_utc})
    return review_id


def list_reviews(g, case_id, limit=50):
    rows = g.ro_query("""MATCH (r:Review {case_id:$case_id})
RETURN r.id, r.account, r.action, r.rationale, r.evidence_ids, r.created_utc
ORDER BY r.created_utc DESC LIMIT $limit""", {"case_id": case_id, "limit": limit}, timeout=TIMEOUT_MS).result_set
    return [{"id": i, "account": a or None, "action": act, "rationale": why, "evidence_ids": list(ev or []),
             "created_utc": at} for i, a, act, why, ev, at in rows]
