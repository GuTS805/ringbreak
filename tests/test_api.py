"""API tests with stubbed graph tools and review store: validation and append-only history."""
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend import agent, app as api
from test_agent import stub_tools


class ApiTests(unittest.TestCase):
    def setUp(self):
        saved = []

        def store(g, case_id, account, action, rationale, evidence_ids, created_utc):
            saved.append({"id": f"R{len(saved)}", "case_id": case_id, "account": account, "action": action,
                          "rationale": rationale, "evidence_ids": evidence_ids, "created_utc": created_utc})
            return saved[-1]["id"]

        def listing(g, case_id):
            return [r for r in reversed(saved) if r["case_id"] == case_id]

        self.patches = [mock.patch.object(api, "store_review", store),
                        mock.patch.object(api, "list_reviews", listing),
                        mock.patch.object(api, "graph", lambda: None),
                        mock.patch.object(api, "llm_from_env", lambda: None),
                        mock.patch.dict(agent.TOOLS, stub_tools())]
        for p in self.patches:
            p.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_case_report(self):
        r = self.client.get("/cases/split").json()
        self.assertTrue(r["synthetic_demo"])
        self.assertEqual(r["candidates"][0]["evidence_ids"], ["A01", "A03", "A05"])

    def test_review_rules_and_history(self):
        post = lambda **b: self.client.post("/reviews", json={"case_id": "split", "rationale": "Checked path A01 to A05.", **b})
        self.assertEqual(post(action="recommend_hold", account="CASH_A", evidence_ids=["A05"]).status_code, 400)
        self.assertEqual(post(action="recommend_hold", account="HUB", evidence_ids=["Z99"]).status_code, 400)
        self.assertEqual(post(action="recommend_hold", account="HUB", evidence_ids=[]).status_code, 400)
        ok = post(action="recommend_hold", account="HUB", evidence_ids=["A01", "A03", "A05"])
        self.assertEqual(ok.json()["status"], "human_review_recommendation")
        self.assertEqual(post(action="no_hold", evidence_ids=[]).status_code, 200)
        history = self.client.get("/reviews/split").json()
        self.assertEqual([h["action"] for h in history], ["no_hold", "recommend_hold"])

    def test_benign_case_cannot_get_hold(self):
        r = self.client.post("/reviews", json={"case_id": "shared", "action": "recommend_hold", "account": "MERCHANT_B",
                                               "rationale": "Shared kiosk device only.", "evidence_ids": ["B01"]})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
