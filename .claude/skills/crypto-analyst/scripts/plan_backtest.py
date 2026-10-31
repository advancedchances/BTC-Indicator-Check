#!/usr/bin/env python3
"""
Walk-forward test of trade_plan.py: replay the plan on past dates, using
only the data available on each date, then trade it on the bars that
followed. It answers whether a setup grade (A++, A, B, C) and the bias mean
anything in R after costs, and what a bot that took the plans would have
earned. Standard library only.

Usage:
  python plan_backtest.py BTC ETH SOL                  # weekly snapshots, both horizons, both sides
  python plan_backtest.py BTC --step 14 --start 2019-01-01
  python plan_backtest.py BTC --csv trades.csv         # also write every simulated trade
  python plan_backtest.py BTC ETH SOL --holdout 2023-01-01   # also report the later period on its own
  python plan_backtest.py BTC ETH SOL --fee-bps 40 --slip-bps 10   # Coinbase Advanced-level costs
  python plan_backtest.py BTC ETH SOL --from-csv trades.csv --holdout 2023-01-01 \\
      --calibration ../references/plan_calibration.json   # re-summarize without replaying
  python plan_backtest.py BTC ETH SOL --json

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
    Anything still open when history runs out is closed at the last close.
  - R = profit per unit / (planned average entry - stop), scaled by the share
    of the planned position that filled, so a half-filled trade counts half.

Costs (on by default; quants.md's rule that a backtest without costs is not
evidence): --fee-bps on every fill (default 10, about a CEX taker fee;
Hyperliquid is ~4.5, Coinbase Advanced retail 40-60), plus --slip-bps on the
fills that behave like market orders: stops, time stops and the final close
(default 5). --funding-bps-day charges longs (and credits shorts) per day
held, for perps (default 0: spot). R is reported after costs; "gross" is
before.

Honest statistics:
  - Weekly snapshots overlap: a plan made this week often trades the same
    bars as last week's. Win-rate floors use only trades that don't overlap
    an earlier one on the same coin, horizon and side ("separate trades").
  - The floor on average R comes from a block bootstrap that resamples whole
    months of trades, so trades that shared a market stay together.
  - --holdout DATE splits the sample: plans dated before DATE whose trades
    also ended before it, and plans dated on or after it. The plan's design
    saw all of history, so the later period checks the calibration's
    stability, not the design; only plans logged from now on test that.

The bot view replays a few simple bots that take the plans one at a time per
coin (a pending order or open trade blocks the next plan on that coin),
risking --risk percent of equity per plan, marked to market daily. It
reports CAGR, Sharpe, max drawdown and time in market against buy-and-hold.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
import ta_core as T  # noqa: E402
import technicals as TA  # noqa: E402
import trade_plan as P  # noqa: E402
from market_data import FetchError, load_candles  # noqa: E402

WAIT = {"swing": 10, "position": 30}
MIN_HISTORY = 400
DAY = 86400
GRADE_ORDER = {"A++": 0, "A": 1, "B": 2, "C": 3}
BOTS = (
    ("Swing, primary side, every grade", lambda r: r["horizon"] == "swing" and r["primary"]),
    ("Swing, long, with the trend, every grade",
     lambda r: r["horizon"] == "swing" and r["side"] == "long" and not r["counter_trend"]),
    ("Swing, long, with the trend, grade A or A++ (picked after seeing the 2026 replay)",
     lambda r: r["horizon"] == "swing" and r["side"] == "long" and not r["counter_trend"] and r["grade"] in ("A", "A++")),
    ("Position, primary side, every grade", lambda r: r["horizon"] == "position" and r["primary"]),
)


def simulate(p, bars, wait, max_hold, fee=0.0, slip=0.0, funding=0.0):
    """Trade one plan side over the following bars. bars: list of (o, h, l, c).

    fee: fraction of the fill price per fill; slip: extra fraction on stop, time-stop and final fills;
    funding: fraction of the position's value per bar held (longs pay, shorts receive).
    Returns R after costs ("r"), before costs ("r_gross"), how it ended, and the daily marked-to-market
    path of cumulative R from the fill bar to the exit bar."""
    long = p["side"] == "long"
    sgn = 1 if long else -1
    e1 = p["entry_1"]["mid"]
    e2 = p["entry_2"]["mid"] if p["entry_2"] else None
    weights = {"e1": 0.5 if e2 else 1.0, "e2": 0.5 if e2 else 0.0}
    stop = p["stop"]
    targets = [t["price"] for t in p["targets"]][:3]
    unit_risk = abs(p["avg_entry"] - stop)
    if not targets or unit_risk <= 0:
        return {"filled": False, "r": 0.0, "r_gross": 0.0, "exit": "no plan", "end_bar": -1}
    share = 1.0 / len(targets)
    fills, path = {}, []
    open_frac, gross, cost, hit, stop_cur, first = 0.0, 0.0, 0.0, 0, stop, None

    def done(how, k):
        return {"filled": True, "r": (gross - cost) / unit_risk, "r_gross": gross / unit_risk,
                "fill_share": sum(weights[n] for n in fills), "exit": how, "bars": k - first,
                "entry_bar": first, "end_bar": k, "path": path}

    for k, (o, h, l, c) in enumerate(bars):
        fill_bar = False
        if hit == 0:
            for name, px in (("e1", e1), ("e2", e2)):
                if px is None or name in fills or (first is None and k >= wait):
                    continue
                if (l <= px) if long else (h >= px):
                    price = min(o, px) if long else max(o, px)
                    fills[name] = price
                    open_frac += weights[name]
                    cost += fee * price * weights[name]
                    fill_bar = True
                    if first is None:
                        first = k
        if first is None:
            if k >= wait:
                return {"filled": False, "r": 0.0, "r_gross": 0.0, "exit": "no fill", "end_bar": k}
            continue
        filled_frac = sum(weights[n] for n in fills)
        avg_fill = sum(fills[n] * weights[n] for n in fills) / filled_frac
        if not fill_bar:
            cost += funding * sgn * open_frac * avg_fill
        if (l <= stop_cur) if long else (h >= stop_cur):
            px = min(o, stop_cur) if long else max(o, stop_cur)
            gross += open_frac * sgn * (px - avg_fill)
            cost += (fee + slip) * px * open_frac
            path.append((gross - cost) / unit_risk)
            return done("stop" if hit == 0 else "stop after exit", k)
        if not fill_bar:
            while hit < len(targets) and (h >= targets[hit] if long else l <= targets[hit]):
                part = min(open_frac, share * filled_frac)
                gross += part * sgn * (targets[hit] - avg_fill)
                cost += fee * targets[hit] * part
                open_frac -= part
                hit += 1
                stop_cur = avg_fill
            if hit == len(targets) or open_frac <= 1e-9:
                path.append((gross - cost) / unit_risk)
                return done("all exits", k)
        if k - first >= max_hold:
            gross += open_frac * sgn * (c - avg_fill)
            cost += (fee + slip) * c * open_frac
            path.append((gross - cost) / unit_risk)
            return done("time stop", k)
        path.append((gross - cost + open_frac * sgn * (c - avg_fill)) / unit_risk)
    if first is None:
        return {"filled": False, "r": 0.0, "r_gross": 0.0, "exit": "no fill", "end_bar": len(bars) - 1}
    c = bars[-1][3]
    filled_frac = sum(weights[n] for n in fills)
    avg_fill = sum(fills[n] * weights[n] for n in fills) / filled_frac
    gross += open_frac * sgn * (c - avg_fill)
    cost += (fee + slip) * c * open_frac
    path[-1] = (gross - cost) / unit_risk
    return done("open at end", len(bars) - 1)


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


def load_series(sym):
    d, src = load_candles(sym, "1d", 4000)
    if d["t"][-1] + DAY > time.time():  # drop the bar still forming
        d = T.slice_series(d, 0, -1)
    return d, src


def run(symbols, step, start, end=None, costs=None):
    costs = costs or {}
    rows, btc_ctx, series = [], {}, {}
    order = sorted(symbols, key=lambda s: s != "BTC")  # BTC first, so alts can use its stance
    for sym in order:
        d, src = load_series(sym)
        series[sym] = d
        idx = snapshot_indexes(d["t"], step, start)
        if end:
            idx = [i for i in idx if d["t"][i] < end]
        t0 = time.time()
        last = len(d["t"]) - 1
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
                    sim = simulate(p, future, WAIT[hz], max_hold, **costs)
                    at = lambda off: d["t"][min(max(i + 1 + off, i), last)]  # noqa: E731
                    row = {"symbol": sym, "date": date, "snap_t": d["t"][i], "horizon": hz, "side": side,
                           "grade": p["grade"], "grade_points": p["grade_points"], "bias": o["bias"],
                           "bias_score": o["bias_score"], "primary": side == o["primary"],
                           "counter_trend": p["counter_trend"], "risk_pct": round(p["risk_pct"], 4), **sim}
                    row["end_t"] = at(sim["end_bar"]) if sim["end_bar"] >= 0 else d["t"][i]
                    if sim["filled"]:
                        row["entry_t"], row["exit_t"] = at(sim["entry_bar"]), row["end_t"]
                    rows.append(row)
            if n and n % 50 == 0:
                print(f"  {sym}: {n}/{len(idx)} snapshots, {time.time() - t0:.0f}s", file=sys.stderr)
        TA.NOW = None
    return rows, series


# ---------------------------------------------------------------- statistics

def separate(rs):
    """Filled trades that don't overlap an earlier one on the same coin, horizon and side."""
    by = defaultdict(list)
    for r in rs:
        if r["filled"]:
            by[(r["symbol"], r["horizon"], r["side"])].append(r)
    out = []
    for g in by.values():
        out += [g[i] for i in T.non_overlapping([(r["entry_t"], r["exit_t"]) for r in g])]
    return out


def month_blocks(filled):
    blocks = defaultdict(list)
    for r in filled:
        blocks[r["date"][:7]].append(r["r"])
    return list(blocks.values())


def cell_stats(rs, draws=1000):
    filled = [r for r in rs if r["filled"]]
    rr = [r["r"] for r in filled]
    wins = sum(1 for x in rr if x > 0)
    sep = separate(rs)
    sw = sum(1 for r in sep if r["r"] > 0)
    n = len(rr)
    return {"plans": len(rs), "filled": n, "fill_rate": n / len(rs) if rs else None,
            "win_rate": wins / n if n else None, "separate": len(sep),
            "win_floor": T.wilson_lower(sw, len(sep)) if sep else None,
            "avg_r": sum(rr) / n if n else None,
            "avg_r_gross": sum(r["r_gross"] for r in filled) / n if n else None,
            "avg_r_floor": T.bootstrap_mean_floor(month_blocks(filled), draws=draws) if n >= 10 else None,
            "median_r": T.median(rr), "r_per_plan": sum(rr) / len(rs) if rs else None, "total_r": sum(rr),
            "r2": sum(x * x for x in rr) / n if n else None,
            "avg_win": (lambda w: sum(w) / len(w) if w else None)([x for x in rr if x > 0]),
            "avg_loss": (lambda w: sum(w) / len(w) if w else None)([x for x in rr if x <= 0])}


def summarize(rows, keys):
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(r[k] for k in keys)].append(r)
    out = [{**dict(zip(keys, k)), **cell_stats(rs)} for k, rs in groups.items()]
    return sorted(out, key=lambda x: tuple(GRADE_ORDER.get(x.get(k), 9) if k == "grade" else str(x.get(k)) for k in keys))


def cell_key(r):
    return f"{r['horizon']}|{r['side']}|{'against' if r['counter_trend'] else 'with'}|{r['grade']}"


def cells(rows):
    by = defaultdict(list)
    for r in rows:
        by[cell_key(r)].append(r)
    out = {}
    for k, rs in by.items():
        s = cell_stats(rs)
        out[k] = {"plans": s["plans"], "fills": s["filled"], "separate": s["separate"]}
        for f in ("win_rate", "win_floor", "avg_r", "avg_r_gross", "avg_r_floor", "r_per_plan", "r2", "avg_win", "avg_loss"):
            out[k][f] = round(s[f], 3) if s[f] is not None else None
    return out


def split(rows, holdout):
    """Plans dated before the holdout whose trade (or pending order) also ended before it; plans on or after it."""
    ts = dt.datetime.fromisoformat(holdout).replace(tzinfo=dt.timezone.utc).timestamp()
    return [r for r in rows if r["end_t"] < ts], [r for r in rows if r["snap_t"] >= ts]


def calibration(rows, symbols, step, costs_bps, holdout=None):
    """Record per kind of plan (horizon, side, with/against trend, grade), for trade_plan.py to quote."""
    out = {"method": "plan_backtest.py walk-forward, chart-only plans, R after costs; win floors on separate "
                     "(non-overlapping) trades; avg R floor from a monthly block bootstrap",
           "costs_bps": costs_bps, "symbols": symbols, "step_days": step,
           "from": min(r["date"] for r in rows), "to": max(r["date"] for r in rows), "cells": cells(rows)}
    if holdout:
        early, late = split(rows, holdout)
        out.update({"holdout_from": holdout, "train_cells": cells(early), "holdout_cells": cells(late)})
    return out


# ---------------------------------------------------------------- bots

def portfolio(rows, series, pick, risk, holdout=None):
    """Daily equity of a bot taking the plans `pick` selects, one at a time per coin, risking `risk` of equity
    per plan (marked to market daily), next to equal-weight buy-and-hold of the same coins."""
    taken, busy = [], {}
    for r in sorted((r for r in rows if pick(r)), key=lambda r: (r["snap_t"], r["symbol"])):
        if r["snap_t"] < busy.get(r["symbol"], -1):
            continue
        busy[r["symbol"]] = r["end_t"]
        taken.append(r)
    if not taken:
        return None
    t0 = min(r["snap_t"] for r in rows)
    t1 = max(series[s]["t"][-1] for s in series)
    days = sorted({t for s in series.values() for t in s["t"] if t0 < t <= t1})
    inc, live = defaultdict(float), set()
    for r in taken:
        if not r["filled"]:
            continue
        ts = series[r["symbol"]]["t"]
        k = ts.index(r["entry_t"])
        prev = 0.0
        for j, v in enumerate(r["path"]):
            if k + j >= len(ts):
                break
            inc[ts[k + j]] += v - prev
            live.add(ts[k + j])
            prev = v
    rets = [risk * inc.get(t, 0.0) for t in days]
    closes = {s: dict(zip(d["t"], d["c"])) for s, d in series.items()}
    bench = []
    for a, b in zip(days, days[1:]):
        rs = [closes[s][b] / closes[s][a] - 1 for s in series if a in closes[s] and b in closes[s]]
        bench.append(sum(rs) / len(rs) if rs else 0.0)
    bench = [0.0] + bench
    filled = [r for r in taken if r["filled"]]
    wins = sum(r["r"] > 0 for r in filled)
    res = {"plans_taken": len(taken), "trades": len(filled), "win_rate": wins / len(filled) if filled else None,
           "avg_r": sum(r["r"] for r in filled) / len(filled) if filled else None,
           "in_market": len(live) / len(days) if days else None,
           "stats": T.perf_stats(rets), "bench": T.perf_stats(bench),
           "from": T.utc(days[0]).strftime("%Y-%m-%d"), "to": T.utc(days[-1]).strftime("%Y-%m-%d")}
    if holdout:
        ts = dt.datetime.fromisoformat(holdout).replace(tzinfo=dt.timezone.utc).timestamp()
        cut = next((i for i, t in enumerate(days) if t >= ts), len(days))
        res["early"] = {"stats": T.perf_stats(rets[:cut]), "bench": T.perf_stats(bench[:cut])}
        res["late"] = {"stats": T.perf_stats(rets[cut:]), "bench": T.perf_stats(bench[cut:])}
    return res


# ---------------------------------------------------------------- csv

NUMERIC = ("snap_t", "entry_t", "exit_t", "end_t", "entry_bar", "end_bar", "bars")


def write_csv(rows, path):
    keys = sorted({k for r in rows for k in r})
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            r = dict(r)
            if "path" in r:
                r["path"] = ";".join(f"{v:.4f}" for v in r["path"])
            w.writerow(r)


def load_csv(path):
    rows = list(csv.DictReader(open(path)))
    if rows and "end_t" not in rows[0]:
        sys.exit(f"{path} was written by an older plan_backtest.py (no trade times or costs); replay instead")
    for r in rows:
        for k in ("filled", "counter_trend", "primary"):
            r[k] = r[k] == "True"
        for k in ("r", "r_gross", "risk_pct", "bias_score"):
            r[k] = float(r[k]) if r.get(k) not in (None, "") else 0.0
        for k in NUMERIC:
            r[k] = int(float(r[k])) if r.get(k) not in (None, "") else None
        r["path"] = [float(x) for x in r["path"].split(";")] if r.get("path") else []
    return rows


# ---------------------------------------------------------------- rendering

def fmt(x, kind="r"):
    if x is None:
        return "n/a"
    return f"{x * 100:.0f}%" if kind == "pct" else f"{x:+.2f}"


def table(title, summ, keys):
    L = [f"### {title}", "| " + " | ".join(k.replace("_", " ").capitalize() for k in keys)
         + " | Plans | Filled | Win rate | Separate trades | Win floor | Avg R | Avg R gross | Avg R floor | R per plan | Total R |",
         "|" + "---|" * (len(keys) + 10)]
    for s in summ:
        L.append("| " + " | ".join(str(s[k]) for k in keys) + f" | {s['plans']} | {fmt(s['fill_rate'], 'pct')} "
                 f"| {fmt(s['win_rate'], 'pct')} | {s['separate']} | {fmt(s['win_floor'], 'pct')} | {fmt(s['avg_r'])} "
                 f"| {fmt(s['avg_r_gross'])} | {fmt(s['avg_r_floor'])} | {fmt(s['r_per_plan'])} | {s['total_r']:+.1f} |")
    return L


def pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def num(x):
    return "n/a" if x is None else f"{x:.2f}"


def bot_rows(bots, part=None):
    head = "| Bot | " + ("" if part else "Trades | Win rate | Avg R | In market | ") + \
        "CAGR | Sharpe | Max DD | Hold CAGR | Hold Sharpe | Hold max DD |"
    L = [head, "|" + "---|" * (head.count("|") - 1)]
    for name, b in bots:
        if not b:
            continue
        s, h = (b[part]["stats"], b[part]["bench"]) if part else (b["stats"], b["bench"])
        if not s or not h:
            continue
        lead = f"| {name} | " + ("" if part else f"{b['trades']} | {fmt(b['win_rate'], 'pct')} | {fmt(b['avg_r'])} "
                                 f"| {fmt(b['in_market'], 'pct')} | ")
        L.append(lead + f"{pct(s['cagr'])} | {num(s['sharpe'])} | {pct(s['max_dd'])} | {pct(h['cagr'])} | {num(h['sharpe'])} "
                 f"| {pct(h['max_dd'])} |")
    return L


def render(rows, symbols, step, costs_bps, bots=None, risk=None, holdout=None):
    first, last = min(r["date"] for r in rows), max(r["date"] for r in rows)
    L = [f"## Walk-forward test of trade plans: {', '.join(symbols)}",
         f"Snapshots every {step} days from {first} to {last}; chart-only plans (no positioning history). Costs: "
         f"{costs_bps['fee']:g} bps a fill, {costs_bps['slip']:g} bps slippage on stop and time exits"
         + (f", funding {costs_bps['funding']:g} bps a day" if costs_bps["funding"] else "") + ". R is after costs. "
         "Win floor: 90% lower bound on separate (non-overlapping) trades. Avg R floor: 90% lower bound from a monthly "
         "block bootstrap. R per plan counts unfilled plans as zero.\n"]
    for title, keys in (("By horizon and grade", ["horizon", "grade"]),
                        ("Primary side only (the side the bias picked), by horizon and grade", ["horizon", "grade"]),
                        ("By horizon and side", ["horizon", "side"]),
                        ("By horizon and bias", ["horizon", "bias"]),
                        ("With or against the trend", ["horizon", "counter_trend"]),
                        ("By coin and horizon", ["symbol", "horizon"])):
        rs = [r for r in rows if r["primary"]] if title.startswith("Primary") else rows
        L += table(title, summarize(rs, keys), keys)
        L.append("")
    ex = defaultdict(int)
    for r in rows:
        if r["filled"]:
            ex[r["exit"]] += 1
    L.append("How filled trades ended: " + ", ".join(f"{k} {v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])))
    if bots:
        b0 = next((b for _, b in bots if b), None)
        L += ["", f"### Bots: one plan at a time per coin, {risk * 100:g}% of equity at risk per plan, marked to market daily",
              (f"{b0['from']} to {b0['to']}. Hold = equal-weight buy-and-hold of {', '.join(symbols)}, rebalanced daily, "
               "no costs.\n") if b0 else ""]
        L += bot_rows(bots)
        if holdout:
            for part, label in (("early", f"before {holdout}"), ("late", f"from {holdout}")):
                L += ["", f"Bots {label}:", ""] + bot_rows(bots, part)
    if holdout:
        early, late = split(rows, holdout)
        a, b = cells(early), cells(late)
        L += ["", f"### Earlier vs later period (split at {holdout}; earlier plans must also have ended before it)",
              "| Kind of plan | Earlier fills | Earlier win rate | Earlier avg R | Later fills | Later win rate | Later avg R | Later avg R floor |",
              "|---|---|---|---|---|---|---|---|"]
        for k in sorted(set(a) & set(b), key=lambda k: (k.split("|")[0], k.split("|")[1], k.split("|")[2],
                                                        GRADE_ORDER.get(k.split("|")[3], 9))):
            if a[k]["fills"] < 20 or b[k]["fills"] < 10:
                continue
            L.append(f"| {k.replace('|', ', ')} | {a[k]['fills']} | {fmt(a[k]['win_rate'], 'pct')} | {fmt(a[k]['avg_r'])} "
                     f"| {b[k]['fills']} | {fmt(b[k]['win_rate'], 'pct')} | {fmt(b[k]['avg_r'])} | {fmt(b[k]['avg_r_floor'])} |")
        L.append("\nThe plan's design saw all of this history, so the later period checks how stable the record is, "
                 "not whether the design generalizes. Only plans logged from now on test that.")
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--step", type=int, default=7, help="days between snapshots (default 7)")
    p.add_argument("--start", default="2017-01-01", help="first snapshot date, YYYY-MM-DD (default 2017-01-01)")
    p.add_argument("--end", help="last snapshot date, YYYY-MM-DD")
    p.add_argument("--fee-bps", type=float, default=10.0, help="fee per fill in basis points (default 10)")
    p.add_argument("--slip-bps", type=float, default=5.0, help="slippage on stop, time-stop and final fills (default 5)")
    p.add_argument("--funding-bps-day", type=float, default=0.0, help="perp funding per day held; longs pay (default 0)")
    p.add_argument("--risk", type=float, default=1.0, help="percent of equity a bot risks per plan (default 1)")
    p.add_argument("--holdout", metavar="DATE", help="also report plans from DATE on separately, e.g. 2023-01-01")
    p.add_argument("--no-bots", action="store_true", help="skip the bot equity curves")
    p.add_argument("--csv", help="write every simulated plan to this CSV")
    p.add_argument("--from-csv", help="re-summarize a CSV written by --csv instead of replaying")
    p.add_argument("--calibration", help="write the per-kind record JSON that trade_plan.py quotes "
                   "(references/plan_calibration.json)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    start = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.timezone.utc).timestamp()
    end = dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.timezone.utc).timestamp() if args.end else None
    symbols = [s.upper() for s in args.symbols]
    costs = {"fee": args.fee_bps / 1e4, "slip": args.slip_bps / 1e4, "funding": args.funding_bps_day / 1e4}
    costs_bps = {"fee": args.fee_bps, "slip": args.slip_bps, "funding": args.funding_bps_day}
    series = {}
    if args.from_csv:
        rows = load_csv(args.from_csv)
        symbols = sorted({r["symbol"] for r in rows}, key=lambda s: (s != "BTC", s))
    else:
        rows, series = run(symbols, args.step, start, end, costs)
    if not rows:
        sys.exit("no snapshots: history too short for the chosen start date")
    if args.csv:
        write_csv(rows, args.csv)
    if args.calibration:
        with open(args.calibration, "w") as fh:
            json.dump(calibration(rows, symbols, args.step, costs_bps, args.holdout), fh, indent=1, sort_keys=True)
    bots = None
    if not args.no_bots:
        try:
            series = series or {s: load_series(s)[0] for s in symbols}
            bots = [(name, portfolio(rows, series, pick, args.risk / 100, args.holdout)) for name, pick in BOTS]
        except FetchError as e:
            print(f"bots skipped: no candles to mark trades to market ({e})", file=sys.stderr)
    if args.json:
        out = {"by_grade": summarize(rows, ["horizon", "grade"]),
               "primary_by_grade": summarize([r for r in rows if r["primary"]], ["horizon", "grade"]),
               "by_side": summarize(rows, ["horizon", "side"]), "rows": len(rows), "costs_bps": costs_bps}
        if bots:
            out["bots"] = [{"bot": n, **(b or {})} for n, b in bots]
        if args.holdout:
            early, late = split(rows, args.holdout)
            out["earlier"], out["later"] = cells(early), cells(late)
        print(json.dumps(out, indent=2, default=str))
    else:
        print(render(rows, symbols, args.step, costs_bps, bots, args.risk / 100, args.holdout))


if __name__ == "__main__":
    main()
