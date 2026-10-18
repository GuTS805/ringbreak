# RINGBREAK

[![ci](https://github.com/GuTS805/ringbreak/actions/workflows/ci.yml/badge.svg)](https://github.com/GuTS805/ringbreak/actions/workflows/ci.yml)

**Live demo: https://ringbreak.vercel.app** (console on Vercel, API at https://ringbreak-api.vercel.app/docs, graph on FalkorDB Cloud)

**Graph-first investigation of fictional UPI mule networks, with a time-aware "what if we held this account?" simulator.**

A flagged payment is rarely the whole story. Money gets split across mule accounts, comes back together at a hub, and is cashed out within minutes. RINGBREAK takes one flagged transaction and uses FalkorDB to:

1. traverse the **time-ordered** paths that money actually took after it,
2. collect device and account-group context **without** treating association as guilt,
3. replay the events with balance checks to compare **hold now** vs **hold 30 minutes later** for each candidate account, and
4. write a cited recommendation that a **human reviewer** accepts, rejects or escalates, kept in an audit log.

Everything is a **synthetic demo**. Accounts, devices and amounts are fictional. A "hold" is a simulated block on outbound transfers in a replay, never a bank action, and every difference reported is **simulated cash-out**, not recovered money. Money is stored in paise and time in UTC milliseconds.

## Run it

Requires Python 3.12+, Node.js 20+, and a FalkorDB server: either local Docker or a free FalkorDB Cloud instance.

**1. FalkorDB**

```powershell
docker compose up -d          # graph on :6379, FalkorDB Browser on http://localhost:3001
```

No Docker? Create a free instance at [FalkorDB Cloud](https://app.falkordb.cloud), then put its host, port, username and password in `.env` (step 2).

**2. API** (from the repo root)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env           # macOS/Linux: cp .env.example .env
uvicorn backend.app:app --reload --port 8000
```

**3. Console** (second terminal)

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev
```

Open http://localhost:3000 and click **Seed FalkorDB**. The console proxies `/api/*` to the API (`API_INTERNAL_URL`, default `http://127.0.0.1:8000`), so a single URL serves everything. To share a public demo, run `npm run build; npm run start` and expose port 3000 only, for example with `cloudflared tunnel --url http://localhost:3000`. The seed response includes node and edge counts read back from the graph. Then pick a case. API docs are at http://localhost:8000/docs.

**Tests**

```powershell
python -m unittest discover -s tests -v
cd frontend; npm run build
```

`tests/test_falkordb_live.py` runs against a real FalkorDB, using a separate `ringbreak_test` graph, and **skips** when none is reachable. Run the suite with FalkorDB up to exercise the actual Cypher. CI (`.github/workflows/ci.yml`) runs all tests against a FalkorDB service container, seeds the graph through the API, checks that the Cypher paths match the verifier and that `algo.WCC` is used, and builds the console.

## Deploying to Vercel

Two Vercel projects deploy from this repo:

| Project | Root directory | Notes |
| --- | --- | --- |
| API | repo root | `main.py` exposes the FastAPI `app`. Set `FALKOR_HOST`, `FALKOR_PORT`, `FALKOR_USERNAME`, `FALKOR_PASSWORD` to a FalkorDB Cloud instance. |
| Console | `frontend` | Set `API_INTERNAL_URL` to the API's URL. The console proxies `/api/*` to it. |

Reviews are stored in the graph, so nothing depends on a local disk. After the first deploy, click **Seed FalkorDB** once.

## Graph model

```
(:Account)-[:SENT]->(:Transaction)-[:RECEIVED_BY]->(:Account)    canonical event
(:Transaction)-[:VIA]->(:Device)                                 device used
(:Account)-[:TRANSFER {tx_id, at_ms, paise, kind, case_id}]->(:Account)   projection for traversal
(:Case {id, title, flagged})
```

Every transaction is written both ways in a single `MERGE` statement keyed on stable IDs, so seeding again changes nothing. Each investigation reads both views and **refuses** the case if their transaction IDs differ.

## How an investigation runs

`backend/agent.py` runs a fixed plan. Each step calls one tool from an **allowlist** and is recorded in a trace that the console shows together with the Cypher it ran.

| Step | Tool | What it reads from FalkorDB |
| --- | --- | --- |
| 1 | `case_transfers` | Event records for the case (≤ 80 rows) |
| 2 | `projected_transfers` | `TRANSFER` projection, cross-checked with step 1 |
| 3 | `ordered_paths` | Variable-length traversal where every hop is later than the previous one (≤ 3 transfers, ≤ 100 paths) |
| 4 | `shared_device` | Other transactions on the flagged transaction's device (≤ 20) |
| 5 | `account_group` | `algo.WCC` component of the receiving account over `TRANSFER` edges |
| 6 | `replay_compare` | Local deterministic replay: baseline vs hold now vs hold +30 min |

Safeguards:

- **Paths are double-checked.** The Cypher traversal is compared with an independent bounded DFS over the projection rows returned by the graph. The trace reports `cypher_matches_dfs`, or the fallback used. Fixtures are never used as evidence.
- **Association is not evidence.** Candidates come only from intermediate accounts on a time-ordered path that ends in a cash-out. Shared devices and account groups give context only. The API rejects a hold recommendation for any account that is not a ranked candidate.
- **Every citation is grounded.** The explanation is checked so that each transaction ID it mentions exists in the graph rows and it cites all path evidence. If an optional LLM is configured (`LLM_*` in `.env`, any OpenAI-compatible endpoint), its rephrasing must pass the same check or it is discarded. Without a key, the deterministic text is used.
- **Every query is bounded.** All queries are parameterised, read-only (`ro_query`), limited in rows and given a 3-second timeout.

### Key Cypher

All statements are in [`backend/graph.py`](backend/graph.py) and appear in the console's agent trace.

Time-ordered traversal:

```cypher
MATCH (:Account)-[:SENT]->(f:Transaction {id:$flagged})-[:RECEIVED_BY]->(start:Account)
MATCH p=(start)-[:TRANSFER*1..2]->(:Account)
WITH f, p, relationships(p) AS rs
WHERE all(r IN rs WHERE r.case_id = f.case_id AND r.at_ms > f.at_ms)
  AND all(i IN range(0, size(rs) - 2) WHERE rs[i].at_ms < rs[i + 1].at_ms)
RETURN [r IN rs | r.tx_id], [n IN nodes(p) | n.id], [r IN rs | r.kind], rs[size(rs) - 1].at_ms
LIMIT $limit
```

Shared-device evidence:

```cypher
MATCH (f:Transaction {id:$flagged})-[:VIA]->(dev:Device)<-[:VIA]-(t:Transaction)<-[:SENT]-(a:Account)
RETURN dev.id, a.id, t.id, t.case_id
ORDER BY t.at_ms, t.id LIMIT $limit
```

Account grouping ([FalkorDB WCC](https://docs.falkordb.com/algorithms/wcc.html)):

```cypher
CALL algo.WCC({nodeLabels: ['Account'], relationshipTypes: ['TRANSFER']})
YIELD node, componentId
WITH componentId, collect(node.id) AS members
WHERE $account IN members
RETURN componentId, members
```

If the server build lacks `algo.WCC`, a bounded undirected `TRANSFER*1..4` match is used instead, and the trace says so.

## Seeded cases and expected results

| Case | Flagged | What should happen |
| --- | --- | --- |
| Split → converge → cash-out | A01 | Path `A01 → A03 → A05` (VICTIM_A → MULE_A → HUB → CASH_A). `A04` (MULE_B → HUB) is not downstream of A01, so it is not cited. Candidates: HUB and MULE_A. Both give the same hold-now difference (₹450), because holding MULE_A starves HUB of the balance A05 needs. |
| Shared device, benign merchant | B01 | B01 and B02 use the same kiosk, and there is no forwarding or cash-out. **No candidate, no hold.** This is the false-positive trap. |
| Delayed forwarding | C01 | RELAY_C forwards at minute 20. Hold now blocks C02 (simulated difference ₹280). Hold +30 min is too late (₹0). |
| No evidence | D01 | No downstream path. No candidate. |

Replay processes events in `(at_ms, id)` order, rejects any transfer above the available balance, asserts non-negative balances, and checks that total paise are conserved.

## Demo script (≈ 3 minutes)

1. **Setup (20 s):** show `docker compose up`, the API and the console. Click **Seed FalkorDB** and point out the node and edge counts read back from the graph.
2. **Split case (60 s):** the red path is the cited evidence. Open the agent trace and expand `ordered_paths` to show the real Cypher and `cypher_matches_dfs`. Click HUB to open the account drawer, then compare HUB and MULE_A.
3. **Shared-device trap (30 s):** the same kiosk links two customers, yet RINGBREAK refuses a hold. Explain why association alone is not evidence.
4. **Delayed case (40 s):** hold now saves ₹280 of simulated cash-out, and +30 minutes saves nothing. Drag the time slider to show C02 arriving at minute 20. Record **Recommend review of hold** and show the audit history.
5. **Close (30 s):** run the tests, open `backend/graph.py`, and state the limitations below.

## Limitations

- The dataset is tiny, fictional and hand-designed. This is not a fraud classifier or a calibrated risk model.
- Candidate ranking uses simulated cash-out difference, which can overstate impact because all other events are assumed to happen as seeded. In the split case, holding MULE_A also starves HUB of the balance needed for A05.
- Ordering is strict: two transfers with the same timestamp are not treated as a path.
- The console has no authentication, so treat a public deployment as a demo. Reviews are stored in FalkorDB as `(:Review)-[:CITES]->(:Transaction)` and are append-only in the app, but the store is not tamper-resistant.
- Nothing here is deployed, and no real funds are recovered.

## Submission checklist

- [ ] `docker compose up -d` (or FalkorDB Cloud), seed, and confirm the live tests pass rather than skip.
- [ ] `python -m unittest discover -s tests -v` and `npm run build` pass on your machine.
- [ ] Walk through all four cases, record a review, and record the demo video.
- [ ] Submit the repository and video according to the event rules.
