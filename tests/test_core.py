import unittest
from backend.core import CASES, INITIAL, MINUTE, TRANSFERS, compare, replay
from backend.graph import time_paths


class CoreTests(unittest.TestCase):
    def test_unique_ids_and_valid_projection(self):
        self.assertEqual(len({t.id for t in TRANSFERS}), len(TRANSFERS))
        for t in TRANSFERS:
            self.assertIn(t.source, INITIAL)
            self.assertIn(t.target, INITIAL)
            self.assertGreater(t.paise, 0)
            self.assertEqual(t.at_ms % MINUTE, 0)

    def test_conservation_and_nonnegative(self):
        for case_id in CASES:
            baseline = replay(case_id)
            self.assertEqual(sum(baseline['balances'].values()), sum(INITIAL.values()))
            self.assertGreaterEqual(min(baseline['balances'].values()), 0)
            self.assertEqual(baseline, replay(case_id))

    def test_delayed_hold(self):
        flagged = next(t for t in TRANSFERS if t.id == 'C01')
        c = compare('delayed', 'RELAY_C', flagged.at_ms + 1)
        self.assertEqual(c['simulated_difference_now'], 28_000)
        self.assertEqual(c['simulated_difference_30m'], 0)
        self.assertEqual(c['blocked_now'][0]['id'], 'C02')

    def test_benign_and_no_evidence(self):
        self.assertEqual(replay('shared')['cashout_paise'], 0)
        self.assertEqual(replay('empty')['cashout_paise'], 0)
        rows = [dict(id=t.id, source=t.source, target=t.target, at_ms=t.at_ms, kind=t.kind)
                for t in TRANSFERS if t.case_id == 'shared']
        self.assertFalse(any(p['cashout'] for p in time_paths(rows, 'B01')))

    def test_time_ordered_path(self):
        rows = [dict(id=t.id, source=t.source, target=t.target, at_ms=t.at_ms, kind=t.kind)
                for t in TRANSFERS if t.case_id == 'split']
        paths = time_paths(rows, 'A01')
        self.assertTrue(any(p['tx_ids'] == ['A01', 'A03', 'A05'] for p in paths))
        self.assertFalse(any('A04' in p['tx_ids'] for p in paths))
        with self.assertRaises(ValueError):
            time_paths(rows, 'A01', 99)


if __name__ == '__main__':
    unittest.main()
