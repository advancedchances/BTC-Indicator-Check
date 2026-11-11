import unittest

import _path  # noqa: F401
import derivatives as D


def day(i, close=100.0, spot_buy=50.0, perp_buy=500.0, oi=1000.0, acct=1.5):
    return {"date": f"d{i:03d}", "close": close, "spot_vol": 100.0, "spot_buy": spot_buy,
            "perp_vol": 1000.0, "perp_buy": perp_buy, "oi": oi, "top_ls": 1.2, "acct_ls": acct}


class FlowRead(unittest.TestCase):
    def test_balanced_flow_has_no_vote(self):
        r = D.flow_read([day(i) for i in range(40)])
        self.assertEqual(r["spot_net"], 0)
        self.assertEqual(r["perp_net"], 0)
        self.assertEqual(r["read"]["vote"], 0)

    def test_spot_bought_perps_sold_with_rising_oi(self):
        rows = [day(i) for i in range(33)]
        rows += [day(33 + k, close=100 - 2 * (k + 1), spot_buy=60, perp_buy=450, oi=1000 + 10 * (k + 1), acct=3.0)
                 for k in range(7)]
        r = D.flow_read(rows)
        self.assertAlmostEqual(r["spot_net"], 7 * 20)
        self.assertAlmostEqual(r["perp_net"], 7 * -100)
        self.assertEqual(r["spot_buy_days"], 7)
        self.assertEqual(r["perp_sell_days"], 7)
        self.assertGreaterEqual(r["divergence_percentile"], 80)
        self.assertIn("spot bought while perps sold", r["read"]["label"])
        self.assertIn("open interest rising as price falls", r["read"]["label"])
        self.assertIn("most net long", r["read"]["label"])
        self.assertAlmostEqual(r["oi_change"], 0.07)

    def test_needs_history(self):
        with self.assertRaises(ValueError):
            D.flow_read([day(i) for i in range(10)])


if __name__ == "__main__":
    unittest.main()
