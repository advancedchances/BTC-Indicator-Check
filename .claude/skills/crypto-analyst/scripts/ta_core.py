#!/usr/bin/env python3
"""
Indicator math for technicals.py and derivatives.py. Standard library only.

Every function takes plain lists (oldest first) and returns lists of the same
length, with None where the indicator has not warmed up yet. Formulas follow
TradingView's built-ins so readings can be checked against a chart:

  - RSI and ATR use Wilder's smoothing (ta.rma), seeded with a simple average.
  - EMAs are seeded with the simple average of the first `n` values (ta.ema).
  - Bollinger Bands use the population standard deviation (ta.stdev).
  - Ichimoku leading spans are drawn `displacement - 1` bars ahead, as on
    TradingView's built-in Ichimoku, so "the cloud now" is the span computed
    displacement - 1 bars ago.
  - RSI divergences use TradingView's built-in rule: pivots on the RSI
    (5 bars left, 5 right), compared 5-60 bars apart, with price taken at
    the RSI pivot bars.

Candle series are dicts of lists: {"t": [...], "o": [...], "h": [...],
"l": [...], "c": [...], "v": [...]}, with "t" the bar's open time in UTC
epoch seconds.
"""

import datetime as dt
import math

DAY = 86400


# ---------------------------------------------------------------- averages

def sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x, n):
    """EMA over a list that may start with None values (it starts at the first real value)."""
    out = [None] * len(x)
    start = next((i for i, v in enumerate(x) if v is not None), None)
    if start is None or len(x) - start < n:
        return out
    alpha = 2 / (n + 1)
    prev = sum(x[start:start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(x)):
        prev = alpha * x[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def rma(x, n):
    """Wilder's moving average (TradingView ta.rma)."""
    out = [None] * len(x)
    start = next((i for i, v in enumerate(x) if v is not None), None)
    if start is None or len(x) - start < n:
        return out
    prev = sum(x[start:start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(x)):
        prev = (prev * (n - 1) + x[i]) / n
        out[i] = prev
    return out


def stdev(x, n):
    out = [None] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((v - m) ** 2 for v in w) / n)
    return out


def highest(x, n):
    return [max(x[i - n + 1:i + 1]) if i >= n - 1 else None for i in range(len(x))]


def lowest(x, n):
    return [min(x[i - n + 1:i + 1]) if i >= n - 1 else None for i in range(len(x))]


# ---------------------------------------------------------------- oscillators

def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    gains = [None] + [max(c[i] - c[i - 1], 0.0) for i in range(1, len(c))]
    losses = [None] + [max(c[i - 1] - c[i], 0.0) for i in range(1, len(c))]
    ag, al = rma(gains, n), rma(losses, n)
    for i in range(len(c)):
        if ag[i] is None:
            continue
        if al[i] == 0:
            out[i] = 100.0 if ag[i] > 0 else 50.0
        else:
            out[i] = 100 - 100 / (1 + ag[i] / al[i])
    return out


def stoch_rsi(c, rsi_n=14, stoch_n=14, k=3, d=3):
    r = rsi(c, rsi_n)
    raw = [None] * len(c)
    for i in range(len(c)):
        w = r[max(0, i - stoch_n + 1):i + 1]
        if len(w) < stoch_n or any(v is None for v in w):
            continue
        hi, lo = max(w), min(w)
        raw[i] = 100 * (r[i] - lo) / (hi - lo) if hi > lo else 50.0
    kline = _sma_skip(raw, k)
    return kline, _sma_skip(kline, d)


def _sma_skip(x, n):
    out = [None] * len(x)
    for i in range(len(x)):
        w = x[max(0, i - n + 1):i + 1]
        if len(w) == n and all(v is not None for v in w):
            out[i] = sum(w) / n
    return out


def macd(c, fast=12, slow=26, signal=9):
    ef, es = ema(c, fast), ema(c, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(ef, es)]
    sig = ema(line, signal)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def awesome(h, l):
    mid = [(a + b) / 2 for a, b in zip(h, l)]
    f, s = sma(mid, 5), sma(mid, 34)
    return [a - b if a is not None and b is not None else None for a, b in zip(f, s)]


# ---------------------------------------------------------------- volatility and volume

def bollinger(c, n=20, k=2.0):
    mid, sd = sma(c, n), stdev(c, n)
    up = [m + k * s if m is not None else None for m, s in zip(mid, sd)]
    lo = [m - k * s if m is not None else None for m, s in zip(mid, sd)]
    pct_b = [(x - b) / (u - b) if u is not None and u != b else None for x, u, b in zip(c, up, lo)]
    width = [(u - b) / m if m else None for u, b, m in zip(up, lo, mid)]
    return mid, up, lo, pct_b, width


def true_range(h, l, c):
    return [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]


def atr(h, l, c, n=14):
    return rma(true_range(h, l, c), n)


def obv(c, v):
    out, total = [0.0], 0.0
    for i in range(1, len(c)):
        total += v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0
        out.append(total)
    return out


def anchored_vwap(h, l, c, v, start):
    """VWAP of typical price from bar `start` to each later bar."""
    out, pv, vol = [None] * len(c), 0.0, 0.0
    for i in range(start, len(c)):
        tp = (h[i] + l[i] + c[i]) / 3
        pv += tp * v[i]
        vol += v[i]
        out[i] = pv / vol if vol else None
    return out


def rolling_vwap(h, l, c, v, n):
    out = [None] * len(c)
    for i in range(n - 1, len(c)):
        pv = sum((h[j] + l[j] + c[j]) / 3 * v[j] for j in range(i - n + 1, i + 1))
        vol = sum(v[i - n + 1:i + 1])
        out[i] = pv / vol if vol else None
    return out


def volume_profile(h, l, v, bins=60, value_area=0.70):
    """Spread each bar's volume evenly over its range. Returns POC, value-area high and low, and the bins."""
    lo, hi = min(l), max(h)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    vol = [0.0] * bins
    for a, b, q in zip(l, h, v):
        i0, i1 = int((a - lo) / step), min(int((b - lo) / step), bins - 1)
        i0 = min(i0, bins - 1)
        share = q / (i1 - i0 + 1)
        for i in range(i0, i1 + 1):
            vol[i] += share
    poc = max(range(bins), key=lambda i: vol[i])
    total, inside, a, b = sum(vol), vol[poc], poc, poc
    while inside < value_area * total and (a > 0 or b < bins - 1):
        below = vol[a - 1] if a > 0 else -1
        above = vol[b + 1] if b < bins - 1 else -1
        if above >= below:
            b += 1
            inside += vol[b]
        else:
            a -= 1
            inside += vol[a]
    center = [lo + (i + 0.5) * step for i in range(bins)]
    return {"poc": center[poc], "vah": lo + (b + 1) * step, "val": lo + a * step,
            "bins": list(zip(center, vol))}


# ---------------------------------------------------------------- Ichimoku

def ichimoku(h, l, c, tenkan=9, kijun=26, senkou=52, disp=26):
    def mid(n):
        hh, ll = highest(h, n), lowest(l, n)
        return [(a + b) / 2 if a is not None else None for a, b in zip(hh, ll)]

    tk, kj, sb = mid(tenkan), mid(kijun), mid(senkou)
    sa = [(a + b) / 2 if a is not None and b is not None else None for a, b in zip(tk, kj)]
    off = disp - 1
    cloud_a = [sa[i - off] if i - off >= 0 else None for i in range(len(c))]
    cloud_b = [sb[i - off] if i - off >= 0 else None for i in range(len(c))]
    return {"tenkan": tk, "kijun": kj, "span_a": sa, "span_b": sb,
            "cloud_a": cloud_a, "cloud_b": cloud_b, "offset": off}


# ---------------------------------------------------------------- structure

def pivot_highs(x, left=5, right=5):
    """Indexes i where x[i] is above the `left` bars before and not below the `right` bars after."""
    out = []
    for i in range(left, len(x) - right):
        if x[i] is None:
            continue
        w_l, w_r = x[i - left:i], x[i + 1:i + right + 1]
        if any(v is None for v in w_l + w_r):
            continue
        if x[i] > max(w_l) and x[i] >= max(w_r):
            out.append(i)
    return out


def pivot_lows(x, left=5, right=5):
    out = []
    for i in range(left, len(x) - right):
        if x[i] is None:
            continue
        w_l, w_r = x[i - left:i], x[i + 1:i + right + 1]
        if any(v is None for v in w_l + w_r):
            continue
        if x[i] < min(w_l) and x[i] <= min(w_r):
            out.append(i)
    return out


def divergences(h, l, osc, left=5, right=5, min_bars=5, max_bars=60):
    """Regular and hidden divergences between price and an oscillator.

    Pivots are found on the oscillator; price is read at the same bars
    (lows for bullish, highs for bearish). A divergence is confirmed `right`
    bars after the second pivot, which is the bar index reported as
    `confirmed`.
    """
    out = []
    lows, highs = pivot_lows(osc, left, right), pivot_highs(osc, left, right)
    for a, b in zip(lows, lows[1:]):
        if not min_bars <= b - a <= max_bars:
            continue
        if osc[b] > osc[a] and l[b] < l[a]:
            out.append({"kind": "regular bullish", "from": a, "to": b, "confirmed": b + right})
        elif osc[b] < osc[a] and l[b] > l[a]:
            out.append({"kind": "hidden bullish", "from": a, "to": b, "confirmed": b + right})
    for a, b in zip(highs, highs[1:]):
        if not min_bars <= b - a <= max_bars:
            continue
        if osc[b] < osc[a] and h[b] > h[a]:
            out.append({"kind": "regular bearish", "from": a, "to": b, "confirmed": b + right})
        elif osc[b] > osc[a] and h[b] < h[a]:
            out.append({"kind": "hidden bearish", "from": a, "to": b, "confirmed": b + right})
    return sorted(out, key=lambda d: d["confirmed"])


def market_structure(h, l, left=5, right=5, lookback=4):
    """Label the swing structure from the last few pivot highs and lows."""
    ph, pl = pivot_highs(h, left, right), pivot_lows(l, left, right)
    hs, ls = [h[i] for i in ph[-lookback:]], [l[i] for i in pl[-lookback:]]
    if len(hs) < 2 or len(ls) < 2:
        return {"label": "unclear", "swing_highs": ph, "swing_lows": pl}
    hh, hl = hs[-1] > hs[-2], ls[-1] > ls[-2]
    label = ("uptrend (higher highs, higher lows)" if hh and hl else
             "downtrend (lower highs, lower lows)" if not hh and not hl else
             "expanding range (higher high, lower low)" if hh else
             "contracting range (lower high, higher low)")
    return {"label": label, "swing_highs": ph, "swing_lows": pl}


# ---------------------------------------------------------------- pivot points

def pivot_points(high, low, close):
    """Classic, Fibonacci and Camarilla pivots from one period's high, low and close."""
    p, r = (high + low + close) / 3, high - low
    return {
        "classic": {"P": p, "R1": 2 * p - low, "S1": 2 * p - high, "R2": p + r, "S2": p - r,
                    "R3": high + 2 * (p - low), "S3": low - 2 * (high - p)},
        "fibonacci": {"P": p, "R1": p + 0.382 * r, "S1": p - 0.382 * r, "R2": p + 0.618 * r,
                      "S2": p - 0.618 * r, "R3": p + r, "S3": p - r},
        "camarilla": {"R1": close + r * 1.1 / 12, "S1": close - r * 1.1 / 12, "R2": close + r * 1.1 / 6,
                      "S2": close - r * 1.1 / 6, "R3": close + r * 1.1 / 4, "S3": close - r * 1.1 / 4,
                      "R4": close + r * 1.1 / 2, "S4": close - r * 1.1 / 2},
    }


def fib_retracements(swing_low, swing_high, up=True):
    """Retracement levels of a move. up=True: a rally from swing_low to swing_high."""
    r = swing_high - swing_low
    levels = (0.236, 0.382, 0.5, 0.618, 0.786)
    return {f"{x:.3f}": (swing_high - x * r) if up else (swing_low + x * r) for x in levels}


# ---------------------------------------------------------------- resampling

def utc(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


def period_key(ts, rule):
    d = utc(ts)
    if rule == "W":  # ISO weeks start Monday 00:00 UTC, as on TradingView's crypto charts
        monday = d.date() - dt.timedelta(days=d.weekday())
        return int(dt.datetime(monday.year, monday.month, monday.day, tzinfo=dt.timezone.utc).timestamp())
    if rule == "M":
        return int(dt.datetime(d.year, d.month, 1, tzinfo=dt.timezone.utc).timestamp())
    if rule == "Q":
        return int(dt.datetime(d.year, 3 * ((d.month - 1) // 3) + 1, 1, tzinfo=dt.timezone.utc).timestamp())
    if rule == "Y":
        return int(dt.datetime(d.year, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    if rule.endswith("D"):  # multi-day bars counted from the Unix epoch
        n = int(rule[:-1]) * DAY
        return ts - ts % n
    if rule.endswith("H"):
        n = int(rule[:-1]) * 3600
        return ts - ts % n
    raise ValueError(f"unknown rule {rule}")


def resample(s, rule):
    out = {k: [] for k in "tohlcv"}
    for i, ts in enumerate(s["t"]):
        k = period_key(ts, rule)
        if out["t"] and out["t"][-1] == k:
            out["h"][-1] = max(out["h"][-1], s["h"][i])
            out["l"][-1] = min(out["l"][-1], s["l"][i])
            out["c"][-1] = s["c"][i]
            out["v"][-1] += s["v"][i]
        else:
            out["t"].append(k)
            out["o"].append(s["o"][i])
            out["h"].append(s["h"][i])
            out["l"].append(s["l"][i])
            out["c"].append(s["c"][i])
            out["v"].append(s["v"][i])
    return out


def slice_series(s, a, b=None):
    return {k: v[a:b] for k, v in s.items()}


# ---------------------------------------------------------------- statistics

def wilson_lower(wins, n, z=1.645):
    """Lower end of the Wilson interval for a win rate (z=1.645: two-sided 90%)."""
    if n == 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def percentile_rank(window, x):
    vals = [v for v in window if v is not None]
    if not vals:
        return None
    below, ties = sum(v < x for v in vals), sum(v == x for v in vals)
    return 100 * (below + 0.5 * ties) / len(vals)  # mid-rank, so a capped series doesn't read as 100th


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def slope_pct(x, n):
    """Percent change of a series over its last n values (None-safe)."""
    if len(x) <= n or x[-1] is None or x[-1 - n] is None or x[-1 - n] == 0:
        return None
    return x[-1] / x[-1 - n] - 1


def last(x, k=1):
    """The k-th last non-None value."""
    seen = 0
    for v in reversed(x):
        if v is not None:
            seen += 1
            if seen == k:
                return v
    return None
