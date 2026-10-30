#!/usr/bin/env python3
"""
Technical analysis for crypto assets: multi-timeframe momentum, trend, key
levels, a support/resistance confluence map, and the historical record of
every signal that is firing now. Standard library only.

Usage:
  python technicals.py analyze BTC ETH          # full dossier per coin
  python technicals.py analyze HYPE --derivs    # add funding, OI, whales and the liquidation map
  python technicals.py scan BTC ETH SOL HYPE    # one line per coin
  python technicals.py levels BTC               # confluence map only
  python technicals.py backtest ETH             # every signal's track record on this coin
  python technicals.py cycle                    # BTC cycle gauges (Mayer, Pi cycle, 200W, 2Y MA, power law)
  python technicals.py seasonality BTC          # returns by calendar month
  python technicals.py analyze MSTR --asset stock   # stocks and ETFs work too (Yahoo Finance daily bars)
  add --json to any command for machine-readable output

What it computes (formulas match TradingView's built-ins; see ta_core.py):
  - RSI(14) on 4H, daily, 3D, weekly and monthly bars, RSI against its own
    14-bar average, Stoch RSI(14,14,3,3), RSI divergences (pivot 5/5,
    5-60 bars apart, the TradingView default)
  - MACD(12,26,9) on 4H, daily, weekly and monthly bars: crosses, zero line,
    histogram turns
  - Period anchors: yearly, quarterly, monthly, weekly and daily opens; the
    prior year's close, high and low; prior quarter, month, week and day
    high/low/close
  - Classic, Fibonacci and Camarilla pivots from the prior day, week, month,
    quarter and year
  - Moving averages: 20/50/100/200-day SMA, 21-day EMA, 20-week SMA and
    21-week EMA (the bull market support band), 50-week and 200-week SMA
  - Ichimoku (default 9/26/52/26 and crypto 20/60/120/30) on daily and
    weekly bars; Bollinger Bands with a squeeze reading; ATR
  - Anchored VWAPs (year to date, 90-day rolling, from the 180-day swing
    high and low) and a volume profile (point of control, value area)
  - Swing structure, Fibonacci retracements of the 180-day swing, Donchian
    breakout levels
  - A confluence map: every level above, clustered into zones and weighted,
    so you can see where several independent levels agree
  - Signals firing on the last closed bar, each with its own track record
    on this coin: how often it worked over the next 7/21/42 days (daily
    signals) or 4/8/13 weeks (weekly signals), a 90% lower bound on the win
    rate, and its edge over an average day

Signals and indicators use closed bars only; the forming bar is shown as the
live price. Nothing here is a trade instruction. Setups are conditional
(if X, then the next level is Y; the idea is wrong below Z) and every
backtest is in-sample on one coin's history, so treat a "proven" grade as a
reason to look closer, not as a forecast.
"""

import argparse
import datetime as dt
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
import ta_core as T  # noqa: E402
from market_data import FetchError, load_candles, forming, fetch  # noqa: E402

DAILY_HORIZONS, DAILY_PRIMARY, DAILY_COOLDOWN = (7, 21, 42), 21, 10
WEEKLY_HORIZONS, WEEKLY_PRIMARY, WEEKLY_COOLDOWN = (4, 8, 13), 8, 4
HISTORY_DAYS = 4000
HOURLY_DAYS = 60
PROVEN_MIN_N = 10
NOW = None  # set to an epoch time to analyze history as of that moment (plan_backtest.py does)


def now():
    return NOW if NOW is not None else time.time()

# Confluence weights: how much a level counts when it agrees with others.
W = {
    "yearly open": 3.0, "prior-year high": 2.5, "prior-year low": 2.5, "prior-year close": 1.0,
    "quarterly open": 2.0, "prior-quarter high": 2.0, "prior-quarter low": 2.0,
    "monthly open": 1.5, "prior-month high": 2.0, "prior-month low": 2.0,
    "weekly open": 1.0, "prior-week high": 1.5, "prior-week low": 1.5,
    "prior-day high": 0.75, "prior-day low": 0.75,
    "200D SMA": 2.5, "100D SMA": 1.0, "50D SMA": 1.5, "20D SMA": 0.75, "21D EMA": 0.75,
    "20W SMA": 2.0, "21W EMA": 2.0, "50W SMA": 2.0, "200W SMA": 3.0,
    "YTD VWAP": 2.0, "90D VWAP": 1.5, "VWAP from swing low": 1.5, "VWAP from swing high": 1.5,
    "365D POC": 2.0, "90D POC": 1.5, "365D VAH": 1.0, "365D VAL": 1.0, "90D VAH": 0.75, "90D VAL": 0.75,
    "swing high": 1.5, "swing low": 1.5, "20D high": 1.0, "20D low": 1.0, "55D high": 1.25, "55D low": 1.25,
    "fib": 1.0, "daily kijun": 1.0, "daily cloud": 1.0, "weekly kijun": 1.5, "weekly cloud": 1.5,
    "liquidation cluster": 1.5, "whale liquidations": 1.0, "order book wall": 0.75,
}
PIVOT_W = {"D": 0.5, "W": 1.0, "M": 1.5, "Q": 2.0, "Y": 2.5}


# ---------------------------------------------------------------- data

def closed(series, rule):
    """Drop the last bar when its period hasn't ended (rule: '1d', 'W', 'M', 'Q', 'Y', '3D', '4H')."""
    if not series["t"]:
        return series
    if rule == "1d" and "forming_last" in series:  # stocks: Yahoo says whether the session is still open
        return T.slice_series(series, 0, -1) if series["forming_last"] and NOW is None else series
    t, now_ = series["t"][-1], now()
    if rule == "1d":
        end = t + 86400
    elif rule.endswith("H"):
        end = t + int(rule[:-1]) * 3600
    elif rule.endswith("D"):
        end = t + int(rule[:-1]) * 86400
    else:
        d = T.utc(t)
        if rule == "W":
            end = t + 7 * 86400
        elif rule == "M":
            end = int(dt.datetime(d.year + d.month // 12, d.month % 12 + 1, 1, tzinfo=dt.timezone.utc).timestamp())
        elif rule == "Q":
            m = d.month + 3
            end = int(dt.datetime(d.year + (m - 1) // 12, (m - 1) % 12 + 1, 1, tzinfo=dt.timezone.utc).timestamp())
        else:
            end = int(dt.datetime(d.year + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    return T.slice_series(series, 0, -1) if end > now_ else series


def load(symbol, days=HISTORY_DAYS, hourly=True, asset=None):
    d, src = load_candles(symbol, "1d", days, asset=asset)
    kind = "stock" if src == "Yahoo Finance" else "crypto"
    h = None
    if hourly and kind == "crypto":  # stock hours make 4-hour bars meaningless
        try:
            h, _ = load_candles(symbol, "1h", HOURLY_DAYS)
        except FetchError:
            h = None
    return {"symbol": symbol.upper(), "source": src, "daily": d, "hourly": h, "asset": kind,
            "live": d["c"][-1], "live_t": d["t"][-1], "forming": forming(d)}


# ---------------------------------------------------------------- per-timeframe indicators

def pack(s):
    c, h, l = s["c"], s["h"], s["l"]
    r = T.rsi(c)
    m, sig, hist = T.macd(c)
    k, d = T.stoch_rsi(c)
    mid, up, lo, pb, width = T.bollinger(c)
    return {"rsi": r, "rsi_ma": T._sma_skip(r, 14), "macd": m, "signal": sig, "hist": hist,
            "stoch_k": k, "stoch_d": d, "bb_mid": mid, "bb_up": up, "bb_lo": lo, "pct_b": pb, "bb_width": width,
            "atr": T.atr(h, l, c), "ema3": T.ema(c, 3), "ao": T.awesome(h, l)}


def macd_state(p, i=None):
    i = len(p["macd"]) - 1 if i is None else i
    m, s, h = p["macd"][i], p["signal"][i], p["hist"][i]
    if m is None or s is None:
        return None
    last_cross, kind = None, None
    for j in range(i, 0, -1):
        a, b = p["hist"][j], p["hist"][j - 1]
        if a is None or b is None:
            break
        if (a > 0) != (b > 0):
            last_cross, kind = i - j, "bullish" if a > 0 else "bearish"
            break
    streak = 0
    for j in range(i, 0, -1):
        if p["hist"][j] is None or p["hist"][j - 1] is None:
            break
        if (p["hist"][j] > p["hist"][j - 1]) == (h > p["hist"][i - 1]):
            streak += 1
        else:
            break
    return {"macd": m, "signal": s, "hist": h, "above_signal": m > s, "above_zero": m > 0,
            "hist_rising": h > p["hist"][i - 1] if p["hist"][i - 1] is not None else None, "hist_streak": streak,
            "bars_since_cross": last_cross, "last_cross": kind}


def tf_summary(s, name):
    if len(s["c"]) < 35:
        return {"tf": name, "bars": len(s["c"])}
    p = pack(s)
    i = len(s["c"]) - 1
    divs = [d for d in T.divergences(s["h"], s["l"], p["rsi"]) if d["confirmed"] >= i - 10]
    width_rank = T.percentile_rank(p["bb_width"][-120:], p["bb_width"][-1]) if p["bb_width"][-1] is not None else None
    return {
        "tf": name, "bars": len(s["c"]), "close": s["c"][-1], "bar_time": T.utc(s["t"][-1]).strftime("%Y-%m-%d %H:%M"),
        "rsi": p["rsi"][-1], "rsi_ma": p["rsi_ma"][-1], "rsi_prev": p["rsi"][-2],
        "stoch_k": p["stoch_k"][-1], "stoch_d": p["stoch_d"][-1],
        "macd": macd_state(p),
        "pct_b": p["pct_b"][-1], "bb_width_rank": width_rank,
        "squeeze": width_rank is not None and width_rank <= 20,
        "atr": p["atr"][-1],
        "rsi_regime": rsi_regime(p["rsi"][-60:]),
        "divergences": [{"kind": d["kind"], "bars_ago": i - d["confirmed"],
                         "from": T.utc(s["t"][d["from"]]).strftime("%Y-%m-%d"),
                         "to": T.utc(s["t"][d["to"]]).strftime("%Y-%m-%d")} for d in divs],
    }


def rsi_regime(window):
    """Cardwell-style ranges: bull markets hold RSI 40-80, bear markets 20-60."""
    vals = [v for v in window if v is not None]
    if len(vals) < 30:
        return None
    lo, hi = min(vals), max(vals)
    if lo >= 38 and hi >= 65:
        return "bull range (holding 40+)"
    if hi <= 62 and lo <= 35:
        return "bear range (capped near 60)"
    return "mixed"


def ichimoku_state(s, preset):
    t, k, b, disp = preset
    if len(s["c"]) < b + disp:
        return None
    ic = T.ichimoku(s["h"], s["l"], s["c"], t, k, b, disp)
    c, i = s["c"], len(s["c"]) - 1
    top, bot = max(ic["cloud_a"][i], ic["cloud_b"][i]), min(ic["cloud_a"][i], ic["cloud_b"][i])
    pos = "above cloud" if c[i] > top else "below cloud" if c[i] < bot else "inside cloud"
    tk_now = ic["tenkan"][i] > ic["kijun"][i]
    cross = None
    for j in range(i, max(i - 30, 1), -1):
        if (ic["tenkan"][j] > ic["kijun"][j]) != (ic["tenkan"][j - 1] > ic["kijun"][j - 1]):
            cross = {"kind": "bullish" if ic["tenkan"][j] > ic["kijun"][j] else "bearish", "bars_ago": i - j}
            break
    future_bull = ic["span_a"][i] > ic["span_b"][i]
    chikou_ok = c[i] > max(s["h"][i - ic["offset"]], c[i - ic["offset"]]) if i - ic["offset"] >= 0 else None
    strong_bull = pos == "above cloud" and tk_now and future_bull and chikou_ok
    strong_bear = pos == "below cloud" and not tk_now and not future_bull and chikou_ok is False
    e2e = None
    if pos == "inside cloud":
        e2e = f"inside the cloud: edge-to-edge target {'%.6g' % (top if c[i - 1] <= bot or c[i] > (top + bot) / 2 else bot)}"
    return {"preset": "%d/%d/%d/%d" % preset, "position": pos, "cloud_top": top, "cloud_bottom": bot,
            "tenkan": ic["tenkan"][i], "kijun": ic["kijun"][i], "tk_bullish": tk_now, "last_tk_cross": cross,
            "future_cloud": "bullish" if future_bull else "bearish", "chikou_clear": chikou_ok,
            "verdict": "STRONG BULL" if strong_bull else "STRONG BEAR" if strong_bear else pos,
            "e2e": e2e}


# ---------------------------------------------------------------- anchors, pivots, MAs, VWAP

def prior_period(s, rule, offset=1):
    """High, low, close and open of the period `offset` back from the current one (resampled from daily)."""
    r = T.resample(s, rule)
    if len(r["t"]) <= offset:
        return None
    i = len(r["t"]) - 1 - offset
    return {"open": r["o"][i], "high": r["h"][i], "low": r["l"][i], "close": r["c"][i],
            "start": T.utc(r["t"][i]).strftime("%Y-%m-%d")}


def anchors(d):
    out = {}
    for rule, name in (("Y", "year"), ("Q", "quarter"), ("M", "month"), ("W", "week")):
        cur, prev = prior_period(d, rule, 0), prior_period(d, rule, 1)
        out[name] = {"open": cur["open"] if cur else None, "open_date": cur["start"] if cur else None,
                     "high_so_far": cur["high"] if cur else None, "low_so_far": cur["low"] if cur else None,
                     "prior": prev}
    out["day"] = {"open": d["o"][-1], "prior": {"high": d["h"][-2], "low": d["l"][-2], "close": d["c"][-2],
                                                 "open": d["o"][-2], "start": T.utc(d["t"][-2]).strftime("%Y-%m-%d")}}
    return out


def pivots_all(anch):
    out = {}
    for code, name in (("D", "day"), ("W", "week"), ("M", "month"), ("Q", "quarter"), ("Y", "year")):
        pr = anch[name]["prior"]
        if pr:
            out[code] = T.pivot_points(pr["high"], pr["low"], pr["close"])
    return out


def moving_averages(dc, wc, live):
    c = dc["c"]
    out = {}
    for n in (20, 50, 100, 200):
        v = T.sma(c, n)
        if v[-1] is not None:
            out[f"{n}D SMA"] = {"value": v[-1], "slope_20d": T.slope_pct(v, 20) if n >= 50 else T.slope_pct(v, 5)}
    e = T.ema(c, 21)
    if e[-1] is not None:
        out["21D EMA"] = {"value": e[-1], "slope_20d": T.slope_pct(e, 5)}
    wcl = wc["c"]
    for label, arr, sl in (("20W SMA", T.sma(wcl, 20), 4), ("21W EMA", T.ema(wcl, 21), 4),
                           ("50W SMA", T.sma(wcl, 50), 4), ("200W SMA", T.sma(wcl, 200), 4)):
        if arr and arr[-1] is not None:
            out[label] = {"value": arr[-1], "slope_20d": T.slope_pct(arr, sl)}
    for v in out.values():
        v["price_vs"] = live / v["value"] - 1
    s50, s200 = T.sma(c, 50), T.sma(c, 200)
    cross = None
    for j in range(len(c) - 1, 0, -1):
        if None in (s50[j], s200[j], s50[j - 1], s200[j - 1]):
            break
        if (s50[j] > s200[j]) != (s50[j - 1] > s200[j - 1]):
            cross = {"kind": "golden cross" if s50[j] > s200[j] else "death cross",
                     "date": T.utc(dc["t"][j]).strftime("%Y-%m-%d")}
            break
    return out, cross


def vwaps(dc):
    h, l, c, v, t = dc["h"], dc["l"], dc["c"], dc["v"], dc["t"]
    out = {}
    year = T.utc(t[-1]).year
    ytd = next((i for i, x in enumerate(t) if T.utc(x).year == year), None)
    if ytd is not None:
        out["YTD VWAP"] = T.anchored_vwap(h, l, c, v, ytd)[-1]
    if len(c) >= 90:
        out["90D VWAP"] = T.rolling_vwap(h, l, c, v, 90)[-1]
    n = min(180, len(c))
    lo_i = min(range(len(c) - n, len(c)), key=lambda i: l[i])
    hi_i = max(range(len(c) - n, len(c)), key=lambda i: h[i])
    out["VWAP from swing low"] = T.anchored_vwap(h, l, c, v, lo_i)[-1]
    out["VWAP from swing high"] = T.anchored_vwap(h, l, c, v, hi_i)[-1]
    return out, {"swing_low": l[lo_i], "swing_low_date": T.utc(t[lo_i]).strftime("%Y-%m-%d"),
                 "swing_high": h[hi_i], "swing_high_date": T.utc(t[hi_i]).strftime("%Y-%m-%d"),
                 "up": lo_i < hi_i}


# ---------------------------------------------------------------- confluence

def collect_levels(a):
    """Every level from the analysis, as (price, label, weight)."""
    L = []
    an = a["anchors"]

    def add(p, label, w):
        if p is not None and p > 0:
            L.append((p, label, w))

    add(an["year"]["open"], "yearly open", W["yearly open"])
    if an["year"]["prior"]:
        add(an["year"]["prior"]["high"], "prior-year high", W["prior-year high"])
        add(an["year"]["prior"]["low"], "prior-year low", W["prior-year low"])
    for nm in ("quarter", "month", "week"):
        add(an[nm]["open"], f"{nm}ly open".replace("weekly open", "weekly open"), W.get(f"{nm}ly open", 1.0))
        if an[nm]["prior"]:
            add(an[nm]["prior"]["high"], f"prior-{nm} high", W[f"prior-{nm} high"])
            add(an[nm]["prior"]["low"], f"prior-{nm} low", W[f"prior-{nm} low"])
    add(an["day"]["prior"]["high"], "prior-day high", W["prior-day high"])
    add(an["day"]["prior"]["low"], "prior-day low", W["prior-day low"])
    for code, piv in a["pivots"].items():
        w = PIVOT_W[code]
        for k, v in piv["classic"].items():
            add(v, f"{code} pivot {k}", w * (1.0 if k in ("P", "R1", "S1") else 0.7))
        if code in ("D", "W"):
            for k in ("R3", "S3", "R4", "S4"):
                add(piv["camarilla"][k], f"{code} Camarilla {k}", w * 0.75)
    for k, v in a["moving_averages"].items():
        add(v["value"], k, W.get(k, 1.0))
    for k, v in a["vwaps"].items():
        add(v, k, W.get(k, 1.0))
    for span in ("365D", "90D"):
        vp = a["volume_profile"].get(span)
        if vp:
            add(vp["poc"], f"{span} POC", W[f"{span} POC"])
            add(vp["vah"], f"{span} VAH", W[f"{span} VAH"])
            add(vp["val"], f"{span} VAL", W[f"{span} VAL"])
    for p, kind, date in a["structure"]["recent_swings"]:
        add(p, f"swing {kind} {date}", W[f"swing {kind}"])
    for k, v in a["breakout_levels"].items():
        add(v, k, W[k])
    for k, v in a["fib"]["levels"].items():
        add(v, f"fib {k}", W["fib"] * (0.75 if k == "0.786" else 1.0))
    for tfname, key in (("daily", "D"), ("weekly", "W")):
        ic = a["ichimoku"].get(f"{key} default")
        if ic:
            add(ic["kijun"], f"{tfname} kijun", W[f"{tfname} kijun"])
            add(ic["cloud_top"], f"{tfname} cloud top", W[f"{tfname} cloud"])
            add(ic["cloud_bottom"], f"{tfname} cloud bottom", W[f"{tfname} cloud"])
    for lv in a.get("extra_levels", []):
        add(lv["price"], lv["label"], lv["weight"])
    return L


def confluence(levels, price, atr, max_dist=0.30):
    tol = max(0.0035, 0.35 * atr / price) if atr else 0.005
    pts = sorted((p, lab, w) for p, lab, w in levels if abs(p / price - 1) <= max_dist)
    zones, cur = [], []
    for p in pts:
        if cur and p[0] > cur[0][0] * (1 + tol):
            zones.append(cur)
            cur = []
        cur.append(p)
    if cur:
        zones.append(cur)
    out = []
    for z in zones:
        w = sum(x[2] for x in z)
        lo, hi = z[0][0], z[-1][0]
        mid = sum(x[0] * x[2] for x in z) / w
        side = "at price" if lo <= price <= hi else "resistance" if lo > price else "support"
        labels = sorted(z, key=lambda x: -x[2])
        out.append({"low": lo, "high": hi, "mid": mid, "weight": round(w, 2), "count": len(z), "side": side,
                    "distance": mid / price - 1, "levels": [f"{x[1]} {x[0]:,.6g}" for x in labels]})
    return out, tol


def key_zones(zones, side, n=4, min_weight=2.5):
    zs = [z for z in zones if z["side"] == side and z["weight"] >= min_weight]
    zs.sort(key=lambda z: abs(z["distance"]))
    return zs[:n]


# ---------------------------------------------------------------- signals and their track record

def _cross_up(a, b, i):
    bp, bn = (b, b) if not isinstance(b, list) else (b[i - 1], b[i])
    return None not in (a[i - 1], a[i], bp, bn) and a[i - 1] <= bp and a[i] > bn


def _cross_dn(a, b, i):
    bp, bn = (b, b) if not isinstance(b, list) else (b[i - 1], b[i])
    return None not in (a[i - 1], a[i], bp, bn) and a[i - 1] >= bp and a[i] < bn


def daily_signals(dc, wc):
    """Each signal: (key, label, direction, [bar indexes where it fired on the close])."""
    c, h, l, t = dc["c"], dc["h"], dc["l"], dc["t"]
    n = len(c)
    p = pack(dc)
    r, k, dd = p["rsi"], p["stoch_k"], p["stoch_d"]
    s20, s50, s200 = T.sma(c, 20), T.sma(c, 50), T.sma(c, 200)
    ic = T.ichimoku(h, l, c)
    top = [max(a, b) if a is not None and b is not None else None for a, b in zip(ic["cloud_a"], ic["cloud_b"])]
    bot = [min(a, b) if a is not None and b is not None else None for a, b in zip(ic["cloud_a"], ic["cloud_b"])]
    width_rank = [None] * n
    for i in range(140, n):
        if p["bb_width"][i] is not None:
            width_rank[i] = T.percentile_rank(p["bb_width"][i - 120:i], p["bb_width"][i])
    yopen, year_idx = [None] * n, [0] * n
    cur_year, cur_open, cnt = None, None, 0
    for i in range(n):
        y = T.utc(t[i]).year
        if y != cur_year:
            cur_year, cur_open, cnt = y, dc["o"][i], 0
        yopen[i], year_idx[i] = cur_open, cnt
        cnt += 1
    # 200-week SMA mapped onto daily bars (last closed week before the bar)
    w200 = T.sma(wc["c"], 200)
    wmap, j = [None] * n, -1
    for i in range(n):
        while j + 1 < len(wc["t"]) and wc["t"][j + 1] + 7 * 86400 <= t[i] + 86400:
            j += 1
        wmap[i] = w200[j] if j >= 0 else None
    divs = T.divergences(h, l, r)
    div_at = {}
    for d in divs:
        div_at.setdefault(d["kind"], set()).add(d["confirmed"])

    def first_in(cond, gap=60):
        out, lastf = [], -10 ** 9
        for i in range(1, n):
            if cond(i):
                if i - lastf > gap:
                    out.append(i)
                lastf = i
        return out

    def hist_turn_up(i):
        hs = p["hist"]
        return (i >= 4 and None not in hs[i - 4:i + 1] and hs[i] < 0 and hs[i] > hs[i - 1]
                and hs[i - 1] < hs[i - 2] < hs[i - 3])

    def hist_turn_dn(i):
        hs = p["hist"]
        return (i >= 4 and None not in hs[i - 4:i + 1] and hs[i] > 0 and hs[i] < hs[i - 1]
                and hs[i - 1] > hs[i - 2] > hs[i - 3])

    def squeeze_before(i):
        return any(width_rank[j] is not None and width_rank[j] <= 20 for j in range(max(0, i - 5), i))

    S = [
        ("rsi_reclaim_30", "Daily RSI crosses back above 30", 1, lambda i: _cross_up(r, 30, i)),
        ("rsi_below_25", "Daily RSI drops below 25 (capitulation)", 1, lambda i: _cross_dn(r, 25, i)),
        ("rsi_lose_70", "Daily RSI falls back below 70", -1, lambda i: _cross_dn(r, 70, i)),
        ("rsi_ma_up_below_50", "Daily RSI crosses above its 14-day average while under 50", 1,
         lambda i: _cross_up(r, p["rsi_ma"], i) and r[i] < 50),
        ("rsi_ma_dn_above_50", "Daily RSI crosses below its 14-day average while over 50", -1,
         lambda i: _cross_dn(r, p["rsi_ma"], i) and r[i] > 50),
        ("stoch_up_oversold", "Stoch RSI K crosses above D under 20", 1,
         lambda i: _cross_up(k, dd, i) and k[i] is not None and k[i] < 20),
        ("stoch_dn_overbought", "Stoch RSI K crosses below D over 80", -1,
         lambda i: _cross_dn(k, dd, i) and k[i] is not None and k[i] > 80),
        ("macd_up", "Daily MACD crosses above its signal", 1, lambda i: _cross_up(p["macd"], p["signal"], i)),
        ("macd_up_below_zero", "Daily MACD bullish cross below zero", 1,
         lambda i: _cross_up(p["macd"], p["signal"], i) and p["macd"][i] < 0),
        ("macd_dn", "Daily MACD crosses below its signal", -1, lambda i: _cross_dn(p["macd"], p["signal"], i)),
        ("macd_dn_above_zero", "Daily MACD bearish cross above zero", -1,
         lambda i: _cross_dn(p["macd"], p["signal"], i) and p["macd"][i] > 0),
        ("macd_hist_turn_up", "MACD histogram turns up below zero (momentum shift)", 1, hist_turn_up),
        ("macd_hist_turn_dn", "MACD histogram turns down above zero (momentum fade)", -1, hist_turn_dn),
        ("golden_cross", "Golden cross (50D over 200D)", 1, lambda i: _cross_up(s50, s200, i)),
        ("death_cross", "Death cross (50D under 200D)", -1, lambda i: _cross_dn(s50, s200, i)),
        ("reclaim_200d", "Close back above the 200D SMA", 1, lambda i: _cross_up(c, s200, i)),
        ("lose_200d", "Close below the 200D SMA", -1, lambda i: _cross_dn(c, s200, i)),
        ("reclaim_yearly_open", "Close back above the yearly open", 1,
         lambda i: year_idx[i] >= 5 and _cross_up(c, yopen, i)),
        ("lose_yearly_open", "Close below the yearly open", -1,
         lambda i: year_idx[i] >= 5 and _cross_dn(c, yopen, i)),
        ("donchian20_up", "Close above the prior 20-day high (breakout)", 1,
         lambda i: i >= 20 and c[i] > max(h[i - 20:i]) and c[i - 1] <= max(h[i - 21:i - 1] or [c[i - 1]])),
        ("donchian20_dn", "Close below the prior 20-day low (breakdown)", -1,
         lambda i: i >= 20 and c[i] < min(l[i - 20:i]) and c[i - 1] >= min(l[i - 21:i - 1] or [c[i - 1]])),
        ("donchian55_up", "Close above the prior 55-day high", 1,
         lambda i: i >= 56 and c[i] > max(h[i - 55:i]) and c[i - 1] <= max(h[i - 56:i - 1])),
        ("donchian55_dn", "Close below the prior 55-day low", -1,
         lambda i: i >= 56 and c[i] < min(l[i - 55:i]) and c[i - 1] >= min(l[i - 56:i - 1])),
        ("squeeze_break_up", "Bollinger squeeze resolves up (close above upper band)", 1,
         lambda i: squeeze_before(i) and _cross_up(c, p["bb_up"], i)),
        ("squeeze_break_dn", "Bollinger squeeze resolves down (close below lower band)", -1,
         lambda i: squeeze_before(i) and _cross_dn(c, p["bb_lo"], i)),
        ("washout", "Close below lower Bollinger Band with RSI under 30", 1,
         lambda i: None not in (p["bb_lo"][i], r[i]) and c[i] < p["bb_lo"][i] and r[i] < 30
         and not (c[i - 1] < (p["bb_lo"][i - 1] or 0) and (r[i - 1] or 50) < 30)),
        ("rsi_bull_div", "Regular bullish RSI divergence confirmed", 1, lambda i: i in div_at.get("regular bullish", ())),
        ("rsi_bear_div", "Regular bearish RSI divergence confirmed", -1, lambda i: i in div_at.get("regular bearish", ())),
        ("rsi_hidden_bull", "Hidden bullish RSI divergence (trend continuation)", 1,
         lambda i: i in div_at.get("hidden bullish", ())),
        ("rsi_hidden_bear", "Hidden bearish RSI divergence (trend continuation)", -1,
         lambda i: i in div_at.get("hidden bearish", ())),
        ("cloud_break_up", "Daily close breaks above the Ichimoku cloud", 1, lambda i: _cross_up(c, top, i)),
        ("cloud_break_dn", "Daily close breaks below the Ichimoku cloud", -1, lambda i: _cross_dn(c, bot, i)),
        ("tk_bull_above_cloud", "Bullish TK cross above the cloud", 1,
         lambda i: _cross_up(ic["tenkan"], ic["kijun"], i) and top[i] is not None and c[i] > top[i]),
        ("tk_bear_below_cloud", "Bearish TK cross below the cloud", -1,
         lambda i: _cross_dn(ic["tenkan"], ic["kijun"], i) and bot[i] is not None and c[i] < bot[i]),
        ("bbawe_buy", "BBAWE buy: 3-EMA crosses above the BB basis with AO rising", 1,
         lambda i: _cross_up(p["ema3"], p["bb_mid"], i) and None not in (p["ao"][i], p["ao"][i - 1])
         and p["ao"][i] > p["ao"][i - 1]),
        ("bbawe_sell", "BBAWE sell: 3-EMA crosses below the BB basis with AO falling", -1,
         lambda i: _cross_dn(p["ema3"], p["bb_mid"], i) and None not in (p["ao"][i], p["ao"][i - 1])
         and p["ao"][i] < p["ao"][i - 1]),
        ("near_200w", "Price within 3% of (or below) the 200-week SMA", 1, None),
        ("mayer_below_0_8", "Price under 0.8x its 200D SMA (Mayer multiple)", 1, None),
    ]
    out = []
    for key, label, direction, cond in S:
        if key == "near_200w":
            idx = first_in(lambda i: wmap[i] is not None and l[i] <= wmap[i] * 1.03)
        elif key == "mayer_below_0_8":
            idx = first_in(lambda i: s200[i] is not None and c[i] < 0.8 * s200[i])
        else:
            idx = [i for i in range(1, n) if cond(i)]
        out.append((key, label, direction, idx))
    return out


def weekly_signals(wc):
    c, h, l = wc["c"], wc["h"], wc["l"]
    p = pack(wc)
    e21 = T.ema(c, 21)
    ic = T.ichimoku(h, l, c)
    top = [max(a, b) if a is not None and b is not None else None for a, b in zip(ic["cloud_a"], ic["cloud_b"])]
    bot = [min(a, b) if a is not None and b is not None else None for a, b in zip(ic["cloud_a"], ic["cloud_b"])]
    S = [
        ("w_macd_up", "Weekly MACD crosses above its signal", 1, lambda i: _cross_up(p["macd"], p["signal"], i)),
        ("w_macd_dn", "Weekly MACD crosses below its signal", -1, lambda i: _cross_dn(p["macd"], p["signal"], i)),
        ("w_rsi_up_50", "Weekly RSI crosses above 50", 1, lambda i: _cross_up(p["rsi"], 50, i)),
        ("w_rsi_dn_50", "Weekly RSI crosses below 50", -1, lambda i: _cross_dn(p["rsi"], 50, i)),
        ("w_reclaim_21w_ema", "Weekly close back above the 21-week EMA", 1, lambda i: _cross_up(c, e21, i)),
        ("w_lose_21w_ema", "Weekly close below the 21-week EMA", -1, lambda i: _cross_dn(c, e21, i)),
        ("w_cloud_break_up", "Weekly close breaks above the Ichimoku cloud", 1, lambda i: _cross_up(c, top, i)),
        ("w_cloud_break_dn", "Weekly close breaks below the Ichimoku cloud", -1, lambda i: _cross_dn(c, bot, i)),
        ("w_rsi_under_30", "Weekly RSI under 30", 1, lambda i: _cross_dn(p["rsi"], 30, i)),
    ]
    return [(k, lab, d, [i for i in range(1, len(c)) if cond(i)]) for k, lab, d, cond in S]


def grade_signal(series, idx, direction, horizons, primary, cooldown, bench=None, recent_bars=3):
    """Forward-return record of one signal. bench: aligned benchmark closes (same timestamps) or None."""
    c, h, l = series["c"], series["h"], series["l"]
    n = len(c)
    kept, lastf = [], -10 ** 9
    for i in idx:
        if i - lastf >= cooldown:
            kept.append(i)
        lastf = i
    res = {"fired": len(kept), "last": T.utc(series["t"][kept[-1]]).strftime("%Y-%m-%d") if kept else None,
           "bars_since_last": n - 1 - kept[-1] if kept else None,
           "active": bool(kept) and n - 1 - kept[-1] < recent_bars}
    warm = 50
    for hz in horizons:
        rets, mae, rel = [], [], []
        for i in kept:
            if i + hz >= n:
                continue
            fr = c[i + hz] / c[i] - 1
            rets.append(direction * fr)
            if direction > 0:
                mae.append(min(l[i + 1:i + hz + 1]) / c[i] - 1)
            else:
                mae.append(1 - max(h[i + 1:i + hz + 1]) / c[i])
            if bench and bench[i] and bench[i + hz]:
                rel.append(direction * (fr - (bench[i + hz] / bench[i] - 1)))
        base = [direction * (c[i + hz] / c[i] - 1) for i in range(warm, n - hz)]
        wins = sum(x > 0 for x in rets)
        res[hz] = {"n": len(rets), "win_rate": wins / len(rets) if rets else None,
                   "wilson_lb": T.wilson_lower(wins, len(rets)) if rets else None,
                   "mean": sum(rets) / len(rets) if rets else None, "median": T.median(rets),
                   "baseline_mean": sum(base) / len(base) if base else None,
                   "baseline_win": sum(x > 0 for x in base) / len(base) if base else None,
                   "mae_median": T.median(mae),
                   "beat_bench": (sum(x > 0 for x in rel) / len(rel)) if rel else None,
                   "rel_mean": (sum(rel) / len(rel)) if rel else None}
        if res[hz]["mean"] is not None and res[hz]["baseline_mean"] is not None:
            res[hz]["edge"] = res[hz]["mean"] - res[hz]["baseline_mean"]
    pr = res[primary]
    if pr["n"] < 5:
        g = "too few"
    elif pr["n"] >= PROVEN_MIN_N and pr["wilson_lb"] >= 0.5 and (pr.get("edge") or 0) > 0:
        g = "proven"
    elif pr["win_rate"] > 0.5 and (pr.get("edge") or 0) > 0:
        g = "positive, unproven"
    else:
        g = "coin-flip or worse"
    res["grade"] = g
    open_now = [i for i in kept if i + primary >= n]
    res["open"] = [{"date": T.utc(series["t"][i]).strftime("%Y-%m-%d"),
                    "return_so_far": direction * (c[-1] / c[i] - 1)} for i in open_now[-3:]]
    return res


def align(bench_series, series):
    m = dict(zip(bench_series["t"], bench_series["c"]))
    return [m.get(t) for t in series["t"]]


def backtest(dc, wc, bench_d=None, bench_w=None):
    out = []
    bd = align(bench_d, dc) if bench_d else None
    bw = align(bench_w, wc) if bench_w else None
    for key, label, direction, idx in daily_signals(dc, wc):
        g = grade_signal(dc, idx, direction, DAILY_HORIZONS, DAILY_PRIMARY, DAILY_COOLDOWN, bd)
        out.append({"key": key, "label": label, "direction": "bullish" if direction > 0 else "bearish",
                    "timeframe": "1D", "primary": DAILY_PRIMARY, **g})
    for key, label, direction, idx in weekly_signals(wc):
        g = grade_signal(wc, idx, direction, WEEKLY_HORIZONS, WEEKLY_PRIMARY, WEEKLY_COOLDOWN, bw, recent_bars=2)
        out.append({"key": key, "label": label, "direction": "bullish" if direction > 0 else "bearish",
                    "timeframe": "1W", "primary": WEEKLY_PRIMARY, **g})
    return out


# ---------------------------------------------------------------- seasonality and cycle

def seasonality(d, live):
    m = T.resample(d, "M")
    rets = {}
    for i in range(1, len(m["c"])):
        mo = T.utc(m["t"][i]).month
        if i == len(m["c"]) - 1 and closed(m, "M") is not m:
            continue
        rets.setdefault(mo, []).append(m["c"][i] / m["c"][i - 1] - 1)
    table = []
    for mo in range(1, 13):
        r = rets.get(mo, [])
        table.append({"month": dt.date(2000, mo, 1).strftime("%b"), "n": len(r),
                      "mean": sum(r) / len(r) if r else None, "median": T.median(r),
                      "positive": sum(x > 0 for x in r) / len(r) if r else None})
    now = T.utc(d["t"][-1])
    return {"table": table, "current_month": now.strftime("%b"), "mtd": live / m["o"][-1] - 1,
            "next_month": (now.replace(day=1) + dt.timedelta(days=32)).strftime("%b")}


def cycle_gauges(dc, wc, live):
    c = dc["c"]
    s200, s111, s350, s730 = T.sma(c, 200), T.sma(c, 111), T.sma(c, 350), T.sma(c, 730)
    mayer_hist = [x / m for x, m in zip(c, s200) if m]
    w200 = T.sma(wc["c"], 200)
    ath_i = max(range(len(dc["h"])), key=lambda i: dc["h"][i])
    genesis = dt.datetime(2009, 1, 3, tzinfo=dt.timezone.utc).timestamp()
    xs = [math.log10((t - genesis) / 86400) for t in dc["t"]]
    ys = [math.log10(x) for x in c]
    nx = len(xs)
    mx, my = sum(xs) / nx, sum(ys) / nx
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(xs, ys)]
    sd = math.sqrt(sum(r * r for r in res) / nx)
    x_now = math.log10((now() - genesis) / 86400)
    fair = 10 ** (a + b * x_now)
    z_now = (math.log10(live) - (a + b * x_now)) / sd
    halving = dt.datetime(2024, 4, 20, tzinfo=dt.timezone.utc)
    return {
        "mayer_multiple": live / s200[-1] if s200[-1] else None,
        "mayer_percentile": T.percentile_rank(mayer_hist, live / s200[-1]) if s200[-1] else None,
        "pi_cycle_ratio": s111[-1] / (2 * s350[-1]) if s350[-1] else None,
        "two_year_ma": s730[-1], "two_year_ma_x5": s730[-1] * 5 if s730[-1] else None,
        "price_vs_2y_ma": live / s730[-1] if s730[-1] else None,
        "200w_sma": w200[-1], "price_vs_200w": live / w200[-1] if w200[-1] else None,
        "ath": dc["h"][ath_i], "ath_date": T.utc(dc["t"][ath_i]).strftime("%Y-%m-%d"),
        "drawdown_from_ath": live / dc["h"][ath_i] - 1,
        "days_since_halving": int((now() - halving.timestamp()) // 86400),
        "power_law": {"fair_value": fair, "band_minus_1sd": fair * 10 ** -sd, "band_plus_1sd": fair * 10 ** sd,
                      "band_plus_2sd": fair * 10 ** (2 * sd), "z": z_now, "slope": b,
                      "fit_from": T.utc(dc["t"][0]).strftime("%Y-%m-%d")},
    }


# ---------------------------------------------------------------- scoring and setups

def score(a):
    ma, d, w = a["moving_averages"], a["tf"]["1D"], a["tf"]["1W"]
    live = a["live"]
    trend, why_t = 0, []

    def tick(cond, pos, neg, bucket):
        nonlocal trend
        if cond is None:
            return 0
        v = 1 if cond else -1
        bucket.append(pos if cond else neg)
        return v

    trend += tick(live > ma["200D SMA"]["value"] if "200D SMA" in ma else None, "above 200D", "below 200D", why_t)
    if "50D SMA" in ma and "200D SMA" in ma:
        trend += tick(ma["50D SMA"]["value"] > ma["200D SMA"]["value"], "50D over 200D", "50D under 200D", why_t)
    trend += tick(live > ma["21W EMA"]["value"] if "21W EMA" in ma else None,
                  "above bull-market band", "below bull-market band", why_t)
    icw = a["ichimoku"].get("W default")
    if icw and icw["position"] != "inside cloud":
        trend += tick(icw["position"] == "above cloud", "above weekly cloud", "below weekly cloud", why_t)
    st = a["structure"]["label"]
    if st.startswith("uptrend") or st.startswith("downtrend"):
        trend += tick(st.startswith("uptrend"), "higher highs/lows", "lower highs/lows", why_t)
    mom, why_m = 0, []

    def mtick(cond, pos, neg):
        nonlocal mom
        if cond is None:
            return
        mom += 1 if cond else -1
        why_m.append(pos if cond else neg)

    if d.get("rsi") is not None:
        mtick(d["rsi"] > 50, "daily RSI > 50", "daily RSI < 50")
    if d.get("macd"):
        mtick(d["macd"]["above_signal"], "daily MACD > signal", "daily MACD < signal")
        mtick(d["macd"]["hist_rising"], "daily histogram rising", "daily histogram falling")
    if w.get("rsi") is not None:
        mtick(w["rsi"] > 50, "weekly RSI > 50", "weekly RSI < 50")
    if w.get("macd"):
        mtick(w["macd"]["above_signal"], "weekly MACD > signal", "weekly MACD < signal")
    if trend >= 3 and mom >= 2:
        stance = "Strong uptrend"
    elif trend >= 3 and mom <= -1:
        stance = "Uptrend, pulling back"
    elif trend >= 2:
        stance = "Uptrend, momentum mixed"
    elif trend <= -3 and mom <= -2:
        stance = "Strong downtrend"
    elif trend <= -3 and mom >= 1:
        stance = "Downtrend, bouncing"
    elif trend <= -2:
        stance = "Downtrend, momentum mixed"
    else:
        stance = "Transition or range"
    flags = []
    if d.get("rsi") is not None and d["rsi"] < 30:
        flags.append("daily RSI oversold")
    if d.get("rsi") is not None and d["rsi"] > 70:
        flags.append("daily RSI overbought")
    if w.get("rsi") is not None and w["rsi"] < 30:
        flags.append("weekly RSI oversold")
    if w.get("rsi") is not None and w["rsi"] > 70:
        flags.append("weekly RSI overbought")
    if d.get("squeeze"):
        flags.append("daily Bollinger squeeze")
    if d.get("pct_b") is not None and d["pct_b"] < 0:
        flags.append("closed below lower Bollinger Band")
    for dv in d.get("divergences", []) + w.get("divergences", []):
        flags.append(f"{dv['kind']} RSI divergence ({dv['to']})")
    return {"trend": trend, "trend_max": 5, "momentum": mom, "momentum_max": 5, "stance": stance,
            "trend_reasons": why_t, "momentum_reasons": why_m, "flags": flags}


def setups(a):
    live, atr = a["live"], a["tf"]["1D"].get("atr") or 0
    sup, res = key_zones(a["zones"], "support", 3), key_zones(a["zones"], "resistance", 3)
    at = [z for z in a["zones"] if z["side"] == "at price" and z["weight"] >= 2.5]
    stance = a["score"]["stance"]
    counter = stance.startswith("Downtrend") or stance.startswith("Strong downtrend")
    plans = []
    if sup and res:
        s1, r1 = sup[0], res[0]
        stop = s1["low"] - 0.5 * atr
        entry = s1["mid"]
        rr = (r1["mid"] - entry) / (entry - stop) if entry > stop else None
        plans.append({
            "name": "Pullback long into support" + (" (counter-trend)" if counter else ""),
            "condition": f"price holds {s1['low']:,.6g}-{s1['high']:,.6g} on a daily close"
                         + ("; wait for a reversal trigger first (daily MACD cross up or RSI back above 30)" if counter else ""),
            "entry": entry, "invalidation": stop, "target_1": r1["mid"],
            "target_2": res[1]["mid"] if len(res) > 1 else None, "reward_risk": rr})
        trig = r1["high"]
        stop_b = r1["low"] - 0.5 * atr
        tgt = res[1]["mid"] if len(res) > 1 else trig + 2 * atr
        plans.append({
            "name": "Breakout through resistance",
            "condition": f"daily close above {trig:,.6g} on volume at least 1.5x the 20-day average, "
                         "RSI under 75, ideally a weekly close above it too; a close back inside the zone "
                         "means the breakout failed",
            "entry": trig, "invalidation": stop_b, "target_1": tgt,
            "target_2": res[2]["mid"] if len(res) > 2 else None,
            "reward_risk": (tgt - trig) / (trig - stop_b) if trig > stop_b else None})
        plans.append({
            "name": "Breakdown (defensive)",
            "condition": f"daily close below {s1['low']:,.6g}: the next support is "
                         + (f"{sup[1]['low']:,.6g}-{sup[1]['high']:,.6g}" if len(sup) > 1 else "not mapped within 30%"),
            "entry": s1["low"], "invalidation": s1["high"] + 0.5 * atr,
            "target_1": sup[1]["mid"] if len(sup) > 1 else None, "target_2": sup[2]["mid"] if len(sup) > 2 else None,
            "reward_risk": ((s1["low"] - sup[1]["mid"]) / (s1["high"] + 0.5 * atr - s1["low"]))
            if len(sup) > 1 else None})
    return {"supports": sup, "resistances": res, "at_price": at, "plans": plans}


# ---------------------------------------------------------------- analysis

def analyze(symbol, with_derivs=False, bench=None, data=None, asset=None):
    """Full analysis. `data` (the dict load() returns) skips fetching, e.g. for a history slice.
    asset: "crypto", "stock" or None to detect (Yahoo Finance as the source means a stock)."""
    data = data or load(symbol, asset=asset)
    kind = data.get("asset") or ("stock" if data.get("source") == "Yahoo Finance" else "crypto")
    if kind == "stock":
        with_derivs = False  # no perps, funding or liquidation data for stocks
    d = data["daily"]
    live = data["live"]
    dc = closed(d, "1d")
    wk = T.resample(d, "W")
    wc, mc, d3 = closed(wk, "W"), closed(T.resample(d, "M"), "M"), closed(T.resample(d, "3D"), "3D")
    a = {"symbol": data["symbol"], "source": data["source"], "live": live, "asset": kind,
         "as_of": T.utc(now()).strftime("%Y-%m-%d %H:%M UTC"),
         "history_from": T.utc(d["t"][0]).strftime("%Y-%m-%d"), "daily_bars": len(dc["c"])}
    a["change"] = {k: live / dc["c"][-n] - 1 if len(dc["c"]) >= n else None for k, n in (("1d", 1), ("7d", 7), ("30d", 30), ("90d", 90))}
    tf = {}
    if data["hourly"]:
        h4 = closed(T.resample(data["hourly"], "4H"), "4H")
        tf["4H"] = tf_summary(h4, "4H")
    tf["1D"] = tf_summary(dc, "1D")
    tf["3D"] = tf_summary(d3, "3D")
    tf["1W"] = tf_summary(wc, "1W")
    tf["1M"] = tf_summary(mc, "1M")
    a["tf"] = tf
    a["anchors"] = anchors(d)
    a["pivots"] = pivots_all(a["anchors"])
    a["moving_averages"], a["ma_cross"] = moving_averages(dc, wc, live)
    a["vwaps"], swing = vwaps(dc)
    a["fib"] = {"swing": swing, "levels": T.fib_retracements(swing["swing_low"], swing["swing_high"], swing["up"])}
    a["volume_profile"] = {}
    for span in (90, 365):
        if len(dc["c"]) >= span:
            vp = T.volume_profile(dc["h"][-span:], dc["l"][-span:], dc["v"][-span:])
            if vp:
                a["volume_profile"][f"{span}D"] = {k: vp[k] for k in ("poc", "vah", "val")}
    ms = T.market_structure(dc["h"], dc["l"])
    recent = [(dc["h"][i], "high", T.utc(dc["t"][i]).strftime("%Y-%m-%d")) for i in ms["swing_highs"][-3:]
              if i >= len(dc["c"]) - 150]
    recent += [(dc["l"][i], "low", T.utc(dc["t"][i]).strftime("%Y-%m-%d")) for i in ms["swing_lows"][-3:]
               if i >= len(dc["c"]) - 150]
    a["structure"] = {"label": ms["label"], "recent_swings": recent}
    a["breakout_levels"] = {}
    if len(dc["c"]) >= 56:
        a["breakout_levels"] = {"20D high": max(dc["h"][-20:]), "20D low": min(dc["l"][-20:]),
                                "55D high": max(dc["h"][-55:]), "55D low": min(dc["l"][-55:])}
    a["ichimoku"] = {}
    for key, s in (("D", dc), ("W", wc)):
        for pname, preset in (("default", (9, 26, 52, 26)), ("crypto", (20, 60, 120, 30))):
            st = ichimoku_state(s, preset)
            if st:
                a["ichimoku"][f"{key} {pname}"] = st
    vol20 = sum(dc["v"][-21:-1]) / 20 if len(dc["v"]) > 21 else None
    a["volume"] = {"rel_volume": dc["v"][-1] / vol20 if vol20 else None,
                   "obv_slope_20d": (lambda o: (o[-1] - o[-21]) / (abs(o[-21]) or 1))(T.obv(dc["c"], dc["v"]))
                   if len(dc["c"]) > 21 else None}
    a["extra_levels"] = []
    if with_derivs:
        try:
            import derivatives as D
            a["derivatives"] = D.snapshot(a["symbol"], live, hourly=data["hourly"])
            a["extra_levels"] = a["derivatives"].get("levels", [])
        except Exception as e:  # derivatives are optional context; the chart analysis stands on its own
            a["derivatives"] = {"error": str(e)}
    a["zones"], a["zone_tolerance"] = confluence(collect_levels(a), live, tf["1D"].get("atr"))
    a["score"] = score(a)
    a["setups"] = setups(a)
    bd = bw = None
    if bench is not None and bench["symbol"] != a["symbol"] and kind == "crypto":
        bd, bw = bench["dc"], bench["wc"]
    bt = backtest(dc, wc, bd, bw)
    a["signals_now"] = [s for s in bt if s["active"]]
    a["backtest"] = bt
    a["seasonality"] = seasonality(d, live)
    a["_series"] = {"dc": dc, "wc": wc}
    return a


# ---------------------------------------------------------------- rendering

def f(x, nd=6):
    if x is None:
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:,.{nd}g}"


def pc(x, nd=1):
    return "n/a" if x is None else f"{x * 100:+.{nd}f}%"


def rate(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def r1(x):
    return "n/a" if x is None else f"{x:.1f}"


def render_signal_rows(sigs, bench_name=None):
    lines = ["| Signal | TF | Last fired | Dir | n | Win rate | 90% floor | Median | Edge vs any day | Typical drawdown | Grade |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in sigs:
        pr = s[s["primary"]]
        hz = f"{s['primary']}{'d' if s['timeframe'] == '1D' else 'w'}"
        lines.append(f"| {s['label']} | {s['timeframe']} | {s['last'] or '-'} | {s['direction']} | {pr['n']} "
                     f"| {rate(pr['win_rate'])} | {rate(pr['wilson_lb'])} "
                     f"| {pc(pr['median'])} | {pc(pr.get('edge'))} | {pc(pr['mae_median'])} | {s['grade']} ({hz}) |")
    return lines


def render(a):
    L = []
    sc = a["score"]
    d, w = a["tf"]["1D"], a["tf"]["1W"]
    atr = d.get("atr")
    L.append(f"## {a['symbol']} — technical dossier")
    L.append(f"*{a['as_of']} · candles: {a['source']} since {a['history_from']} · signals use closed bars*\n")
    L.append(f"**{sc['stance']}** · trend {sc['trend']:+d}/5 · momentum {sc['momentum']:+d}/5 · "
             f"live {f(a['live'])} · 1d {pc(a['change']['1d'])} · 7d {pc(a['change']['7d'])} · 30d {pc(a['change']['30d'])} · "
             f"ATR(14) {f(atr)} ({pc(atr / a['live'] if atr else None)})")
    L.append(f"- Trend: {', '.join(sc['trend_reasons'])}")
    L.append(f"- Momentum: {', '.join(sc['momentum_reasons'])}")
    if sc["flags"]:
        L.append(f"- Flags: {'; '.join(sc['flags'])}")
    L.append("")
    L.append("### Levels that agree (confluence map)")
    L.append("| Side | Zone | Distance | Strength | What's there |\n|---|---|---|---|---|")
    st = a["setups"]
    rows = list(reversed(st["resistances"])) + st["at_price"] + st["supports"]
    for z in rows:
        L.append(f"| {z['side']} | {f(z['low'])}-{f(z['high'])} | {pc(z['distance'])} | {z['weight']:.1f} ({z['count']}) "
                 f"| {'; '.join(z['levels'][:6])}{' …' if len(z['levels']) > 6 else ''} |")
    L.append(f"\nZones merge levels within {pc(a['zone_tolerance'], 2).lstrip('+')} of each other (0.35 ATR); "
             "strength is the sum of the levels' weights.\n")
    an = a["anchors"]
    L.append("### Period opens and prior ranges")
    L.append("| Anchor | Open | Price vs open | Prior high | Prior low | Prior close |\n|---|---|---|---|---|---|")
    for nm in ("year", "quarter", "month", "week", "day"):
        x = an[nm]
        pr = x["prior"] or {}
        L.append(f"| {nm.capitalize()} | {f(x['open'])} | {pc(a['live'] / x['open'] - 1 if x['open'] else None)} "
                 f"| {f(pr.get('high'))} | {f(pr.get('low'))} | {f(pr.get('close'))} |")
    L.append("\n### Pivot points (classic; Fibonacci and Camarilla in --json)")
    L.append("| From prior | S3 | S2 | S1 | P | R1 | R2 | R3 |\n|---|---|---|---|---|---|---|---|")
    for code, nm in (("D", "day"), ("W", "week"), ("M", "month"), ("Q", "quarter"), ("Y", "year")):
        p = a["pivots"].get(code)
        if p:
            c = p["classic"]
            L.append(f"| {nm} | {f(c['S3'])} | {f(c['S2'])} | {f(c['S1'])} | {f(c['P'])} | {f(c['R1'])} | {f(c['R2'])} | {f(c['R3'])} |")
    L.append("\n### Momentum by timeframe")
    L.append("| TF | RSI(14) | RSI vs its 14-bar MA | Stoch RSI K/D | MACD vs signal | Histogram | Last MACD cross | RSI regime |")
    L.append("|---|---|---|---|---|---|---|---|")
    for k in ("4H", "1D", "3D", "1W", "1M"):
        x = a["tf"].get(k)
        if not x or x.get("rsi") is None:
            continue
        m = x["macd"] or {}
        rel = "above" if x["rsi_ma"] is not None and x["rsi"] > x["rsi_ma"] else "below" if x["rsi_ma"] is not None else "n/a"
        cross = f"{m.get('last_cross')} {m.get('bars_since_cross')} bars ago" if m.get("last_cross") else "n/a"
        L.append(f"| {k} | {r1(x['rsi'])} | {rel} | {r1(x['stoch_k'])}/{r1(x['stoch_d'])} "
                 f"| {'above' if m.get('above_signal') else 'below'} ({'above' if m.get('above_zero') else 'below'} zero) "
                 f"| {'rising' if m.get('hist_rising') else 'falling'} {m.get('hist_streak', '')} bars | {cross} "
                 f"| {x.get('rsi_regime') or 'n/a'} |")
    divs = [(k, dv) for k in ("1D", "3D", "1W") for dv in a["tf"].get(k, {}).get("divergences", [])]
    if divs:
        L.append("\nDivergences confirmed in the last 10 bars: " +
                 "; ".join(f"{k} {dv['kind']} ({dv['from']} → {dv['to']})" for k, dv in divs))
    L.append("\n### Trend")
    L.append("| Average | Value | Price vs | Slope |\n|---|---|---|---|")
    for k, v in a["moving_averages"].items():
        L.append(f"| {k} | {f(v['value'])} | {pc(v['price_vs'])} | {pc(v['slope_20d'])} |")
    if a["ma_cross"]:
        L.append(f"\nLast 50/200 cross: {a['ma_cross']['kind']} on {a['ma_cross']['date']}.")
    for k, ic in a["ichimoku"].items():
        tk = ic["last_tk_cross"]
        tk_note = f" (crossed {tk['bars_ago']} bars ago)" if tk else ""
        e2e = f" · {ic['e2e']}" if ic["e2e"] else ""
        L.append(f"- Ichimoku {k} ({ic['preset']}): **{ic['verdict']}** · cloud {f(ic['cloud_bottom'])}-{f(ic['cloud_top'])} · "
                 f"future cloud {ic['future_cloud']} · TK {'bullish' if ic['tk_bullish'] else 'bearish'}{tk_note}{e2e}")
    L.append(f"- Structure: {a['structure']['label']}. 180-day swing {f(a['fib']['swing']['swing_low'])} "
             f"({a['fib']['swing']['swing_low_date']}) to {f(a['fib']['swing']['swing_high'])} ({a['fib']['swing']['swing_high_date']}).")
    L.append(f"- Bollinger: %B {r1(d['pct_b'] * 100 if d.get('pct_b') is not None else None)}, width at the "
             f"percentile {r1(d.get('bb_width_rank'))} of 120 days{' (squeeze)' if d.get('squeeze') else ''}. "
             f"Relative volume {r1(a['volume']['rel_volume'])}x.")
    vw = a["vwaps"]
    L.append("- VWAPs: " + ", ".join(f"{k} {f(v)} ({pc(a['live'] / v - 1)})" for k, v in vw.items() if v))
    vp = a["volume_profile"]
    if vp:
        L.append("- Volume profile: " + ", ".join(f"{k} POC {f(v['poc'])} (value area {f(v['val'])}-{f(v['vah'])})" for k, v in vp.items()))
    if a.get("derivatives") and "error" not in a["derivatives"]:
        try:
            import derivatives as D
            L.append("")
            L += D.render_snapshot(a["derivatives"])
        except Exception:
            pass
    L.append(f"\n### Signals firing now, and how each has traded on {a['symbol']}")
    if a["signals_now"]:
        L += render_signal_rows(a["signals_now"])
        L.append("\nWin rate and median at the grading horizon (21 days for daily signals, 8 weeks for weekly), "
                 "direction-adjusted. 90% floor: lower bound of the win rate. Edge: average return after the signal "
                 "minus the average over all days. Typical drawdown: median worst move against the signal before "
                 "the horizon (use it to size stops). Proven = 10+ trades and a 90% floor above 50%.")
    else:
        L.append("None of the tracked signals fired on the last closed bars.")
    opens = [(s, o) for s in a["backtest"] for o in s["open"] if s["grade"] in ("proven", "positive, unproven")]
    if opens:
        L.append("\nStill inside their holding window: " + "; ".join(
            f"{s['label']} ({o['date']}, {pc(o['return_so_far'])} so far, {s['grade']})" for s, o in opens[-6:]))
    L.append("\n### Conditional setups (research, not instructions)")
    for p in a["setups"]["plans"]:
        t2 = f" then {f(p['target_2'])}" if p.get("target_2") else ""
        rr = f" · reward/risk {p['reward_risk']:.1f}" if p.get("reward_risk") else ""
        L.append(f"- **{p['name']}:** {p['condition']}. Reference {f(p['entry'])}, invalidation {f(p['invalidation'])}, "
                 f"target {f(p['target_1'])}{t2}{rr}.")
    se = a["seasonality"]
    cur = next(x for x in se["table"] if x["month"] == se["current_month"])
    nxt = next(x for x in se["table"] if x["month"] == se["next_month"])
    L.append(f"\nSeasonality: {se['current_month']} month to date {pc(se['mtd'])}; {se['current_month']} has been up "
             f"{rate(cur['positive'])} of {cur['n']} years (median {pc(cur['median'])}). "
             f"{se['next_month']}: up {rate(nxt['positive'])} of {nxt['n']} years, median {pc(nxt['median'])}.")
    return "\n".join(L)


def scan_row(a):
    d, w = a["tf"]["1D"], a["tf"]["1W"]
    sup = a["setups"]["supports"][:1]
    res = a["setups"]["resistances"][:1]
    yo = a["anchors"]["year"]["open"]
    ma = a["moving_averages"]
    good = [s for s in a["signals_now"] if s["grade"] in ("proven", "positive, unproven")]
    return {"symbol": a["symbol"], "live": a["live"], "1d": a["change"]["1d"], "7d": a["change"]["7d"],
            "30d": a["change"]["30d"], "vs_yearly_open": a["live"] / yo - 1 if yo else None,
            "rsi_d": d.get("rsi"), "rsi_w": w.get("rsi"),
            "macd_d": (d.get("macd") or {}).get("above_signal"), "macd_w": (w.get("macd") or {}).get("above_signal"),
            "vs_200d": ma.get("200D SMA", {}).get("price_vs"), "vs_21w_ema": ma.get("21W EMA", {}).get("price_vs"),
            "stance": a["score"]["stance"], "trend": a["score"]["trend"], "momentum": a["score"]["momentum"],
            "support": f"{f(sup[0]['low'])}-{f(sup[0]['high'])}" if sup else None,
            "resistance": f"{f(res[0]['low'])}-{f(res[0]['high'])}" if res else None,
            "signals": [f"{s['label']} [{s['grade']}]" for s in good], "flags": a["score"]["flags"]}


def render_scan(rows):
    L = ["| Coin | Price | 1d | 7d | 30d | vs yearly open | RSI D/W | MACD D/W | vs 200D | vs 21W EMA | Stance | Support | Resistance | Signals with a record |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md = lambda b: "n/a" if b is None else ("up" if b else "down")  # noqa: E731
        L.append(f"| {r['symbol']} | {f(r['live'])} | {pc(r['1d'])} | {pc(r['7d'])} | {pc(r['30d'])} | {pc(r['vs_yearly_open'])} "
                 f"| {r1(r['rsi_d'])}/{r1(r['rsi_w'])} | {md(r['macd_d'])}/{md(r['macd_w'])} | {pc(r['vs_200d'])} "
                 f"| {pc(r['vs_21w_ema'])} | {r['stance']} ({r['trend']:+d}/{r['momentum']:+d}) | {r['support'] or 'n/a'} "
                 f"| {r['resistance'] or 'n/a'} | {'; '.join(r['signals']) or '-'} |")
    return "\n".join(L)


def bench_series():
    d, _ = load_candles("BTC", "1d", HISTORY_DAYS, asset="crypto")
    dc = closed(d, "1d")
    return {"symbol": "BTC", "dc": dc, "wc": closed(T.resample(d, "W"), "W")}


def strip(a):
    a = dict(a)
    a.pop("_series", None)
    return a


def _json_anywhere(sub):
    """Let --json follow the subcommand too (`liqmap BTC --json`), not only precede it."""
    for sp in sub.choices.values():
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="emit JSON")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("analyze", "scan", "levels", "backtest", "seasonality"):
        sp = sub.add_parser(name)
        sp.add_argument("symbols", nargs="+")
        sp.add_argument("--asset", choices=["crypto", "stock"], help="force the asset type (default: crypto venues "
                        "first, then Yahoo Finance for stocks and ETFs)")
        if name in ("analyze", "levels"):
            sp.add_argument("--derivs", action="store_true", help="add funding, OI, whale positions and liquidation map")
    sub.add_parser("cycle", help="BTC cycle gauges")
    _json_anywhere(sub)
    args = p.parse_args()

    if args.cmd == "cycle":
        d, src = load_candles("BTC", "1d", HISTORY_DAYS, asset="crypto")
        live = d["c"][-1]
        dc, wc = closed(d, "1d"), closed(T.resample(d, "W"), "W")
        g = cycle_gauges(dc, wc, live)
        g["live"], g["source"] = live, src
        if args.json:
            print(json.dumps(g, indent=2))
            return
        pl = g["power_law"]
        print(f"## BTC cycle gauges ({src}, live {f(live)})\n")
        print("| Gauge | Reading | How to read it |\n|---|---|---|")
        print(f"| Mayer multiple (price / 200D SMA) | {g['mayer_multiple']:.2f} (percentile {g['mayer_percentile']:.0f} of history) | under 0.8 deep value; over 2.4 historically overheated |")
        print(f"| Pi cycle (111D SMA / 2x 350D SMA) | {g['pi_cycle_ratio']:.2f} | 1.0 or above has marked cycle tops |")
        print(f"| Price / 2-year MA | {g['price_vs_2y_ma']:.2f} (2Y MA {f(g['two_year_ma'])}, x5 {f(g['two_year_ma_x5'])}) | under 1 is the accumulation zone; x5 has marked tops |")
        print(f"| Price / 200-week SMA | {g['price_vs_200w']:.2f} (200W {f(g['200w_sma'])}) | near 1 has marked bear-market floors |")
        print(f"| Drawdown from ATH | {pc(g['drawdown_from_ath'])} (ATH {f(g['ath'])} on {g['ath_date']}) | |")
        print(f"| Days since the April 2024 halving | {g['days_since_halving']} | past cycle tops came ~520-550 days after a halving |")
        print(f"| Power-law fair value | {f(pl['fair_value'])} (z {pl['z']:+.2f}; -1sd {f(pl['band_minus_1sd'])}, +1sd {f(pl['band_plus_1sd'])}, +2sd {f(pl['band_plus_2sd'])}) | fitted to {src} daily closes since {pl['fit_from']}; a rainbow-chart-style band, not a forecast |")
        return

    bench = None
    if args.cmd in ("analyze", "scan", "backtest") and any(s.upper() != "BTC" for s in args.symbols):
        try:
            bench = bench_series()
        except FetchError:
            bench = None
    results, errors = [], []
    for sym in args.symbols:
        try:
            if args.cmd == "seasonality":
                d, src = load_candles(sym, "1d", HISTORY_DAYS, asset=args.asset)
                results.append({"symbol": sym.upper(), "source": src, **seasonality(d, d["c"][-1])})
                continue
            a = analyze(sym, with_derivs=getattr(args, "derivs", False), bench=bench, asset=args.asset)
            results.append(a)
        except FetchError as e:
            errors.append(f"{sym.upper()}: {e}")
    if args.json:
        if args.cmd == "scan":
            print(json.dumps([scan_row(a) for a in results], indent=2, default=str))
        elif args.cmd == "levels":
            print(json.dumps([{"symbol": a["symbol"], "live": a["live"], "zones": a["zones"]} for a in results], indent=2))
        elif args.cmd == "backtest":
            print(json.dumps([{"symbol": a["symbol"], "signals": a["backtest"]} for a in results], indent=2, default=str))
        else:
            print(json.dumps([strip(a) for a in results], indent=2, default=str))
    else:
        if args.cmd == "scan":
            print(render_scan([scan_row(a) for a in results]))
        elif args.cmd == "levels":
            for a in results:
                print(f"## {a['symbol']} levels (live {f(a['live'])})\n")
                print("| Side | Zone | Distance | Strength | What's there |\n|---|---|---|---|---|")
                for z in sorted(a["zones"], key=lambda z: -z["mid"]):
                    if z["weight"] >= 2.5:
                        print(f"| {z['side']} | {f(z['low'])}-{f(z['high'])} | {pc(z['distance'])} | {z['weight']:.1f} | {'; '.join(z['levels'][:8])} |")
                print()
        elif args.cmd == "backtest":
            for a in results:
                print(f"## {a['symbol']} — every tracked signal ({a['source']}, {a['daily_bars']} daily bars)\n")
                print("\n".join(render_signal_rows(sorted(a["backtest"], key=lambda s: (
                    {"proven": 0, "positive, unproven": 1, "coin-flip or worse": 2, "too few": 3}[s["grade"]],
                    -(s[s["primary"]].get("edge") or -1))))))
                print("\nIn-sample on one coin; with ~45 signals tested, expect two or three to look good by chance. "
                      "Prefer signals that are also proven on BTC and ETH.\n")
        elif args.cmd == "seasonality":
            for r in results:
                print(f"## {r['symbol']} returns by calendar month ({r['source']})\n")
                print("| Month | Years | Up | Median | Mean |\n|---|---|---|---|---|")
                for x in r["table"]:
                    print(f"| {x['month']} | {x['n']} | {rate(x['positive'])} "
                          f"| {pc(x['median'])} | {pc(x['mean'])} |")
                print(f"\n{r['current_month']} to date: {pc(r['mtd'])}.\n")
        else:
            for a in results:
                print(render(a))
                print()
    for e in errors:
        print(f"skipped {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
