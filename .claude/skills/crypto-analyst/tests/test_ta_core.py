import math
import random
import unittest

import _path  # noqa: F401
import ta_core as T


class Indicators(unittest.TestCase):
    def test_sma_and_ema_warm_up(self):
        x = [1, 2, 3, 4, 5]
        self.assertEqual(T.sma(x, 3), [None, None, 2.0, 3.0, 4.0])
        e = T.ema(x, 3)
        self.assertEqual(e[:2], [None, None])
        self.assertAlmostEqual(e[2], 2.0)              # seeded with the simple average, as ta.ema
        self.assertAlmostEqual(e[3], 0.5 * 4 + 0.5 * 2.0)

    def test_rsi_extremes(self):
        up = [float(i) for i in range(1, 40)]
        self.assertEqual(T.rsi(up, 14)[-1], 100.0)
        down = list(reversed(up))
        self.assertAlmostEqual(T.rsi(down, 14)[-1], 0.0)

    def test_macd_of_a_flat_series_is_zero(self):
        line, sig, hist = T.macd([10.0] * 60)
        self.assertAlmostEqual(line[-1], 0.0)
        self.assertAlmostEqual(hist[-1], 0.0)

    def test_rolling_vol(self):
        steady = [100 * 1.01 ** i for i in range(60)]
        self.assertAlmostEqual(T.rolling_vol(steady, 30)[-1], 0.0, places=9)
        rng = random.Random(1)
        c = [100.0]
        for _ in range(3000):
            c.append(c[-1] * math.exp(rng.gauss(0, 0.02)))
        v = T.rolling_vol(c, 2000, 365)[-1]
        self.assertAlmostEqual(v, 0.02 * math.sqrt(365), delta=0.03)

    def test_correlation(self):
        a = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(T.correlation(a, [2 * x for x in a]), 1.0)
        self.assertAlmostEqual(T.correlation(a, [-x for x in a]), -1.0)
        self.assertIsNone(T.correlation(a, [1.0] * 4))


class Performance(unittest.TestCase):
    def test_max_drawdown(self):
        dd, peak, trough = T.max_drawdown([1, 2, 1, 3, 1.5, 2])
        self.assertAlmostEqual(dd, -0.5)
        self.assertEqual((peak, trough), (1, 2))   # first -50% (2 -> 1) wins ties
        self.assertEqual(T.max_drawdown([1, 2, 3])[0], 0.0)

    def test_perf_stats(self):
        s = T.perf_stats([0.01] * 365, periods=365)
        self.assertAlmostEqual(s["total"], 1.01 ** 365 - 1)
        self.assertAlmostEqual(s["cagr"], 1.01 ** 365 - 1)
        self.assertEqual(s["max_dd"], 0.0)
        self.assertIsNone(s["sharpe"])          # no volatility: Sharpe undefined
        s = T.perf_stats([0.02, -0.01] * 200, periods=365)
        self.assertGreater(s["sharpe"], 0)
        self.assertLess(s["max_dd"], 0)

    def test_psr_and_deflation(self):
        p = T.psr(0.05, 1000)
        self.assertGreater(p, 0.9)
        self.assertLess(T.psr(0.05, 1000, skew=-1.0, kurt=10.0), p)   # negative skew and fat tails widen doubt
        self.assertAlmostEqual(T.psr(0.0, 1000), 0.5)
        self.assertLess(T.deflated_sharpe(0.05, 1000, 0, 3, 50, 0.001), p)
        self.assertEqual(T.expected_max_sharpe(1, 0.001), 0.0)

    def test_sharpe_beat_prob(self):
        rng = random.Random(3)
        a = [rng.gauss(0.001, 0.02) for _ in range(2000)]
        self.assertEqual(T.sharpe_beat_prob([x + 0.002 for x in a], a), 1.0)
        self.assertEqual(T.sharpe_beat_prob([x - 0.002 for x in a], a), 0.0)
        self.assertIsNone(T.sharpe_beat_prob(a[:40], a[:40]))


class Inference(unittest.TestCase):
    def test_binom_sf_matches_brute_force(self):
        for n, p in ((10, 0.5), (25, 0.56), (40, 0.3)):
            for k in range(n + 1):
                brute = sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))
                self.assertAlmostEqual(T.binom_sf(k, n, p), brute, places=10)

    def test_benjamini_hochberg(self):
        # Classic example: with q=0.05 and m=5, p-values 0.01, 0.02 pass (0.02 <= 0.05*2/5), 0.04 doesn't (> 0.03).
        self.assertEqual(T.benjamini_hochberg([0.01, 0.02, 0.04, 0.3, 0.9], 0.05), [True, True, False, False, False])
        # A later p-value under its threshold lifts the earlier ones (step-up rule).
        self.assertEqual(T.benjamini_hochberg([0.03, 0.035, 0.04], 0.05), [True, True, True])
        self.assertEqual(T.benjamini_hochberg([None, 0.001], 0.05), [False, True])

    def test_bootstrap_floor(self):
        blocks = [[1.0, -1.0, 0.5], [0.2], [-0.4, 0.9], [0.3, 0.3]]
        mean = sum(sum(b) for b in blocks) / sum(len(b) for b in blocks)
        f = T.bootstrap_mean_floor(blocks)
        self.assertLess(f, mean)
        self.assertEqual(f, T.bootstrap_mean_floor(blocks))   # seeded: repeatable
        self.assertIsNone(T.bootstrap_mean_floor([[1.0]]))

    def test_non_overlapping(self):
        spans = [(0, 10), (5, 12), (11, 20), (20, 25), (21, 30)]
        self.assertEqual(T.non_overlapping(spans), [0, 2, 4])

    def test_wilson(self):
        self.assertIsNone(T.wilson_lower(0, 0))
        self.assertLess(T.wilson_lower(7, 10), 0.7)


if __name__ == "__main__":
    unittest.main()
