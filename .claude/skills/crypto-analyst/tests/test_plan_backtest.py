import unittest

import _path  # noqa: F401
import plan_backtest as B


def plan(side="long", e1=100.0, e2=None, stop=95.0, targets=(105.0, 110.0, 115.0)):
    avg = (e1 + e2) / 2 if e2 else e1
    return {"side": side, "entry_1": {"mid": e1}, "entry_2": {"mid": e2} if e2 else None, "avg_entry": avg,
            "stop": stop, "targets": [{"price": t} for t in targets]}


class Simulate(unittest.TestCase):
    def test_no_fill(self):
        bars = [(102, 103, 101, 102)] * 12
        r = B.simulate(plan(), bars, wait=10, max_hold=20)
        self.assertFalse(r["filled"])
        self.assertEqual(r["exit"], "no fill")

    def test_gap_through_the_stop_fills_at_the_open(self):
        bars = [(101, 101, 99, 100), (90, 91, 88, 89)]  # fills at 100, then opens below the 95 stop
        r = B.simulate(plan(), bars, 10, 20)
        self.assertEqual(r["exit"], "stop")
        self.assertAlmostEqual(r["r"], (90 - 100) / 5)

    def test_stop_checked_on_the_fill_bar_and_no_exits_there(self):
        bars = [(101, 120, 94, 100)]  # touches entry, stop and every target on one bar: the stop wins
        r = B.simulate(plan(), bars, 10, 20)
        self.assertEqual(r["exit"], "stop")
        bars = [(101, 120, 99, 100), (100, 101, 99.5, 100)]  # fill bar reaches the targets but they aren't taken
        r = B.simulate(plan(), bars, 10, 1)
        self.assertEqual(r["exit"], "time stop")

    def test_exits_in_thirds_then_breakeven_stop(self):
        bars = [(101, 101, 99, 100), (100, 106, 100, 105), (104, 104, 99, 100)]
        r = B.simulate(plan(), bars, 10, 20)
        self.assertEqual(r["exit"], "stop after exit")
        self.assertAlmostEqual(r["r"], (5 / 3) / 5)  # a third out at +5, the rest at breakeven

    def test_costs_reduce_r(self):
        bars = [(101, 101, 99, 100), (100, 116, 100, 115)]
        gross = B.simulate(plan(), bars, 10, 20)
        net = B.simulate(plan(), bars, 10, 20, fee=0.001, slip=0.0005)
        self.assertAlmostEqual(gross["r"], gross["r_gross"])
        self.assertAlmostEqual(net["r_gross"], gross["r"])
        fees = 0.001 * (100 + 105 / 3 + 110 / 3 + 115 / 3)
        self.assertAlmostEqual(net["r"], gross["r"] - fees / 5)

    def test_short_side_and_path(self):
        bars = [(99, 101, 98, 100), (100, 100, 94, 95)]
        r = B.simulate(plan("short", stop=105, targets=(95.0,)), bars, 10, 20)
        self.assertEqual(r["exit"], "all exits")
        self.assertAlmostEqual(r["r"], 1.0)
        self.assertAlmostEqual(r["path"][-1], r["r"])
        self.assertEqual(len(r["path"]), 2)


class Statistics(unittest.TestCase):
    def rows(self):
        def row(sym, snap, entry, exit_, r, date):
            return {"symbol": sym, "horizon": "swing", "side": "long", "filled": True, "snap_t": snap,
                    "entry_t": entry, "exit_t": exit_, "end_t": exit_, "r": r, "r_gross": r, "date": date,
                    "primary": True, "counter_trend": False, "grade": "A", "path": [r]}
        return [row("BTC", 0, 1, 10, 1.0, "2020-01-01"), row("BTC", 7, 8, 12, -1.0, "2020-01-08"),
                row("BTC", 14, 15, 20, 0.5, "2020-01-15"), row("ETH", 7, 8, 9, 2.0, "2020-01-08")]

    def test_separate_drops_overlapping_trades(self):
        sep = B.separate(self.rows())
        self.assertEqual(sorted((r["symbol"], r["entry_t"]) for r in sep), [("BTC", 1), ("BTC", 15), ("ETH", 8)])

    def test_split_purges_trades_that_cross_the_date(self):
        rows = self.rows()
        early, late = B.split(rows, "1970-01-01")
        self.assertEqual(early, [])
        self.assertEqual(len(late), 4)

    def test_cell_stats(self):
        s = B.cell_stats(self.rows())
        self.assertEqual(s["filled"], 4)
        self.assertEqual(s["separate"], 3)
        self.assertAlmostEqual(s["avg_r"], 2.5 / 4)


if __name__ == "__main__":
    unittest.main()
