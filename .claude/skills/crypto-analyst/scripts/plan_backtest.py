#!/usr/bin/env python3
"""
Walk-forward test of trade_plan.py: replay the plan on past dates, using
only the data available on each date, then trade it on the bars that
followed. It answers whether a setup grade (A++, A, B, C) and the bias mean
anything in R. Standard library only.

Usage:
  python plan_backtest.py BTC ETH SOL                  # weekly snapshots, both horizons, both sides
  python plan_backtest.py BTC --step 14 --start 2019-01-01
  python plan_backtest.py BTC --csv trades.csv         # also write every simulated trade
  python plan_backtest.py BTC ETH SOL --json
  python plan_backtest.py BTC ETH SOL --from-csv trades.csv --calibration ../references/plan_calibration.json

What is replayed: the chart side of the plan only. Funding, open interest,
whale positions, options and the liquidation map have no free history, so
the positioning and liquidity votes are empty and those levels are absent.
The macro backdrop is skipped too. Everything else (levels, zones, evidence,
signal records, seasonality, BTC cycle gauges) is recomputed from history up
to each snapshot, so nothing from the future leaks in.

How each plan is traded:
  - Entry 1 and Entry 2 are limit orders at each zone's midpoint (half size
    each, or full size when there is one entry). They stay open for 10 bars
    (swing) or 30 bars (position) and are cancelled once exit 1 is hit. A bar
    that gaps through a limit fills at its open.
  - The stop is checked before any exit on the same bar (the conservative
    reading of a daily bar), and on the bar that fills an entry exits are not
    taken.
  - Exits close equal parts of the position (thirds with three exits). After
    the first, the stop moves to the average fill price.
  - Time stop: close at the bar's close after twice the plan's expected hold.
    Anything still open when history runs out is marked to the last close.
  - R = profit per unit / (planned average entry - stop), scaled by the share
    of the planned position that filled, so a half-filled trade counts half.

Results are in-sample for the method's design choices and out-of-sample for
the data at each date. Fees, funding and slippage are not included.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
import ta_core as T  # noqa: E402
import technicals as TA  # noqa: E402
import trade_plan as P  # noqa: E402
from market_data import load_candles  # noqa: E402

WAIT = {"swing": 10, "position": 30}
MIN_HISTORY = 400
DAY = 86400


def simulate(p, bars, wait, max_hold):
    """Trade one plan side over the following bars. bars: list of (o, h, l, c)."""
    long = p["side"] == "long"
    e1 = p["entry_1"]["mid"]
    e2 = p["entry_2"]["mid"] if p["entry_2"] else None
    weights = {"e1": 0.5 if e2 else 1.0, "e2": 0.5 if e2 else 0.0}
    stop = p["stop"]
    targets = [t["price"] for t in p["targets"]][:3]
    unit_risk = abs(p["avg_entry"] - stop)
    if not targets or unit_risk <= 0:
        return {"filled": False, "r": 0.0, "exit": "no plan"}
    share = 1.0 / len(targets)
    fills = {}
    open_frac, pnl, hit, stop_cur, first = 0.0, 0.0, 0, stop, None
    for k, (o, h, l, c) in enumerate(bars):
        fill_bar = False
        if hit == 0:
            for name, px in (("e1", e1), ("e2", e2)):
                if px is None or name in fills or (first is None and k >= wait):
                    continue
                touched = l <= px if long else h >= px
                if touched:
                    price = min(o, px) if long else max(o, px)
                    fills[name] = price
                    open_frac += weights[name]
                    fill_bar = True
                    if first is None:
                        first = k
        if first is None:
            if k >= wait:
                return {"filled": False, "r": 0.0, "exit": "no fill"}
            continue
        filled_frac = sum(weights[n] for n in fills)
        avg_fill = sum(fills[n] * weights[n] for n in fills) / filled_frac
        stopped = l <= stop_cur if long else h >= stop_cur
        if stopped:
            px = min(o, stop_cur) if long else max(o, stop_cur)
            pnl += open_frac * ((px - avg_fill) if long else (avg_fill - px))
            return result(pnl, unit_risk, filled_frac, "stop" if hit == 0 else "stop after exit", k - first)
        if not fill_bar:
            while hit < len(targets) and (h >= targets[hit] if long else l <= targets[hit]):
                part = min(open_frac, share * filled_frac)
                pnl += part * ((targets[hit] - avg_fill) if long else (avg_fill - targets[hit]))
                open_frac -= part
                hit += 1
                stop_cur = avg_fill
            if hit == len(targets) or open_frac <= 1e-9:
                return result(pnl, unit_risk, filled_frac, "all exits", k - first)
        if k - first >= max_hold:
            pnl += open_frac * ((c - avg_fill) if long else (avg_fill - c))
            return result(pnl, unit_risk, filled_frac, "time stop", k - first)
    if first is None:
        return {"filled": False, "r": 0.0, "exit": "no fill"}
    c = bars[-1][3]
    filled_frac = sum(weights[n] for n in fills)
    avg_fill = sum(fills[n] * weights[n] for n in fills) / filled_frac
    pnl += open_frac * ((c - avg_fill) if long else (avg_fill - c))
    return result(pnl, unit_risk, filled_frac, "open at end", len(bars) - 1 - first)


def result(pnl, unit_risk, filled_frac, how, bars_held):
    return {"filled": True, "r": pnl / unit_risk, "fill_share": filled_frac, "exit": how, "bars": bars_held}


def snapshot_indexes(t, step, start):
    """Indexes of bars that close on a fixed weekly grid (UTC Sundays when step is a multiple of 7)."""
    out = []
    for i, ts in enumerate(t):
        if i < MIN_HISTORY or ts < start:
            continue
        day = int(ts // DAY)
        if (day - 3) % step == 0:  # epoch day 3 (1970-01-04) was a Sunday
            out.append(i)
    return out


def run(symbols, step, start, end=None):
    rows, btc_ctx = [], {}
    order = sorted(symbols, key=lambda s: s != "BTC")  # BTC first, so alts can use its stance
    for sym in order:
        d, src = load_candles(sym, "1d", 4000)
        if d["t"][-1] + DAY > time.time():  # drop the bar still forming
            d = T.slice_series(d, 0, -1)
        idx = snapshot_indexes(d["t"], step, start)
        if end:
            idx = [i for i in idx if d["t"][i] < end]
        t0 = time.time()
        for n, i in enumerate(idx):
            TA.NOW = d["t"][i] + DAY
            sl = T.slice_series(d, 0, i + 1)
            data = {"symbol": sym, "source": src, "daily": sl, "hourly": None, "live": sl["c"][-1],
                    "live_t": sl["t"][-1], "forming": False}
            try:
                a = TA.analyze(sym, data=data)
            except (ValueError, ZeroDivisionError, IndexError, KeyError):
                continue
            date = T.utc(d["t"][i]).strftime("%Y-%m-%d")
            if sym == "BTC":
                btc_ctx[date] = {"30d": a["change"]["30d"], "stance": a["score"]["stance"]}
            ctx = btc_ctx.get(date) if sym != "BTC" else None
            future = list(zip(d["o"][i + 1:], d["h"][i + 1:], d["l"][i + 1:], d["c"][i + 1:]))
            for hz in ("swing", "position"):
                o = P.plan(sym, derivs=False, analysis=a, horizon=hz, btc={"bench": None, "ctx": ctx} if ctx else None)
                for side in ("long", "short"):
                    p = o["plans"].get(side)
                    if not p:
                        continue
                    max_hold = min(180, 2 * p["expected_hold_days"])
                    sim = simulate(p, future, WAIT[hz], max_hold)
                    rows.append({"symbol": sym, "date": date, "horizon": hz, "side": side, "grade": p["grade"],
                                 "grade_points": p["grade_points"], "bias": o["bias"], "bias_score": o["bias_score"],
                                 "primary": side == o["primary"], "counter_trend": p["counter_trend"],
                                 "risk_pct": round(p["risk_pct"], 4), **sim})
            if n and n % 50 == 0:
                print(f"  {sym}: {n}/{len(idx)} snapshots, {time.time() - t0:.0f}s", file=sys.stderr)
        TA.NOW = None
    return rows


def summarize(rows, keys):
    groups = {}
    for r in rows:
        groups.setdefault(tuple(r[k] for k in keys), []).append(r)
    out = []
    for k, rs in groups.items():
        filled = [r for r in rs if r["filled"]]
        rr = [r["r"] for r in filled]
        wins = sum(1 for x in rr if x > 0)
        out.append({**dict(zip(keys, k)), "plans": len(rs), "filled": len(filled),
                    "fill_rate": len(filled) / len(rs) if rs else None,
                    "win_rate": wins / len(rr) if rr else None,
                    "win_floor": T.wilson_lower(wins, len(rr)) if rr else None,
                    "avg_r": sum(rr) / len(rr) if rr else None, "median_r": T.median(rr),
                    "r_per_plan": sum(rr) / len(rs) if rs else None,
                    "total_r": sum(rr)})
    order = {"A++": 0, "A": 1, "B": 2, "C": 3}
    return sorted(out, key=lambda x: tuple(order.get(x.get(k), 9) if k == "grade" else str(x.get(k)) for k in keys))


def calibration(rows, symbols, step):
    """Record per kind of plan (horizon, side, with/against trend, grade), for trade_plan.py to quote."""
    cells = {}
    for r in rows:
        key = f"{r['horizon']}|{r['side']}|{'against' if r['counter_trend'] else 'with'}|{r['grade']}"
        cells.setdefault(key, []).append(r)
    out = {}
    for k, rs in cells.items():
        rr = [x["r"] for x in rs if x["filled"]]
        wins = sum(1 for x in rr if x > 0)
        out[k] = {"plans": len(rs), "fills": len(rr), "win_rate": round(wins / len(rr), 3) if rr else None,
                  "win_floor": round(T.wilson_lower(wins, len(rr)), 3) if rr else None,
                  "avg_r": round(sum(rr) / len(rr), 3) if rr else None,
                  "r_per_plan": round(sum(rr) / len(rs), 3) if rs else None}
    return {"method": "plan_backtest.py walk-forward, chart-only plans, no fees or slippage",
            "symbols": symbols, "step_days": step, "from": min(r["date"] for r in rows),
            "to": max(r["date"] for r in rows), "cells": out}


def load_csv(path):
    rows = list(csv.DictReader(open(path)))
    for r in rows:
        r["filled"] = r["filled"] == "True"
        r["counter_trend"] = r["counter_trend"] == "True"
        r["primary"] = r["primary"] == "True"
        r["r"] = float(r["r"])
    return rows


def fmt(x, kind="r"):
    if x is None:
        return "n/a"
    return f"{x * 100:.0f}%" if kind == "pct" else f"{x:+.2f}"


def table(title, summ, keys):
    L = [f"### {title}", "| " + " | ".join(k.replace("_", " ").capitalize() for k in keys)
         + " | Plans | Filled | Win rate | 90% floor | Avg R | Median R | R per plan | Total R |",
         "|" + "---|" * (len(keys) + 8)]
    for s in summ:
        L.append("| " + " | ".join(str(s[k]) for k in keys) + f" | {s['plans']} | {fmt(s['fill_rate'], 'pct')} "
                 f"| {fmt(s['win_rate'], 'pct')} | {fmt(s['win_floor'], 'pct')} | {fmt(s['avg_r'])} | {fmt(s['median_r'])} "
                 f"| {fmt(s['r_per_plan'])} | {s['total_r']:+.1f} |")
    return L


def render(rows, symbols, step):
    first, last = min(r["date"] for r in rows), max(r["date"] for r in rows)
    L = [f"## Walk-forward test of trade plans: {', '.join(symbols)}",
         f"Snapshots every {step} days from {first} to {last}; chart-only plans (no positioning history); "
         "no fees or slippage. R per plan counts unfilled plans as zero.\n"]
    for title, keys in (("By horizon and grade", ["horizon", "grade"]),
                        ("Primary side only (the side the bias picked), by horizon and grade", ["horizon", "grade"]),
                        ("By horizon and side", ["horizon", "side"]),
                        ("By horizon and bias", ["horizon", "bias"]),
                        ("With or against the trend", ["horizon", "counter_trend"]),
                        ("By coin and horizon", ["symbol", "horizon"])):
        rs = [r for r in rows if r["primary"]] if title.startswith("Primary") else rows
        L += table(title, summarize(rs, keys), keys)
        L.append("")
    ex = {}
    for r in rows:
        if r["filled"]:
            ex[r["exit"]] = ex.get(r["exit"], 0) + 1
    L.append("How filled trades ended: " + ", ".join(f"{k} {v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])))
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--step", type=int, default=7, help="days between snapshots (default 7)")
    p.add_argument("--start", default="2017-01-01", help="first snapshot date, YYYY-MM-DD (default 2017-01-01)")
    p.add_argument("--end", help="last snapshot date, YYYY-MM-DD")
    p.add_argument("--csv", help="write every simulated plan to this CSV")
    p.add_argument("--from-csv", help="re-summarize a CSV written by --csv instead of replaying")
    p.add_argument("--calibration", help="write the per-kind record JSON that trade_plan.py quotes "
                   "(references/plan_calibration.json)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    start = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.timezone.utc).timestamp()
    end = dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.timezone.utc).timestamp() if args.end else None
    symbols = [s.upper() for s in args.symbols]
    rows = load_csv(args.from_csv) if args.from_csv else run(symbols, args.step, start, end)
    if not rows:
        sys.exit("no snapshots: history too short for the chosen start date")
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
            w.writeheader()
            w.writerows(rows)
    if args.calibration:
        with open(args.calibration, "w") as fh:
            json.dump(calibration(rows, symbols, args.step), fh, indent=1, sort_keys=True)
    if args.json:
        print(json.dumps({"by_grade": summarize(rows, ["horizon", "grade"]),
                          "primary_by_grade": summarize([r for r in rows if r["primary"]], ["horizon", "grade"]),
                          "by_side": summarize(rows, ["horizon", "side"]), "rows": len(rows)}, indent=2))
    else:
        print(render(rows, symbols, args.step))


if __name__ == "__main__":
    main()
