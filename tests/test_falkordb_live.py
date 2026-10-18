"""Integration tests against a real FalkorDB. Skipped when no server is reachable.

Uses a separate graph name so the demo graph is untouched."""
import os
import unittest

from backend import agent, graph as G

G.GRAPH_NAME = os.getenv("FALKOR_TEST_GRAPH", "ringbreak_test")  # never the demo graph


def connect():
    try:
        g = G.graph()
        g.ro_query("RETURN 1", timeout=2000)
        return g
    except Exception:
        return None


GRAPH = connect()


@unittest.skipIf(GRAPH is None, "FalkorDB not reachable; set FALKOR_HOST/FALKOR_PORT")
class LiveGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            GRAPH.delete()
        except Exception:
            pass
        cls.first = G.seed(GRAPH)

    def test_seed_is_idempotent(self):
        again = G.seed(GRAPH)
        self.assertEqual(self.first["graph_counts"], again["graph_counts"])
        self.assertEqual(again["graph_counts"]["edges"]["TRANSFER"], again["transfers"])

    def test_event_and_projection_agree(self):
        for case_id in ("split", "shared", "delayed", "empty"):
            events = G.case_transfers(GRAPH, case_id)
            projection = G.projected_transfers(GRAPH, case_id)
            self.assertEqual([(e["id"], e["source"], e["target"], e["paise"], e["at_ms"]) for e in events],
                             [(p["id"], p["source"], p["target"], p["paise"], p["at_ms"]) for p in projection])

    def test_cypher_paths_match_dfs(self):
        paths = G.ordered_paths(GRAPH, "A01")
        self.assertIn(["A01", "A03", "A05"], [p["tx_ids"] for p in paths])
        self.assertNotIn("A04", {i for p in paths for i in p["tx_ids"]})
        verifier = G.time_paths(G.projected_transfers(GRAPH, "split"), "A01")
        self.assertEqual(sorted(p["tx_ids"] for p in paths), sorted(p["tx_ids"] for p in verifier))

    def test_bounds_are_enforced(self):
        with self.assertRaises(ValueError):
            G.case_transfers(GRAPH, "split", limit=1000)
        with self.assertRaises(ValueError):
            G.ordered_paths(GRAPH, "A01", max_hops=9)
        self.assertLessEqual(len(G.shared_device(GRAPH, "B01")), 20)

    def test_account_group(self):
        group = G.account_group(GRAPH, "HUB")
        self.assertTrue({"MULE_A", "MULE_B", "CASH_A"} <= set(group["members"]))
        self.assertNotIn("MERCHANT_B", group["members"])

    def test_full_investigation(self):
        r = agent.investigate(GRAPH, "split")
        self.assertEqual(r["path_check"], "cypher_matches_dfs")
        self.assertEqual(r["evidence_ids"], ["A01", "A03", "A05"])
        self.assertEqual(agent.investigate(GRAPH, "shared")["candidates"], [])


if __name__ == "__main__":
    unittest.main()
