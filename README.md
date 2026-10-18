# RINGBREAK

A local, **synthetic demo** for investigating fictional UPI transfer networks with FalkorDB and comparing simulated outbound holds. All money is in paise internally; all event times are UTC milliseconds. A recommendation is an audit record for **human review**, never a bank action.

## Start

Requires Docker Compose, Python 3.12+, and Node.js 20+ with npm. From this directory:

```powershell
docker compose up -d
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
Copy-Item .env.example .env
uvicorn backend.app:app --reload --port 8000
```

In another terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev
```

Open `http://localhost:3000`, click **Seed FalkorDB**, then choose a case. The API is at `http://localhost:8000/docs`. Seeding repeats safely using stable IDs and Cypher `MERGE`. The review log is local SQLite and is excluded from Git.

For macOS/Linux, use `source .venv/bin/activate` and `cp` instead of the PowerShell equivalents. Run `python -m unittest discover -s tests -v` for deterministic core tests; run `npm run build` in `frontend` for the UI build. A live integration smoke test should seed, fetch `/cases/split`, verify `A01 → A03 → A05`, submit a review, and read it back.

## Model and architecture

`Account -[:SENT]-> Transaction -[:RECEIVED_BY]-> Account` is the canonical event record. Each event also has an `Account -[:TRANSFER {tx_id, at_ms, paise}]-> Account` projection for bounded traversal. The API reads both views from FalkorDB and refuses a case if their transaction IDs differ. Seed fixtures are used by the deterministic replay, not as a substitute for graph evidence. The API queries at most 80 case events, 20 shared-device events, with a 3-second query timeout. A bounded DFS (maximum three transaction hops and 100 reported paths) checks event order after fetching projected edges. This avoids reliance on Neo4j-only path procedures.

Key Cypher (full statements in [`backend/graph.py`](backend/graph.py)):

```cypher
MATCH (s:Account)-[:SENT]->(t:Transaction)-[:RECEIVED_BY]->(d:Account)
WHERE t.case_id=$case_id
RETURN t.id, s.id, d.id, t.paise, t.at_ms, t.device, t.kind
ORDER BY t.at_ms, t.id LIMIT $limit
```

```cypher
MATCH (s:Account)-[r:TRANSFER]->(d:Account)
WHERE r.case_id=$case_id
RETURN r.tx_id, s.id, d.id, r.paise, r.at_ms, r.kind
ORDER BY r.at_ms, r.tx_id LIMIT $limit
```

The official [FalkorDB Python client](https://github.com/FalkorDB/falkordb-py) documents `select_graph`, `query`, and `ro_query`; [FalkorDB's query documentation](https://falkordb.github.io/docs-staging/commands/graph.query/) documents parameterized Cypher. No graph algorithm procedure is assumed.

## Cases and expected seed truth

| Case | Flag | Expected observation |
| --- | --- | --- |
| Split/converge/cash-out | A01 | A01 → A03 → A05 gives a time-ordered path through MULE_A and HUB; another branch joins HUB. |
| Benign shared-device/merchant | B01 | B01 and B02 share a device, but no forwarding or cash-out path exists. Do not recommend a hold from association alone. |
| Delayed forwarding | C01 | RELAY_C forwards at minute 20. A hold immediately after C01 blocks C02; a hold after 30 minutes misses it. |
| No evidence | D01 | No downstream transfer or candidate. |

Replay processes events by `(at_ms, id)`, checks available balance, and conserves paise across all accounts. A simulated hold blocks only outbound transfers at or after its effective time. It compares baseline, hold now, and hold after 30 minutes. The reported difference is **counterfactual synthetic cash-out**, never real recovery or a prediction of actual outcomes.

## Demo script and participant checklist

1. Personally start Docker, API, and UI. Seed FalkorDB and explain the two graph representations.
2. Open **Split**, click `A01`, trace exact evidence IDs to `A05`, and compare ranked candidates. Explain that a blocked upstream transfer can also starve a later cash-out under this fixture.
3. Move the time slider. Open **Shared device** and explain why the association does not support a hold.
4. Open **Delayed**, compare `RELAY_C` hold now with +30 minutes. Record a human review recommendation and show its audit history.
5. Run tests and the frontend build yourself; record the terminal output in the demo video. Inspect the Cypher in `backend/graph.py` and state the limitations below in your own words.

## Limitations and AI-use disclosure

The dataset is tiny, fictional, and hand designed. It is not a fraud classifier or calibrated risk model. Candidate ranking uses simulated cash-out difference, which can overstate causal impact when all seed events are assumed fixed. The frontend has no authentication; run locally only. Review records are append-only in the app but SQLite has no tamper-resistant audit mechanism. The optional LLM phrasing described in the original brief is omitted; deterministic text works without an API key. This repository does not claim a deployed service, real recovery, or competition outcome.

## Submission checklist

- [ ] Start FalkorDB and verify a live seed and graph-derived case response.
- [ ] Run Python tests and `npm run build` on a machine with dependencies.
- [ ] Verify review persistence and record the demo video.
- [ ] Review source, citations, synthetic labels, and limitations.
- [ ] Submit repository and video according to the event rules.
