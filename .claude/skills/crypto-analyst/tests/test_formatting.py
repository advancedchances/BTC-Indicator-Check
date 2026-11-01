import unittest

import _path  # noqa: F401
import formatting as F


class Formats(unittest.TestCase):
    def test_price(self):
        self.assertEqual(F.price(77380.42), "77,380")
        self.assertEqual(F.price(-1000.0), "-1,000")
        self.assertEqual(F.price(999.4), "999.4")
        self.assertEqual(F.price(2.249318), "2.24932")
        self.assertEqual(F.price(0.000012345678), "1.23457e-05")
        self.assertEqual(F.price(2.249318, 3), "2.25")

    def test_pct(self):
        self.assertEqual(F.pct(0.0512), "+5.1%")
        self.assertEqual(F.pct(-0.25), "-25.0%")
        self.assertEqual(F.pct(0.0), "+0.0%")
        self.assertEqual(F.pct(0.01234, 2), "+1.23%")

    def test_rate(self):
        self.assertEqual(F.rate(0.5312), "53%")
        self.assertEqual(F.rate(1.0), "100%")
        self.assertEqual(F.rate(0.005), "0%")   # banker's rounding, as Python formats it

    def test_num_and_signed(self):
        self.assertEqual(F.num(1.234), "1.23")
        self.assertEqual(F.num(7.0, 0), "7")
        self.assertEqual(F.num(-0.26), "-0.26")
        self.assertEqual(F.signed(0.261), "+0.26")
        self.assertEqual(F.signed(-1.0), "-1.00")
        self.assertEqual(F.signed(0.5, 1), "+0.5")

    def test_missing_values(self):
        for fn in (F.price, F.pct, F.rate, F.num, F.signed):
            self.assertEqual(fn(None), "n/a")
            self.assertEqual(fn(float("nan")), "n/a")


if __name__ == "__main__":
    unittest.main()
