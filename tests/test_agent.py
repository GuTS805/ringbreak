"""Agent-loop tests. Graph tools are stubbed from the seed fixture so the loop logic
can be checked without a server; tests/test_falkordb_live.py covers the real graph."""
import unittest
from unittest import mock

from backend import agent
from backend.core import TRANSFERS
from backend.graph import time_paths


def rows(case_id):
    return [dict(id=t.id, source=t.source, target=t.target, paise=t.paise, at_ms=t.at_ms,
                 device=t.device, kind=t.kind) for t in TRANSFERS if t.case_id == case_id]


def stub_tools(break_paths=False):
    def paths(g, flagged_id, max_hops=3):
        if break_paths:
            raise RuntimeError("no var-length support")
        tx = next(t for t in TRANSFERS if t.id == flagged_id)
        return time_paths(rows(tx.case_id), flagged_id, max_hops)

    def devices(g, flagged_id):
        dev = next(t.device for t in TRANSFERS if t.id == flagged_id)
        return [{"device": dev, "account": t.source, "tx_id": t.id, "case_id": t.case_id}
                for t in TRANSFERS if t.device == dev]

    return {"case_transfers": lambda g, case_id: rows(case_id),
            "projected_transfers": lambda g, case_id: rows(case_id),
            "ordered_paths": paths, "shared_device": devices,
            "account_group": lambda g, account: {"method": "stub", "component_id": 0, "members": [account]}}


class AgentTests(unittest.TestCase):
    def run_case(self, case_id, llm=None, **kw):
        with mock.patch.dict(agent.TOOLS, stub_tools(**kw)):
            return agent.investigate(None, case_id, llm=llm)

    def test_split_cites_graph_path_and_ranks_candidates(self):
        r = self.run_case("split")
        self.assertEqual(r["path_check"], "cypher_matches_dfs")
        self.assertEqual(r["evidence_ids"], ["A01", "A03", "A05"])
        self.assertEqual({c["account"] for c in r["candidates"]}, {"MULE_A", "HUB"})
        self.assertTrue(agent.verify_explanation(r["recommendation"], {t["id"] for t in r["transfers"]}, r["evidence_ids"]))
        self.assertEqual(r["phrasing"], "deterministic")

    def test_benign_shared_device_is_not_convicted(self):
        r = self.run_case("shared")
        self.assertGreater(len({d["account"] for d in r["shared_device"]}), 1)
        self.assertEqual(r["candidates"], [])
        self.assertIn("Do not recommend", r["recommendation"])

    def test_no_evidence_case(self):
        r = self.run_case("empty")
        self.assertEqual((r["paths"], r["candidates"], r["evidence_ids"]), ([], [], []))

    def test_delayed_hold_now_vs_30m(self):
        top = self.run_case("delayed")["candidates"][0]
        self.assertEqual((top["account"], top["simulated_difference_now"], top["simulated_difference_30m"]),
                         ("RELAY_C", 28_000, 0))

    def test_trace_only_uses_allowlisted_tools(self):
        r = self.run_case("split")
        self.assertTrue({s["tool"] for s in r["trace"]} <= set(agent.TOOLS) | {"replay_compare"})
        with self.assertRaises(agent.InvestigationError):
            agent.call([], None, "drop_graph")

    def test_path_fallback_is_reported(self):
        r = self.run_case("split", break_paths=True)
        self.assertEqual(r["path_check"], "dfs_only")
        self.assertEqual(r["evidence_ids"], ["A01", "A03", "A05"])

    def test_llm_output_is_verified(self):
        good = lambda text: "Simulated hold for human review: path A01, A03, A05 leads to cash-out."
        invented = lambda text: "Simulated hold: A01, A03, A05 and also Z99."
        down = lambda text: None
        self.assertEqual(self.run_case("split", llm=good)["phrasing"], "llm_verified")
        for llm in (invented, down):
            r = self.run_case("split", llm=llm)
            self.assertEqual(r["phrasing"], "deterministic (llm output rejected)")
            self.assertNotIn("Z99", r["recommendation"])

    def test_no_llm_configured(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(agent.llm_from_env())


if __name__ == "__main__":
    unittest.main()
