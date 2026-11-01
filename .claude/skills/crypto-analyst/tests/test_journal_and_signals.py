import unittest

import _path  # noqa: F401
import journal_stats as J
import technicals as TA


def trade(open_, close, pnl, size=0.5):
    return {"open": open_, "close": close, "pnl": pnl, "impact": pnl * size}


class Journal(unittest.TestCase):
    def test_close_date(self):
        self.assertEqual(J.close_date("2025-01-01", "2025-01-09", None), "2025-01-09")
        self.assertEqual(J.close_date("2025-01-01", "", 3), "2025-01-04")
        self.assertEqual(J.close_date("", "", None), "")

    def test_equity_and_breaker(self):
        ts = [trade("2025-01-01", "2025-01-02", 0.10), trade("2025-01-03", "2025-01-04", -0.30),
              trade("2025-01-05", "2025-01-06", -0.10), trade("2025-01-07", "2025-01-08", 0.20),
              trade("2025-02-20", "2025-02-21", 0.10), trade("2025-02-22", "2025-02-23", -0.05)]
        eq = J.equity(ts, breaker=0.15, cooldown=14)
        self.assertEqual(eq["trades"], 6)
        self.assertEqual(eq["worst_losing_streak"], 2)
        self.assertLess(eq["max_dd"], -0.15)
        br = eq["breaker"]
        self.assertEqual(br["skipped"], 2)        # the trades opened on 01-05 and 01-07 fall in the pause
        self.assertAlmostEqual(br["skipped_impact"], -0.05 + 0.10)
        self.assertIsNone(J.equity(ts[:3]))       # too few trades


class SignalGrading(unittest.TestCase):
    def test_independent_firings_and_baseline(self):
        n = 600
        c = [100.0 * 1.001 ** i for i in range(n)]   # a steady uptrend: any day wins
        s = {"t": list(range(n)), "c": c, "h": c, "l": c}
        idx = list(range(60, 500, 5))                   # fires every 5 bars
        g = TA.grade_signal(s, idx, 1, (7, 21), 21, cooldown=5)
        self.assertEqual(g[21]["baseline_win"], 1.0)
        self.assertLess(g["independent"]["n"], g[21]["n"])   # overlapping firings are thinned out
        self.assertAlmostEqual(g["independent"]["p_value"], 1.0)  # winning as often as any day proves nothing
        self.assertNotEqual(g["grade"], "proven")


if __name__ == "__main__":
    unittest.main()
