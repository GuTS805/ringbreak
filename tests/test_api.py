"""API tests with stubbed graph tools: review validation and append-only persistence."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend import agent, app as api
from test_agent import stub_tools


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(api, "DB_PATH", Path(self.tmp.name) / "r.sqlite3"),
                        mock.patch.object(api, "graph", lambda: None),
                        mock.patch.object(api, "llm_from_env", lambda: None),
                        mock.patch.dict(agent.TOOLS, stub_tools())]
        for p in self.patches:
            p.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

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
