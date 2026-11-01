#!/usr/bin/env python3
"""
Strategy lab: test simple, rule-based strategies on daily bars, with costs,
against buy-and-hold, using one scorecard for every rule (total return, CAGR,
volatility, Sharpe, Sortino, max drawdown, Calmar, time in market, turnover)
plus two checks on whether a good number is luck: the probabilistic Sharpe
ratio, and for parameter grids the deflated Sharpe ratio. Standard library
only; candles from the same keyless venues as the rest of the skill.

Usage:
  python strategy_lab.py BTC                             # every single-coin strategy on BTC
  python strategy_lab.py BTC ETH SOL --split 2023-01-01  # same, with the years before and after a date
  python strategy_lab.py BTC ETH --grid                  # parameter grids: plateau or spike, deflated Sharpe
  python strategy_lab.py --basket BTC ETH SOL XRP DOGE ADA LTC LINK BCH   # cross-coin strategies
  python strategy_lab.py SPY QQQ --asset stock           # stocks and ETFs (Yahoo Finance)
  python strategy_lab.py BTC --strategies hold,voltrend --breaker 25 --cooldown 30
  python strategy_lab.py BTC --csv equity.csv --json
  --fee-bps 10 --slip-bps 5 (charged on every unit traded), --start 2017-01-01, --rf 0.04

Single-coin strategies (long or flat; the position is set on the daily close and traded at the next open)
  hold      buy and hold: the benchmark every rule has to beat
  notebook  the BTC Indicator Check notebook's verdict: RSI(14) under 30 or over 70, a MACD(12,26,9)
            histogram cross, a close outside the Bollinger Bands(20,2). Long from a BULLISH verdict
            until a BEARISH one
  sma       long while the close is above its 200-day SMA
  tsmom     time-series momentum: the share of the 28, 56, 112 and 224-day returns that are positive
            (0, 25, 50, 75 or 100% invested). Trend following of this kind is the most replicated
            edge in futures (Moskowitz, Ooi and Pedersen 2012) and has been documented in crypto
  voltrend  tsmom scaled so the position targets 40% annualized volatility, never above 100% invested.
            Volatility clusters, so cutting size when it rises improves risk-adjusted returns
            (Moreira and Muir 2017). Trades only when the target moves 10% or more (a drift band)
Basket strategies (--basket; weekly rebalance on the last close of the week)
  basket       equal-weight buy and hold of the coins, rebalanced weekly: the benchmark
  xsmom        cross-sectional momentum: the 3 coins with the best 28-day return, each only while that
               return is positive, else cash (relative plus absolute momentum, i.e. dual momentum)
  trendbasket  each coin gets 1/N of the book, scaled by its own tsmom signal and to 40% volatility

Overlay
  --breaker 25 --cooldown 30  also show each strategy with a circuit breaker: flat for 30 days after a
                              25% fall from its equity peak, then back on its signal

Reading the output
  - Beats hold (or basket): the share of block-bootstrap resamples (21-day blocks, strategy and
    benchmark resampled together) in which the rule's Sharpe beat the benchmark's. A Sharpe above
    zero proves nothing here: holding BTC has had one near 1. Under about 90% the edge could be luck.
    PSR (probability the Sharpe is above zero) is in the JSON.
  - --grid runs each strategy over a parameter grid. A robust rule shows a plateau (neighbouring
    settings nearly as good); a lone spike is curve-fitting. The best setting's chance of beating the
    benchmark is raised to the power of the settings tried, a conservative correction for picking
    the best of several. The deflated Sharpe (Bailey and Lopez de Prado 2014, against zero) is in
    the JSON.
  - --split compares the period before and after a date. A rule that only worked early is a warning.
  - Every coin needs 370 days of history before the test starts, so all lookbacks are warm and every
    strategy on a coin is scored on the same days.
  - Basket results carry survivorship bias: the coins are ones still trading today (no LUNA, FTT).
    Judge xsmom and trendbasket against the equal-weight basket of the same coins, which shares the
    bias, rather than against BTC.
  - Costs are charged on every change in position. Cash earns --rf (default 0). No funding, borrow or
    taxes. Daily bars: a stop or a fill inside the day is not modelled.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
import ta_core as T  # noqa: E402
from market_data import FetchError, forming, load_candles  # noqa: E402

DAY = 86400
WARM = 370          # bars of history a coin needs before it is traded (longest lookback 365 + slack)
TSMOM_LOOKBACKS = (28, 56, 112, 224)
SINGLE_BAND = 0.10  # drift band for single-coin strategies
BASKET_BAND = 0.05


# ---------------------------------------------------------------- data

def load(sym, asset=None, days=4000):
    s, src = load_candles(sym, "1d", days, asset=asset)
    if forming(s, "1d"):
        s = T.slice_series(s, 0, -1)
    else:
        s = T.slice_series(s, 0, None)
    return s, src


def bar_returns(s):
    """Return over each bar from its open to the next bar's open (the last bar: open to close)."""
    o, c = s["o"], s["c"]
    return [o[j + 1] / o[j] - 1 if o[j] else 0.0 for j in range(len(o) - 1)] + ([c[-1] / o[-1] - 1] if o else [])


# ---------------------------------------------------------------- single-coin signals (target weight per bar)

def sig_hold(s, **_):
    return [1.0] * len(s["c"])


def sig_notebook(s, **_):
    c = s["c"]
    r = T.rsi(c, 14)
    hist = T.macd(c)[2]
    _, up, lo, _, _ = T.bollinger(c, 20, 2.0)
    out, pos = [], 0.0
    for i in range(len(c)):
        buy = sell = 0
        if r[i] is not None:
            buy, sell = buy + (r[i] < 30), sell + (r[i] > 70)
        if i and hist[i] is not None and hist[i - 1] is not None:
            buy, sell = buy + (hist[i] > 0 >= hist[i - 1]), sell + (hist[i] < 0 <= hist[i - 1])
        if lo[i] is not None:
            buy, sell = buy + (c[i] <= lo[i]), sell + (c[i] >= up[i])
        if buy > sell:
            pos = 1.0
        elif sell > buy:
            pos = 0.0
        out.append(pos)
    return out


def sig_sma(s, n=200, **_):
    m = T.sma(s["c"], n)
    return [1.0 if v is not None and x > v else 0.0 for x, v in zip(s["c"], m)]


def sig_tsmom(s, lookbacks=TSMOM_LOOKBACKS, **_):
    c = s["c"]
    top = max(lookbacks)
    return [sum(c[i] > c[i - L] for L in lookbacks) / len(lookbacks) if i >= top else 0.0 for i in range(len(c))]


def sig_voltrend(s, target=0.40, lookbacks=TSMOM_LOOKBACKS, periods=365, **_):
    base = sig_tsmom(s, lookbacks)
    vol = T.rolling_vol(s["c"], 30, periods)
    return [b * min(1.0, target / v) if v else 0.0 for b, v in zip(base, vol)]


SINGLE = {
    "hold": ("Buy and hold", sig_hold, {}),
    "notebook": ("Notebook verdict (RSI/MACD/Bollinger vote)", sig_notebook, {}),
    "sma": ("Long above the 200-day SMA", sig_sma, {"n": 200}),
    "tsmom": ("Trend: share of 28/56/112/224-day returns up", sig_tsmom, {}),
    "voltrend": ("Trend, scaled to 40% volatility", sig_voltrend, {"target": 0.40}),
}
SINGLE_GRID = {
    "sma": ("n", [{"n": n} for n in (20, 50, 100, 150, 200, 250, 300)]),
    "tsmom": ("lookback", [{"lookbacks": (L,)} for L in (7, 14, 28, 56, 112, 224, 365)]),
    "voltrend": ("target vol", [{"target": t} for t in (0.2, 0.3, 0.4, 0.6, 0.8)]),
}


# ---------------------------------------------------------------- basket signals (target weights on rebalance bars)

def week_ends(t):
    """The last bar of each ISO week: the Sunday close for crypto, usually Friday for stocks."""
    return [i == len(t) - 1 or T.period_key(t[i + 1], "W") != T.period_key(t[i], "W") for i in range(len(t))]


def bas_equal(view, j, avail, **_):
    return {a: 1 / len(avail) for a in avail}


def bas_xsmom(view, j, avail, n=28, k=3, **_):
    scores = []
    for a in avail:
        i = view[a]["idx"][j]
        if i >= n:
            c = view[a]["s"]["c"]
            scores.append((c[i] / c[i - n] - 1, a))
    scores.sort(reverse=True)
    return {a: 1 / k for sc, a in scores[:k] if sc > 0}


def bas_trend(view, j, avail, target=0.40, **_):
    out = {}
    for a in avail:
        i = view[a]["idx"][j]
        sig, vol = view[a]["tsmom"][i], view[a]["vol"][i]
        if sig and vol:
            out[a] = sig * min(1.0, target / vol) / len(avail)
    return out


BASKET = {
    "basket": ("Equal-weight basket, rebalanced weekly", bas_equal, {}),
    "xsmom": ("Top 3 by 28-day return, while positive", bas_xsmom, {"n": 28, "k": 3}),
    "trendbasket": ("Each coin's trend at 40% volatility, 1/N each", bas_trend, {"target": 0.40}),
}
BASKET_GRID = {
    "xsmom": ("lookback, top K", [{"n": n, "k": k} for n in (7, 14, 28, 56, 91) for k in (1, 2, 3, 5)]),
    "trendbasket": ("target vol", [{"target": t} for t in (0.2, 0.3, 0.4, 0.6, 0.8)]),
}


# ---------------------------------------------------------------- engine

def simulate(rets, targets, cost, start=1, band=0.0, breaker=None, cooldown=0, rf_period=0.0):
    """Run a book over bars.

    rets[j]: each asset's return over bar j (its open to the next open). targets[j]: weights decided at the
    close of bar j (None keeps the book), traded at the open of bar j+1 when any weight moves by `band` or
    more (always when going flat). Weights drift with prices between trades. cost: fraction lost per unit
    traded. breaker: go flat for `cooldown` bars after the equity falls this fraction from its peak.
    Returns per-bar returns, turnover and exposure, and the number of entries from flat."""
    n = len(rets)
    m = len(rets[0]) if n else 0
    w = [0.0] * m
    out, turn, expo = [0.0] * n, [0.0] * n, [0.0] * n
    eq = peak = 1.0
    pause_to, entries = -1, 0
    for j in range(max(start, 1), n):
        tgt = targets[j - 1]
        if breaker is not None and j <= pause_to:
            tgt = [0.0] * m
        c = 0.0
        if tgt is not None:
            d = [a - b for a, b in zip(tgt, w)]
            going_flat = sum(tgt) <= 1e-12 and sum(w) > 1e-12
            if going_flat or max(abs(x) for x in d) >= max(band, 1e-12):
                if sum(w) <= 1e-12 and sum(tgt) > 1e-12:
                    entries += 1
                turn[j] = sum(abs(x) for x in d)
                c = turn[j] * cost
                w = list(tgt)
        r = sum(a * b for a, b in zip(w, rets[j]))
        cash = 1 - sum(w)
        out[j] = r + cash * rf_period - c
        expo[j] = sum(w)
        g = 1 + r + cash * rf_period
        w = [a * (1 + b) / g for a, b in zip(w, rets[j])] if g > 0 else [0.0] * m
        eq *= 1 + out[j]
        if breaker is not None and j > pause_to:
            peak = max(peak, eq)
            if eq / peak - 1 <= -breaker:
                pause_to, peak = j + cooldown, eq
    return out, turn, expo, entries


def score(t, out, turn, expo, entries, start, periods, rf=0.0):
    st = T.perf_stats(out[start:], periods, rf)
    if not st:
        return None
    st["psr"] = T.psr(st["sr_period"], st["n"], st["skew"], st["kurt"])
    st["in_market"] = sum(expo[start:]) / len(expo[start:])
    st["turnover"] = sum(turn[start:]) / st["years"]
    st["entries"] = entries
    years = {}
    for ts, r in zip(t[start:], out[start:]):
        y = T.utc(ts).year
        years[y] = years.get(y, 1.0) * (1 + r)
    st["by_year"] = {y: v - 1 for y, v in years.items()}
    return st


def split_scores(t, out, start, cut_ts, periods, rf):
    cut = next((i for i in range(start, len(t)) if t[i] >= cut_ts), len(t))
    return {"before": T.perf_stats(out[start:cut], periods, rf), "after": T.perf_stats(out[cut:], periods, rf)}


# ---------------------------------------------------------------- runners

def periods_of(src):
    return 252 if src == "Yahoo Finance" else 365


def start_index(t, start_ts, first_ok):
    return max(first_ok, next((i for i, x in enumerate(t) if x >= start_ts), len(t)))


def run_single(s, src, names, cfg, grid=False):
    periods = periods_of(src)
    t = s["t"]
    rets = [[r] for r in bar_returns(s)]
    start = start_index(t, cfg["start_ts"], WARM)
    if start >= len(t) - 30:
        raise ValueError("not enough history after the warm-up")
    res = {"from": T.utc(t[start]).strftime("%Y-%m-%d"), "to": T.utc(t[-1]).strftime("%Y-%m-%d"), "source": src,
           "strategies": {}, "grids": {}, "equity": {}}

    def one(key, label, fn, params, breaker=None):
        tg = [[x] for x in fn(s, periods=periods, **params)]
        band = 0.0 if key in ("hold", "notebook", "sma") else SINGLE_BAND
        out, turn, expo, ent = simulate(rets, tg, cfg["cost"], start, band, breaker, cfg["cooldown"], cfg["rf_period"])
        st = score(t, out, turn, expo, ent, start, periods, cfg["rf"])
        if st and cfg.get("split_ts"):
            st["split"] = split_scores(t, out, start, cfg["split_ts"], periods, cfg["rf"])
        return st, out

    for key in names:
        label, fn, params = SINGLE[key]
        st, out = one(key, label, fn, params)
        res["strategies"][key] = dict(st, label=label)
        res["equity"][key] = out
        if cfg.get("breaker"):
            st, out = one(key, label, fn, params, cfg["breaker"])
            k2 = f"{key}+breaker"
            res["strategies"][k2] = dict(st, label=f"{label}, {cfg['breaker'] * 100:g}% breaker")
            res["equity"][k2] = out
    if grid:
        for key, (pname, plist) in SINGLE_GRID.items():
            if key not in names:
                continue
            label, fn, base = SINGLE[key]
            trials = [(prm, *one(key, label, fn, dict(base, **prm))) for prm in plist]
            res["grids"][key] = grid_summary(pname, trials, res["equity"]["hold"], start)
    add_beats(res, "hold", start)
    res["_t"] = t
    res["_start"] = start
    return res


def run_basket(series, names, cfg, grid=False):
    srcs = {src for _, src in series.values()}
    periods = 252 if srcs == {"Yahoo Finance"} else 365
    t = sorted({x for s, _ in series.values() for x in s["t"]})
    pos = {x: j for j, x in enumerate(t)}
    assets = list(series)
    view, rets = {}, [[0.0] * len(assets) for _ in t]
    for a_i, a in enumerate(assets):
        s = series[a][0]
        idx = [None] * len(t)
        for i, x in enumerate(s["t"]):
            idx[pos[x]] = i
        br = bar_returns(s)
        for i, x in enumerate(s["t"]):
            rets[pos[x]][a_i] = br[i]
        view[a] = {"s": s, "idx": idx, "tsmom": sig_tsmom(s), "vol": T.rolling_vol(s["c"], 30, periods)}
    avail = [[a for a in assets if view[a]["idx"][j] is not None and view[a]["idx"][j] >= WARM] for j in range(len(t))]
    ends = week_ends(t)
    need = min(3, len(assets))
    first_ok = next((j for j in range(len(t)) if len(avail[j]) >= need), len(t))
    start = start_index(t, cfg["start_ts"], first_ok + 1)
    if start >= len(t) - 30:
        raise ValueError("not enough history after the warm-up")
    res = {"from": T.utc(t[start]).strftime("%Y-%m-%d"), "to": T.utc(t[-1]).strftime("%Y-%m-%d"),
           "source": ", ".join(sorted(srcs)), "assets": assets, "strategies": {}, "grids": {}, "equity": {}}

    def targets(fn, params):
        tg = [None] * len(t)
        for j in range(len(t)):
            if ends[j] and avail[j]:
                w = fn(view, j, avail[j], **params)
                tg[j] = [w.get(a, 0.0) for a in assets]
        return tg

    def one(fn, params, breaker=None):
        out, turn, expo, ent = simulate(rets, targets(fn, params), cfg["cost"], start, BASKET_BAND, breaker,
                                        cfg["cooldown"], cfg["rf_period"])
        st = score(t, out, turn, expo, ent, start, periods, cfg["rf"])
        if st and cfg.get("split_ts"):
            st["split"] = split_scores(t, out, start, cfg["split_ts"], periods, cfg["rf"])
        return st, out

    if "BTC" in assets:
        bi = assets.index("BTC")
        tg = [[1.0 if k == bi else 0.0 for k in range(len(assets))] for _ in t]
        out, turn, expo, ent = simulate(rets, tg, cfg["cost"], start, 0.0, None, 0, cfg["rf_period"])
        st = score(t, out, turn, expo, ent, start, periods, cfg["rf"])
        if st and cfg.get("split_ts"):
            st["split"] = split_scores(t, out, start, cfg["split_ts"], periods, cfg["rf"])
        res["strategies"]["btc"] = dict(st, label="BTC buy and hold")
        res["equity"]["btc"] = out
    for key in names:
        label, fn, params = BASKET[key]
        st, out = one(fn, params)
        res["strategies"][key] = dict(st, label=label)
        res["equity"][key] = out
        if cfg.get("breaker"):
            st, out = one(fn, params, cfg["breaker"])
            res["strategies"][f"{key}+breaker"] = dict(st, label=f"{label}, {cfg['breaker'] * 100:g}% breaker")
            res["equity"][f"{key}+breaker"] = out
    if grid:
        for key, (pname, plist) in BASKET_GRID.items():
            if key not in names:
                continue
            _, fn, base = BASKET[key]
            plist = [prm for prm in plist if prm.get("k", 1) < len(assets)]
            trials = [(prm, *one(fn, dict(base, **prm))) for prm in plist]
            res["grids"][key] = grid_summary(pname, trials, res["equity"]["basket"], start)
    add_beats(res, "basket", start)
    res["_t"] = t
    res["_start"] = start
    return res


def add_beats(res, ref_key, start):
    """For each strategy, the bootstrap probability that its Sharpe beats the benchmark's on the same days."""
    ref = res["equity"][ref_key][start:]
    for k, st in res["strategies"].items():
        st["beats"] = None if k == ref_key else T.sharpe_beat_prob(res["equity"][k][start:], ref)
    res["benchmark"] = ref_key


def grid_summary(pname, trials, ref_out, start):
    """Each setting's scorecard and whether it beat the benchmark. The best setting's chance of beating the
    benchmark is raised to the power of the number of settings tried (a conservative correction for picking
    the best of several), and its Sharpe is deflated for the trials (DSR, against zero)."""
    ok = [(p, s, out) for p, s, out in trials if s and s["sr_period"] is not None]
    if not ok:
        return None
    ref = ref_out[start:]
    for _, s, out in ok:
        s["beats"] = T.sharpe_beat_prob(out[start:], ref)
    srs = [s["sr_period"] for _, s, _ in ok]
    m = sum(srs) / len(srs)
    var = sum((x - m) ** 2 for x in srs) / (len(srs) - 1) if len(srs) > 1 else 0.0
    bp, bs, _ = max(ok, key=lambda x: x[1]["sr_period"])
    sharpes = sorted(s["sharpe"] for _, s, _ in ok)
    beats = [s["beats"] for _, s, _ in ok if s["beats"] is not None]
    return {"param": pname, "trials": [{"params": p, "sharpe": s["sharpe"], "cagr": s["cagr"], "max_dd": s["max_dd"],
                                        "beats": s["beats"]} for p, s, _ in ok],
            "best": bp, "best_sharpe": bs["sharpe"], "median_sharpe": T.median(sharpes), "worst_sharpe": sharpes[0],
            "best_beats": bs["beats"], "best_beats_adjusted": bs["beats"] ** len(ok) if bs["beats"] is not None else None,
            "settings_beating": sum(b >= 0.5 for b in beats), "dsr": T.deflated_sharpe(
                bs["sr_period"], bs["n"], bs["skew"], bs["kurt"], len(ok), var)}


# ---------------------------------------------------------------- rendering

def pc(x, signed=True):
    return "n/a" if x is None else (f"{x * 100:+.1f}%" if signed else f"{x * 100:.0f}%")


def nm(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def params_text(p):
    return ", ".join(f"{v}" if not isinstance(v, tuple) else "/".join(map(str, v)) for v in p.values())


def render(title, res, cfg):
    yrs = (dt.date.fromisoformat(res["to"]) - dt.date.fromisoformat(res["from"])).days / 365.25
    bench = "hold" if res["benchmark"] == "hold" else "basket"
    L = [f"## Strategy lab: {title}",
         f"{res['source']} daily bars, {res['from']} to {res['to']} ({yrs:.1f} years). Costs {cfg['cost'] * 1e4:g} bps "
         f"per unit traded. Position set on the daily close, traded at the next open; cash earns "
         f"{cfg['rf'] * 100:g}%.\n",
         f"| Strategy | CAGR | Volatility | Sharpe | Beats {bench} | Sortino | Max DD | Calmar | In market | Turnover/yr | Entries |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in res["strategies"].items():
        L.append(f"| {s['label']} | {pc(s['cagr'])} | {pc(s['vol'], False)} | {nm(s['sharpe'])} | "
                 f"{'(benchmark)' if s['beats'] is None else pc(s['beats'], False)} "
                 f"| {nm(s['sortino'])} | {pc(s['max_dd'])} | {nm(s['calmar'])} | {pc(s['in_market'], False)} "
                 f"| {s['turnover']:.1f}x | {s['entries']} |")
    if cfg.get("split_ts"):
        d = cfg["split"]
        L += ["", f"Before and after {d}:", "",
              "| Strategy | Sharpe before | Sharpe after | CAGR before | CAGR after | Max DD before | Max DD after |",
              "|---|---|---|---|---|---|---|"]
        for s in res["strategies"].values():
            b, a = s["split"]["before"], s["split"]["after"]
            if not b or not a:
                continue
            L.append(f"| {s['label']} | {nm(b['sharpe'])} | {nm(a['sharpe'])} | {pc(b['cagr'])} | {pc(a['cagr'])} "
                     f"| {pc(b['max_dd'])} | {pc(a['max_dd'])} |")
    keys = list(res["strategies"])
    years = sorted({y for s in res["strategies"].values() for y in s["by_year"]})
    L += ["", "By calendar year (first and last years are partial):", "",
          "| Year | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for y in years:
        L.append(f"| {y} | " + " | ".join(pc(res["strategies"][k]["by_year"].get(y)) for k in keys) + " |")
    for key, g in res["grids"].items():
        if not g:
            continue
        L += ["", f"Grid for {key} ({g['param']}): best Sharpe {nm(g['best_sharpe'])} at {params_text(g['best'])}, "
              f"median {nm(g['median_sharpe'])}, worst {nm(g['worst_sharpe'])}; {g['settings_beating']} of "
              f"{len(g['trials'])} settings beat {bench} more often than not. Chance the best setting really beats "
              f"{bench}, allowing for {len(g['trials'])} tries: {pc(g['best_beats_adjusted'], False)}.", "",
              f"| {g['param']} | Sharpe | CAGR | Max DD | Beats {bench} |", "|---|---|---|---|---|"]
        for tr in g["trials"]:
            L.append(f"| {params_text(tr['params'])} | {nm(tr['sharpe'])} | {pc(tr['cagr'])} | {pc(tr['max_dd'])} "
                     f"| {pc(tr['beats'], False)} |")
    return "\n".join(L)


def render_summary(results):
    keys = []
    for r in results.values():
        for k in r["strategies"]:
            if k not in keys:
                keys.append(k)
    L = ["## Summary: Sharpe (max drawdown) by coin", "", "| Coin | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for sym, r in results.items():
        cells = []
        for k in keys:
            s = r["strategies"].get(k)
            cells.append(f"{nm(s['sharpe'])} ({pc(s['max_dd'])})" if s else "")
        L.append(f"| {sym} | " + " | ".join(cells) + " |")
    return "\n".join(L)


def strip(res):
    return {k: v for k, v in res.items() if not k.startswith("_") and k != "equity"}


def write_equity(path, results):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "set", "strategy", "equity"])
        for name, r in results.items():
            t, start = r["_t"], r["_start"]
            for k, out in r["equity"].items():
                eq = 1.0
                for ts, x in zip(t[start:], out[start:]):
                    eq *= 1 + x
                    w.writerow([T.utc(ts).strftime("%Y-%m-%d"), name, k, f"{eq:.6f}"])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="*", help="coins (or stocks with --asset stock), each tested on its own")
    p.add_argument("--basket", nargs="+", metavar="SYM", help="run the basket strategies over these coins")
    p.add_argument("--strategies", help="comma-separated subset, e.g. hold,voltrend (default: all)")
    p.add_argument("--asset", choices=["crypto", "stock"])
    p.add_argument("--start", default="2015-01-01", help="first date to score (default: as early as history allows)")
    p.add_argument("--split", metavar="DATE", help="also score the periods before and after DATE")
    p.add_argument("--grid", action="store_true", help="run the parameter grids and report the deflated Sharpe")
    p.add_argument("--fee-bps", type=float, default=10.0, help="fee per unit traded, in basis points (default 10)")
    p.add_argument("--slip-bps", type=float, default=5.0, help="slippage per unit traded (default 5)")
    p.add_argument("--rf", type=float, default=0.0, help="annual return on cash, e.g. 0.04 (default 0)")
    p.add_argument("--breaker", type=float, help="also run each strategy with a drawdown circuit breaker at this percent")
    p.add_argument("--cooldown", type=int, default=30, help="days flat after the breaker trips (default 30)")
    p.add_argument("--csv", help="write daily equity of every strategy to this CSV")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if not args.symbols and not args.basket:
        p.error("give at least one symbol, or --basket")
    to_ts = lambda d: dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp()  # noqa: E731
    periods = 252 if args.asset == "stock" else 365
    cfg = {"cost": (args.fee_bps + args.slip_bps) / 1e4, "start_ts": to_ts(args.start), "rf": args.rf,
           "rf_period": (1 + args.rf) ** (1 / periods) - 1, "breaker": args.breaker / 100 if args.breaker else None,
           "cooldown": args.cooldown, "split_ts": to_ts(args.split) if args.split else None, "split": args.split}
    chosen = set(args.strategies.split(",")) if args.strategies else None
    results, texts = {}, []
    for sym in [s.upper() for s in args.symbols]:
        names = [k for k in SINGLE if not chosen or k in chosen or k == "hold"]
        try:
            s, src = load(sym, args.asset)
            results[sym] = run_single(s, src, names, cfg, args.grid)
            texts.append(render(sym, results[sym], cfg))
        except (FetchError, ValueError) as e:
            print(f"skipped {sym}: {e}", file=sys.stderr)
    if args.basket:
        names = [k for k in BASKET if not chosen or k in chosen or k == "basket"]
        series = {}
        for sym in [s.upper() for s in args.basket]:
            try:
                series[sym] = load(sym, args.asset)
            except FetchError as e:
                print(f"left {sym} out of the basket: {e}", file=sys.stderr)
        if len(series) >= 2:
            label = "basket of " + ", ".join(series)
            try:
                results[label] = run_basket(series, names, cfg, args.grid)
                texts.append(render(label, results[label], cfg))
            except ValueError as e:
                print(f"basket skipped: {e}", file=sys.stderr)
    if not results:
        sys.exit("nothing to test")
    if args.csv:
        write_equity(args.csv, results)
    if args.json:
        print(json.dumps({k: strip(v) for k, v in results.items()}, indent=2, default=str))
        return
    singles = {k: v for k, v in results.items() if not k.startswith("basket")}
    if len(singles) > 1:
        texts.append(render_summary(singles))
    print("\n\n".join(texts))
    print("\nResearch on past data with modelled costs, not a forecast. A strategy that beat holding on Sharpe "
          "or drawdown but not on CAGR traded return for a smoother ride; say which one it bought.")


if __name__ == "__main__":
    main()
