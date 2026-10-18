"""Investigation loop: flagged transaction -> allowlisted graph tools -> evidence ->
candidate comparison -> replay -> verified explanation.

The plan is fixed and every step is recorded in a trace. An optional LLM may only
rephrase the final explanation; its text is rejected unless every transaction ID it
mentions is grounded in graph evidence and it keeps the "simulated" framing.
"""
import json
import os
import re
import time
import urllib.request

from . import graph as G
from .core import CASES, compare

TOOLS = {
    "case_transfers": G.case_transfers,
    "projected_transfers": G.projected_transfers,
    "ordered_paths": G.ordered_paths,
    "shared_device": G.shared_device,
    "account_group": G.account_group,
}
TX_ID = re.compile(r"\b[A-Z]\d{2}\b")


class InvestigationError(Exception):
    pass


def _cypher_for(tool, kwargs):
    if tool == "ordered_paths":
        return G.CYPHER["ordered_paths"].format(hops=kwargs.get("max_hops", 3) - 1)
    if tool == "account_group":
        return G.CYPHER["account_group_wcc"]
    return G.CYPHER[tool]


def call(trace, g, tool, **kwargs):
    if tool not in TOOLS:
        raise InvestigationError(f"tool not allowlisted: {tool}")
    started = time.perf_counter()
    result = TOOLS[tool](g, **kwargs)
    size = len(result) if isinstance(result, list) else len(result.get("members", []))
    trace.append({"step": len(trace) + 1, "tool": tool, "args": kwargs, "rows": size,
                  "ms": round((time.perf_counter() - started) * 1000, 1), "cypher": _cypher_for(tool, kwargs)})
    return result


def investigate(g, case_id, llm=None):
    if case_id not in CASES:
        raise InvestigationError("unknown case")
    trace = []
    flagged = CASES[case_id]["flagged"]

    txs = call(trace, g, "case_transfers", case_id=case_id)
    projection = call(trace, g, "projected_transfers", case_id=case_id)
    if {x["id"] for x in txs} != {x["id"] for x in projection}:
        raise InvestigationError("graph transaction/projection mismatch")
    flag = next((t for t in txs if t["id"] == flagged), None)
    if flag is None:
        raise InvestigationError("flagged transaction missing from graph")

    # Traverse in FalkorDB, then verify against an independent DFS over the graph-returned projection.
    verifier = G.time_paths(projection, flagged)
    try:
        paths = call(trace, g, "ordered_paths", flagged_id=flagged, max_hops=3)
        path_check = "cypher_matches_dfs" if _keys(paths) == _keys(verifier) else "cypher_differs_from_dfs_using_dfs"
        if path_check != "cypher_matches_dfs":
            paths = verifier
    except Exception as exc:
        trace.append({"step": len(trace) + 1, "tool": "ordered_paths", "error": type(exc).__name__,
                      "note": "fell back to bounded DFS over graph-returned projection"})
        paths, path_check = verifier, "dfs_only"

    devices = call(trace, g, "shared_device", flagged_id=flagged)
    group = call(trace, g, "account_group", account=flag["target"])

    # Candidates come only from intermediate accounts on time-ordered cash-out paths.
    # Shared-device association never makes an account a candidate on its own.
    downstream = [p for p in paths if p["cashout"]]
    candidate_ids = sorted({a for p in downstream for a in p["accounts"][1:-1]})
    decision_ms = flag["at_ms"] + 1
    candidates = [compare(case_id, a, decision_ms) for a in candidate_ids]
    for c in candidates:
        c["evidence_ids"] = sorted({i for p in downstream if c["account"] in p["accounts"][1:-1] for i in p["tx_ids"]})
    candidates.sort(key=lambda c: (-c["simulated_difference_now"], -c["simulated_difference_30m"], c["account"]))
    trace.append({"step": len(trace) + 1, "tool": "replay_compare", "args": {"accounts": candidate_ids,
                  "decision_ms": decision_ms}, "rows": len(candidates), "note": "deterministic local replay"})

    evidence_ids = sorted({i for p in downstream for i in p["tx_ids"]})
    grounded = {t["id"] for t in txs}
    text = explain(flag, candidates, evidence_ids, devices)
    phrasing = "deterministic"
    if llm is not None:
        phrased = llm(text)
        if phrased and verify_explanation(phrased, grounded, evidence_ids):
            text, phrasing = phrased, "llm_verified"
        else:
            phrasing = "deterministic (llm output rejected)"
    if not verify_explanation(text, grounded, evidence_ids):
        raise InvestigationError("explanation cites ungrounded evidence")

    return {"synthetic_demo": True, "case": {"id": case_id, **CASES[case_id]},
            "transfers": txs, "projection": projection, "paths": paths, "path_check": path_check,
            "shared_device": devices, "account_group": group, "candidates": candidates,
            "evidence_ids": evidence_ids, "decision_ms": decision_ms,
            "recommendation": text, "phrasing": phrasing, "trace": trace}


def _keys(paths):
    return sorted(tuple(p["tx_ids"]) for p in paths)


def explain(flag, candidates, evidence_ids, devices):
    others = sorted({d["account"] for d in devices if d["account"] != flag["source"]})
    device_note = (f" Device {flag['device']} is also used by {', '.join(others)}; device sharing alone is not treated as evidence."
                   if others else "")
    if not candidates:
        return (f"No time-ordered cash-out path follows {flag['id']} in the graph. "
                f"Do not recommend a simulated hold on this evidence.{device_note}")
    top = candidates[0]
    delay = (f" A hold after 30 minutes changes it by {top['simulated_difference_30m']} paise."
             if top["simulated_difference_30m"] != top["simulated_difference_now"] else "")
    return (f"For human review: consider a simulated outbound hold on {top['account']}. "
            f"Time-ordered graph paths cite {', '.join(evidence_ids)}. "
            f"Holding now changes simulated cash-out by {top['simulated_difference_now']} paise.{delay}{device_note}")


def verify_explanation(text, grounded_ids, evidence_ids):
    cited = set(TX_ID.findall(text))
    if not cited <= set(grounded_ids):
        return False
    if evidence_ids and not set(evidence_ids) <= cited:
        return False
    return "simulated" in text.lower()


def llm_from_env():
    """Optional phrasing via any OpenAI-compatible chat endpoint. Returns None if unset."""
    url, key, model = os.getenv("LLM_API_URL"), os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL")
    if not (url and key and model):
        return None

    def phrase(text):
        body = {"model": model, "temperature": 0, "max_tokens": 300, "messages": [
            {"role": "system", "content": "Rephrase for a fraud analyst in plain English. Keep every transaction ID "
             "and number exactly. Keep the words 'simulated' and 'human review'. Add no new facts."},
            {"role": "user", "content": text}]}
        req = urllib.request.Request(url, json.dumps(body).encode(), {
            "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.load(resp)["choices"][0]["message"]["content"].strip()
        except Exception:
            return None
    return phrase
