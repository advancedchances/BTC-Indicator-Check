import unittest

import _path  # noqa: F401
import strategy_lab as L

DAY = 86400


def series(closes, t0=1_600_000_000 - 1_600_000_000 % DAY):
    return {"t": [t0 + i * DAY for i in range(len(closes))], "o": list(closes), "h": list(closes),
            "l": list(closes), "c": list(closes), "v": [1.0] * len(closes)}


class Engine(unittest.TestCase):
    def test_hold_compounds_returns_less_entry_cost(self):
        rets = [[0.01], [0.02], [-0.01], [0.03]]
        out, turn, expo, ent = L.simulate(rets, [[1.0]] * 4, cost=0.001, start=1)
        self.assertEqual(out[0], 0.0)
        self.assertAlmostEqual(out[1], 0.02 - 0.001)
        self.assertAlmostEqual(out[2], -0.01)
        self.assertEqual(ent, 1)

    def test_targets_trade_at_the_next_bar(self):
        rets = [[0.0], [0.5], [0.0]]
        out, *_ = L.simulate(rets, [[0.0], [1.0], [1.0]], cost=0.0, start=1)
        self.assertEqual(out[1], 0.0)   # the target set on bar 1's close can't earn bar 1's return

    def test_band_suppresses_small_trades_but_not_exits(self):
        rets = [[0.0]] * 4
        out, turn, *_ = L.simulate(rets, [[0.5], [0.55], [0.0], [0.0]], cost=0.01, start=1, band=0.1)
        self.assertAlmostEqual(turn[1], 0.5)
        self.assertEqual(turn[2], 0.0)     # 0.5 -> 0.55 is inside the band
        self.assertAlmostEqual(turn[3], 0.5)  # going flat always trades

    def test_breaker_goes_flat_then_resumes(self):
        rets = [[0.0], [-0.3], [0.1], [0.1], [0.1], [0.1]]
        out, turn, expo, ent = L.simulate(rets, [[1.0]] * 6, cost=0.0, start=1, breaker=0.2, cooldown=2)
        self.assertEqual(expo[2], 0.0)
        self.assertEqual(expo[3], 0.0)
        self.assertEqual(expo[4], 1.0)
        self.assertEqual(ent, 2)

    def test_no_look_ahead_in_signals(self):
        closes = [100.0] * 300 + [200.0] + [200.0] * 5
        s = series(closes)
        w = L.sig_sma(s, n=50)
        self.assertEqual(w[299], 0.0)
        self.assertEqual(w[300], 1.0)        # known only at bar 300's close...
        rets = [[r] for r in L.bar_returns(s)]
        out, *_ = L.simulate(rets, [[x] for x in w], cost=0.0, start=1)
        self.assertEqual(sum(out[:301]), 0.0)   # ...so the jump into bar 300 isn't captured


class Signals(unittest.TestCase):
    def test_bar_returns_open_to_open(self):
        s = series([100, 110, 121])
        s["o"] = [100, 110, 121]
        s["c"] = [105, 115, 130]
        self.assertEqual([round(x, 6) for x in L.bar_returns(s)], [0.1, 0.1, round(130 / 121 - 1, 6)])

    def test_tsmom_is_a_share_of_lookbacks(self):
        s = series([100.0 + i for i in range(300)])
        self.assertEqual(L.sig_tsmom(s)[-1], 1.0)
        self.assertEqual(L.sig_tsmom(s)[100], 0.0)  # not warm yet

    def test_week_ends(self):
        t0 = 1_700_438_400  # Monday 2023-11-20 00:00 UTC
        t = [t0 + i * DAY for i in range(10)]
        self.assertEqual([i for i, e in enumerate(L.week_ends(t)) if e], [6, 9])

    def test_xsmom_picks_the_strongest_positive(self):
        view = {}
        for a, drift in (("A", 1.02), ("B", 0.99), ("C", 1.01)):
            s = series([100 * drift ** i for i in range(40)])
            view[a] = {"s": s, "idx": list(range(40))}
        w = L.bas_xsmom(view, 39, ["A", "B", "C"], n=28, k=2)
        self.assertEqual(set(w), {"A", "C"})
        self.assertAlmostEqual(sum(w.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
