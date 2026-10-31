#!/usr/bin/env python3
"""
Derivatives, positioning and liquidity for crypto assets, from free public
APIs. Standard library only.

Usage:
  python derivatives.py funding BTC ETH HYPE     # funding across venues, open interest, 30-day funding percentile
  python derivatives.py positioning HYPE         # OI change, long/short ratios, taker flow, Hyperliquid whales
  python derivatives.py liqmap BTC               # modeled liquidation map + whale liquidation levels + recent liquidations
  python derivatives.py options BTC              # Deribit put/call, max pain, OI walls, 25-delta skew, DVOL, basis
  python derivatives.py book BTC                 # order-book walls within 5%
  python derivatives.py premium BTC              # Coinbase premium vs Binance (US spot demand)
  python derivatives.py sentiment                # Fear & Greed, MVRV Z-score, BTC dominance, stablecoins, DXY, S&P 500, 10Y
  python derivatives.py snapshot SOL             # all of the above for one coin
  add --json for machine-readable output

Sources: Hyperliquid (funding, OI, predicted Binance/Bybit funding, top
traders' live positions), OKX (OI history, long/short ratios, taker volume,
liquidation orders), Bitget, Gate, dYdX, Deribit (options, DVOL, futures
basis), Coinbase (order book), alternative.me, CoinMetrics community,
CoinGecko, DefiLlama and Yahoo Finance. Binance futures and Bybit
refuse some regions; their funding still arrives through Hyperliquid's
predicted-funding feed.

The liquidation map is a model, not exchange data: it replays the last 30
days of OKX open interest hour by hour, assumes every new contract opened a
long and a short at that hour's average price across a spread of leverage
(5x 15%, 10x 30%, 25x 30%, 50x 15%, 100x 10%), closes positions pro rata
when open interest falls, and deletes any level price has since traded
through. It shows where forced selling or buying would cluster if those
assumptions hold, scaled to the open interest on the venues reachable here.
The whale levels, by contrast, are real: liquidation prices of the largest
profitable Hyperliquid accounts' open positions.
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
import ta_core as T  # noqa: E402
from market_data import ALIASES, HL_INFO, FetchError, fetch, load_candles  # noqa: E402

CACHE_DIR = os.environ.get("CRYPTO_ANALYST_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "crypto-analyst")
LEVERAGE_TIERS = ((5, 0.15), (10, 0.30), (25, 0.30), (50, 0.15), (100, 0.10))
MAINT_MARGIN = 0.005
WHALE_MIN_ACCOUNT = 1_000_000
WHALE_COUNT = 100
BASELINE_APR = 0.1095  # 0.01% per 8h: the default rate many venues charge when the market is balanced
CROWDED_APR = 0.25      # annualized funding above this: longs are paying well over the baseline
SQUEEZE_APR = -0.05     # below this: shorts are paying


# ---------------------------------------------------------------- helpers

def cached(name, ttl, fn):
    """Cache JSON results on disk for `ttl` seconds (the leaderboard is 40 MB)."""
    path = os.path.join(CACHE_DIR, name)
    try:
        if time.time() - os.path.getmtime(path) < ttl:
            with open(path) as fh:
                return json.load(fh)
    except (OSError, ValueError):
        pass
    data = fn()
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh)
    except OSError:
        pass
    return data


def safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except (FetchError, KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def hl_coin(sym):
    return ALIASES.get(sym, {}).get("hyperliquid", sym)


def okx_inst(sym):
    return f"{ALIASES.get(sym, {}).get('okx', sym)}-USDT-SWAP"


def pct(x, nd=1):
    return "n/a" if x is None else f"{x * 100:+.{nd}f}%"


def usd(x):
    if x is None:
        return "n/a"
    a = abs(x)
    s = f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}k"
    return ("-" if x < 0 else "") + s


def px(x):
    return "n/a" if x is None else (f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.6g}")


# ---------------------------------------------------------------- funding and open interest

def hl_contexts():
    meta, ctxs = fetch(HL_INFO, {"type": "metaAndAssetCtxs"})
    return {u["name"]: c for u, c in zip(meta["universe"], ctxs)}


def funding(sym, hl_ctx=None, predicted=None):
    """Current funding (per 8h and annualized) and open interest by venue."""
    rows = []
    coin = hl_coin(sym)
    hl_ctx = hl_ctx if hl_ctx is not None else safe(hl_contexts)
    c = hl_ctx.get(coin) if isinstance(hl_ctx, dict) else None
    mark = None
    if c:
        mark = float(c["markPx"])
        r = float(c["funding"])
        rows.append({"venue": "Hyperliquid", "rate_8h": r * 8, "apr": r * 24 * 365,
                     "oi_usd": float(c["openInterest"]) * mark, "basis_bps": float(c["premium"]) * 1e4,
                     "volume_24h": float(c["dayNtlVlm"])})
    predicted = predicted if predicted is not None else safe(fetch, HL_INFO, {"type": "predictedFundings"})
    if isinstance(predicted, list):
        for name, venues in predicted:
            if name != coin:
                continue
            for venue, info in venues:
                if venue == "HlPerp" or not info:
                    continue
                ih = info.get("fundingIntervalHours") or 8
                r = float(info["fundingRate"])
                rows.append({"venue": {"BinPerp": "Binance (predicted)", "BybitPerp": "Bybit (predicted)"}.get(venue, venue),
                             "rate_8h": r * 8 / ih, "apr": r * (24 / ih) * 365, "oi_usd": None})
    okx = safe(fetch, f"https://www.okx.com/api/v5/public/funding-rate?instId={okx_inst(sym)}")
    if isinstance(okx, dict) and okx.get("data"):
        d = okx["data"][0]
        ih = (int(d["nextFundingTime"]) - int(d["fundingTime"])) / 3.6e6 or 8
        r = float(d["fundingRate"])
        oi = safe(fetch, f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={okx_inst(sym)}")
        oi_usd = float(oi["data"][0]["oiUsd"]) if isinstance(oi, dict) and oi.get("data") else None
        rows.append({"venue": "OKX", "rate_8h": r * 8 / ih, "apr": r * (24 / ih) * 365, "oi_usd": oi_usd})
    bg = safe(fetch, f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={sym}USDT&productType=usdt-futures")
    if isinstance(bg, dict) and bg.get("data"):
        d = bg["data"][0]
        rate = float(d.get("fundingRate") or 0)
        rows.append({"venue": "Bitget", "rate_8h": rate, "apr": rate * 3 * 365,
                     "oi_usd": float(d.get("holdingAmount") or 0) * float(d.get("lastPr") or 0) or None})
    gt = safe(fetch, f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{sym}_USDT")
    if isinstance(gt, dict) and "funding_rate" in gt:
        ih = (gt.get("funding_interval") or 28800) / 3600
        r = float(gt["funding_rate"])
        rows.append({"venue": "Gate", "rate_8h": r * 8 / ih, "apr": r * (24 / ih) * 365,
                     "oi_usd": float(gt.get("position_size") or 0) * float(gt.get("quanto_multiplier") or 0)
                     * float(gt.get("mark_price") or 0) or None})
    dy = safe(fetch, f"https://indexer.dydx.trade/v4/perpetualMarkets?ticker={sym}-USD")
    if isinstance(dy, dict) and dy.get("markets", {}).get(f"{sym}-USD"):
        d = dy["markets"][f"{sym}-USD"]
        r = float(d.get("nextFundingRate") or 0)
        rows.append({"venue": "dYdX", "rate_8h": r * 8, "apr": r * 24 * 365,
                     "oi_usd": float(d.get("openInterest") or 0) * float(d.get("oraclePrice") or 0) or None})
    if sym in ("BTC", "ETH"):
        de = safe(fetch, f"https://www.deribit.com/api/v2/public/ticker?instrument_name={sym}-PERPETUAL")
        if isinstance(de, dict) and de.get("result"):
            d = de["result"]
            rows.append({"venue": "Deribit", "rate_8h": d.get("funding_8h"), "apr": (d.get("funding_8h") or 0) * 3 * 365,
                         "oi_usd": d.get("open_interest")})
    with_oi = [r for r in rows if r.get("oi_usd")]
    tot = sum(r["oi_usd"] for r in with_oi)
    weighted = sum(r["apr"] * r["oi_usd"] for r in with_oi) / tot if tot else None
    hist = safe(hl_funding_history, coin)
    out = {"venues": rows, "oi_total_usd": tot or None, "apr_oi_weighted": weighted, "mark": mark}
    if isinstance(hist, dict) and "error" not in hist:
        out["hl_history"] = hist
        cur = next((r["apr"] for r in rows if r["venue"] == "Hyperliquid"), None)
        if cur is not None:
            out["hl_percentile_30d"] = T.percentile_rank(hist["aprs"], cur)
    out["read"] = funding_read(out)
    return out


def hl_funding_history(coin, days=30):
    now = int(time.time() * 1000)
    start, rows = now - days * 86400000, []
    while start < now:
        page = fetch(HL_INFO, {"type": "fundingHistory", "coin": coin, "startTime": start})
        if not page:
            break
        rows += page
        start = page[-1]["time"] + 1
        if len(page) < 500:
            break
    aprs = [float(r["fundingRate"]) * 24 * 365 for r in rows]
    if not aprs:
        raise ValueError("no funding history")
    last7 = aprs[-24 * 7:]
    return {"aprs": aprs, "mean_30d": sum(aprs) / len(aprs), "mean_7d": sum(last7) / len(last7),
            "max_30d": max(aprs), "min_30d": min(aprs)}


def funding_read(f):
    apr = f.get("apr_oi_weighted")
    if apr is None:
        hl = next((r["apr"] for r in f["venues"] if r["venue"] == "Hyperliquid"), None)
        apr = hl
    p = f.get("hl_percentile_30d")
    if apr is None:
        return {"label": "no perp market found", "vote": 0}
    if apr >= CROWDED_APR or (p is not None and p >= 90 and apr >= 0.20):
        return {"label": f"crowded long: longs pay {apr * 100:.0f}% a year", "vote": -1}
    if apr <= SQUEEZE_APR or (p is not None and p <= 10 and apr < 0):
        return {"label": f"shorts paying ({apr * 100:.0f}% a year): squeeze fuel if price turns up", "vote": 1}
    if apr < BASELINE_APR * 0.5:
        return {"label": f"below the 11% baseline ({apr * 100:.0f}% a year): little appetite for leveraged longs", "vote": 0}
    return {"label": f"neutral, near the 11% baseline ({apr * 100:.0f}% a year)", "vote": 0}


# ---------------------------------------------------------------- OI history, ratios, taker flow

def okx_pages(url_base, pages=8, key=0):
    """Page backwards through an OKX rubik endpoint that takes `end`; returns rows oldest first."""
    rows, end = [], None
    for _ in range(pages):
        url = url_base + (f"&end={end}" if end else "")
        d = fetch(url).get("data") or []
        if not d:
            break
        rows += d
        end = d[-1][key]
        if len(d) < 100:
            break
    return sorted({r[0]: r for r in rows}.values(), key=lambda r: int(r[0]))


def oi_history(sym, pages=8):
    rows = okx_pages(f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?instId={okx_inst(sym)}"
                     f"&period=1H&limit=100", pages)
    return [(int(r[0]) // 1000, float(r[3])) for r in rows]


def positioning(sym, prices=None):
    out = {}
    oi = safe(oi_history, sym)
    if isinstance(oi, list) and len(oi) > 25:
        cur = oi[-1][1]

        def chg(h):
            return cur / oi[-1 - h][1] - 1 if len(oi) > h and oi[-1 - h][1] else None

        out["okx_oi"] = {"usd": cur, "4h": chg(4), "24h": chg(24), "7d": chg(168), "30d_points": len(oi)}
        if prices:
            pm = dict(zip(prices["t"], prices["c"]))
            p_now, p_24 = pm.get(oi[-1][0]), pm.get(oi[-25][0])
            if p_now and p_24:
                pr, oc = p_now / p_24 - 1, chg(24)
                out["quadrant"] = oi_quadrant(pr, oc)
                out["price_24h"] = pr
    ccy = ALIASES.get(sym, {}).get("okx", sym)
    ls = safe(fetch, f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=1H")
    if isinstance(ls, dict) and ls.get("data"):
        d = ls["data"]
        out["okx_accounts_long_short"] = {"now": float(d[0][1]), "24h_ago": float(d[24][1]) if len(d) > 24 else None}
    tp = safe(fetch, "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader"
                     f"?instId={okx_inst(sym)}&period=1H&limit=25")
    if isinstance(tp, dict) and tp.get("data"):
        d = tp["data"]
        out["okx_top_traders_position_ratio"] = {"now": float(d[0][1]), "24h_ago": float(d[-1][1])}
    tv = safe(fetch, f"https://www.okx.com/api/v5/rubik/stat/taker-volume-contract?instId={okx_inst(sym)}&period=1H&limit=24")
    if isinstance(tv, dict) and tv.get("data"):
        sell = sum(float(r[1]) for r in tv["data"])
        buy = sum(float(r[2]) for r in tv["data"])
        out["okx_taker_buy_share_24h"] = buy / (buy + sell) if buy + sell else None
    return out


def oi_quadrant(price_chg, oi_chg):
    if price_chg is None or oi_chg is None:
        return None
    up_p, up_oi = price_chg > 0, oi_chg > 0
    if abs(oi_chg) < 0.02:
        return {"label": "open interest flat", "vote": 0}
    if up_p and up_oi:
        return {"label": "new longs (price and OI up): trend has leverage behind it", "vote": 0.5}
    if not up_p and up_oi:
        return {"label": "new shorts (price down, OI up): pressure building, squeeze fuel if it reverses", "vote": -0.5}
    if up_p and not up_oi:
        return {"label": "short covering (price up, OI down): rally lacks new buyers", "vote": -0.25}
    return {"label": "long flush (price and OI down): leverage cleared, often near short-term lows", "vote": 0.5}


# ---------------------------------------------------------------- Hyperliquid whales

def leaderboard():
    rows = fetch("https://stats-data.hyperliquid.xyz/Mainnet/leaderboard")["leaderboardRows"]
    keep = []
    for r in rows:
        av = float(r["accountValue"])
        if av < WHALE_MIN_ACCOUNT:
            continue
        perf = dict(r["windowPerformances"])
        keep.append({"address": r["ethAddress"], "account_value": av,
                     "pnl_all": float(perf["allTime"]["pnl"]), "pnl_month": float(perf["month"]["pnl"])})
    keep.sort(key=lambda r: -r["pnl_all"])
    return keep[:WHALE_COUNT]


def whale_positions():
    """Open positions of the most profitable large Hyperliquid accounts (cached 15 minutes)."""
    def build():
        lb = cached("hl_leaderboard_top.json", 6 * 3600, leaderboard)

        def one(r):
            try:
                st = fetch(HL_INFO, {"type": "clearinghouseState", "user": r["address"]})
            except FetchError:
                return []
            out = []
            for ap in st.get("assetPositions", []):
                p = ap["position"]
                szi = float(p["szi"])
                if szi == 0:
                    continue
                out.append({"coin": p["coin"], "side": "long" if szi > 0 else "short",
                            "notional": float(p["positionValue"]), "entry": float(p["entryPx"]),
                            "liq": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
                            "leverage": p["leverage"]["value"], "upnl": float(p["unrealizedPnl"]),
                            "account": r["address"][:8]})
            return out

        with cf.ThreadPoolExecutor(8) as ex:
            return [p for ps in ex.map(one, lb) for p in ps]
    return cached("hl_whale_positions.json", 900, build)


def whales(sym, live):
    coin = hl_coin(sym)
    pos = [p for p in whale_positions() if p["coin"] == coin]
    longs = [p for p in pos if p["side"] == "long"]
    shorts = [p for p in pos if p["side"] == "short"]
    ln, sn = sum(p["notional"] for p in longs), sum(p["notional"] for p in shorts)

    def avg_entry(ps):
        n = sum(p["notional"] for p in ps)
        return sum(p["entry"] * p["notional"] for p in ps) / n if n else None

    liqs = sorted([p for p in pos if p["liq"] and abs(p["liq"] / live - 1) <= 0.30], key=lambda p: p["liq"])
    return {"accounts": len({p["account"] for p in pos}), "long_usd": ln, "short_usd": sn,
            "long_short": ln / sn if sn else None, "net_usd": ln - sn,
            "avg_entry_long": avg_entry(longs), "avg_entry_short": avg_entry(shorts),
            "upnl_long": sum(p["upnl"] for p in longs), "upnl_short": sum(p["upnl"] for p in shorts),
            "liquidations_within_30pct": [{"price": p["liq"], "side": p["side"], "notional": p["notional"],
                                          "distance": p["liq"] / live - 1, "leverage": p["leverage"]} for p in liqs],
            "universe": f"top {WHALE_COUNT} Hyperliquid accounts by all-time PnL with over ${WHALE_MIN_ACCOUNT / 1e6:.0f}M"}


# ---------------------------------------------------------------- liquidation map

def liquidation_model(sym, live, hourly=None, scale_to=None):
    oi = oi_history(sym)
    if len(oi) < 48:
        raise ValueError("not enough OKX open-interest history")
    if hourly is None:
        hourly, _ = load_candles(sym, "1h", 35)
    bars = {t: (h, l, c) for t, h, l, c in zip(hourly["t"], hourly["h"], hourly["l"], hourly["c"])}
    book = []  # [side, liq_price, notional]
    swept = {"long": 0.0, "short": 0.0}
    prev = None
    for t, o in oi:
        bar = bars.get(t)
        if bar is None:
            prev = o if prev is None else prev
            continue
        h, l, c = bar
        keep = []
        for side, lp, n in book:
            if (side == "long" and l <= lp) or (side == "short" and h >= lp):
                swept[side] += n
            else:
                keep.append([side, lp, n])
        book = keep
        if prev:
            d = o - prev
            if d < 0 and prev > 0:
                f = max(0.0, 1 + d / prev)
                for b in book:
                    b[2] *= f
            elif d > 0:
                p = (h + l + c) / 3
                for lev, w in LEVERAGE_TIERS:
                    book.append(["long", p * (1 - 1 / lev + MAINT_MARGIN), d * w])
                    book.append(["short", p * (1 + 1 / lev - MAINT_MARGIN), d * w])
        prev = o
    scale = (scale_to / oi[-1][1]) if scale_to and oi[-1][1] else 1.0
    step = live * 0.005
    bins = {}
    for side, lp, n in book:
        if abs(lp / live - 1) > 0.25:
            continue
        k = round((lp - live) / step)
        bins[(side, k)] = bins.get((side, k), 0.0) + n * scale
    clusters = []
    for (side, k), n in bins.items():
        if (side == "long" and k < 0) or (side == "short" and k > 0):
            clusters.append({"side": side, "price": live + k * step, "notional": n, "distance": k * step / live})

    def within(side, d):
        return sum(c["notional"] for c in clusters if c["side"] == side and abs(c["distance"]) <= d + 1e-9)

    cum = {f"{int(d * 100)}%": {"long_liqs_below": within("long", d), "short_liqs_above": within("short", d)}
           for d in (0.02, 0.05, 0.10, 0.20)}
    top_long = sorted([c for c in clusters if c["side"] == "long"], key=lambda c: -c["notional"])[:5]
    top_short = sorted([c for c in clusters if c["side"] == "short"], key=lambda c: -c["notional"])[:5]
    l5, s5 = cum["5%"]["long_liqs_below"], cum["5%"]["short_liqs_above"]
    imbalance = s5 / l5 if l5 else None
    if imbalance is None:
        read = {"label": "no mapped liquidations within 5%", "vote": 0}
    elif imbalance >= 1.5:
        read = {"label": f"more shorts than longs would be liquidated within 5% ({imbalance:.1f}x): upside magnet", "vote": 0.5}
    elif imbalance <= 1 / 1.5:
        read = {"label": f"more longs than shorts would be liquidated within 5% ({1 / imbalance:.1f}x): downside magnet", "vote": -0.5}
    else:
        read = {"label": f"liquidations roughly balanced within 5% ({imbalance:.2f}x shorts/longs)", "vote": 0}
    return {"model": "OKX OI replay, last 30 days" + (", scaled to reachable venues' OI" if scale != 1.0 else ""),
            "hours": len(oi), "top_long_clusters": sorted(top_long, key=lambda c: -c["price"]),
            "top_short_clusters": sorted(top_short, key=lambda c: c["price"]), "cumulative": cum,
            "swept_30d": {k: v * scale for k, v in swept.items()}, "read": read,
            "bins": sorted(clusters, key=lambda c: c["price"])}


def realized_liquidations(sym, pages=6):
    """Recent forced liquidations on OKX (exchange-reported, not modeled)."""
    rows, after = [], None
    inst = okx_inst(sym)
    uly = inst.replace("-SWAP", "")
    ct = fetch(f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst}")["data"][0]
    ct_val = float(ct["ctVal"])
    for _ in range(pages):
        url = f"https://www.okx.com/api/v5/public/liquidation-orders?instType=SWAP&uly={uly}&state=filled&limit=100"
        if after:
            url += f"&after={after}"
        d = fetch(url).get("data") or []
        if not d or not d[0]["details"]:
            break
        det = d[0]["details"]
        rows += det
        after = det[-1]["ts"]
        if len(det) < 100:
            break
    if not rows:
        return {"hours": 0}
    span_h = (int(rows[0]["ts"]) - int(rows[-1]["ts"])) / 3.6e6
    longs = sum(float(r["sz"]) * ct_val * float(r["bkPx"]) for r in rows if r["posSide"] == "long")
    shorts = sum(float(r["sz"]) * ct_val * float(r["bkPx"]) for r in rows if r["posSide"] == "short")
    return {"hours": span_h, "long_usd": longs, "short_usd": shorts, "orders": len(rows), "venue": "OKX"}


# ---------------------------------------------------------------- options (Deribit)

def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def options(cur):
    cur = cur.upper()
    if cur not in ("BTC", "ETH"):
        raise ValueError("Deribit options cover BTC and ETH")
    book = fetch(f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={cur}&kind=option")["result"]
    now = time.time()
    parsed = []
    for o in book:
        _, exp, strike, kind = o["instrument_name"].split("-")
        e = dt.datetime.strptime(exp, "%d%b%y").replace(hour=8, tzinfo=dt.timezone.utc).timestamp()
        parsed.append({"exp": exp, "t": (e - now) / (365 * 86400), "k": float(strike), "cp": kind,
                       "oi": o.get("open_interest") or 0, "vol": o.get("volume") or 0, "iv": o.get("mark_iv"),
                       "S": o.get("underlying_price")})
    spot = next(p["S"] for p in parsed if p["S"])
    puts = sum(p["oi"] for p in parsed if p["cp"] == "P")
    calls = sum(p["oi"] for p in parsed if p["cp"] == "C")
    by_exp = {}
    for p in parsed:
        by_exp.setdefault(p["exp"], []).append(p)
    expiries = sorted(by_exp, key=lambda e: by_exp[e][0]["t"])
    pain = []
    for e in sorted([e for e in expiries if by_exp[e][0]["t"] <= 60 / 365], key=lambda e: -sum(p["oi"] for p in by_exp[e]))[:3]:
        ps = by_exp[e]
        strikes = sorted({p["k"] for p in ps})

        def payout(s):
            return sum(p["oi"] * (max(0, s - p["k"]) if p["cp"] == "C" else max(0, p["k"] - s)) for p in ps)

        mp = min(strikes, key=payout)
        pain.append({"expiry": e, "days": round(ps[0]["t"] * 365, 1), "max_pain": mp,
                     "oi": sum(p["oi"] for p in ps), "put_call_oi": sum(p["oi"] for p in ps if p["cp"] == "P")
                     / max(1e-9, sum(p["oi"] for p in ps if p["cp"] == "C"))})
    near = [p for p in parsed if p["t"] <= 45 / 365]
    walls = {}
    for p in near:
        walls[(p["cp"], p["k"])] = walls.get((p["cp"], p["k"]), 0) + p["oi"]
    call_walls = sorted([(k, v) for (cp, k), v in walls.items() if cp == "C" and k > spot], key=lambda x: -x[1])[:3]
    put_walls = sorted([(k, v) for (cp, k), v in walls.items() if cp == "P" and k < spot], key=lambda x: -x[1])[:3]
    target = min(expiries, key=lambda e: abs(by_exp[e][0]["t"] - 30 / 365))
    skew = None
    ps = [p for p in by_exp[target] if p["iv"] and p["t"] > 0]
    if ps:
        def delta(p):
            s, k, t, v = spot, p["k"], p["t"], p["iv"] / 100
            d1 = (math.log(s / k) + 0.5 * v * v * t) / (v * math.sqrt(t))
            return _ncdf(d1) if p["cp"] == "C" else _ncdf(d1) - 1
        c25 = min([p for p in ps if p["cp"] == "C"], key=lambda p: abs(delta(p) - 0.25))
        p25 = min([p for p in ps if p["cp"] == "P"], key=lambda p: abs(delta(p) + 0.25))
        atm = min(ps, key=lambda p: abs(p["k"] - spot))
        skew = {"expiry": target, "days": round(by_exp[target][0]["t"] * 365, 1), "put_25d_iv": p25["iv"],
                "call_25d_iv": c25["iv"], "skew": p25["iv"] - c25["iv"], "atm_iv": atm["iv"]}
    now_ms = int(now * 1000)
    dv = fetch(f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={cur}"
               f"&start_timestamp={now_ms - 366 * 86400000}&end_timestamp={now_ms}&resolution=86400")["result"]["data"]
    closes = [r[4] for r in dv]
    fut = fetch(f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={cur}&kind=future")["result"]
    basis = []
    for fu in fut:
        name = fu["instrument_name"]
        if name.endswith("PERPETUAL"):
            continue
        e = dt.datetime.strptime(name.split("-")[1], "%d%b%y").replace(hour=8, tzinfo=dt.timezone.utc).timestamp()
        days = (e - now) / 86400
        idx = fu.get("estimated_delivery_price") or spot
        if days > 3 and fu.get("mark_price"):
            basis.append({"expiry": name.split("-")[1], "days": round(days), "annualized": (fu["mark_price"] / idx - 1) * 365 / days})
    basis.sort(key=lambda b: b["days"])
    read = {"label": "neutral", "vote": 0}
    if skew and skew["skew"] >= 5:
        read = {"label": f"puts {skew['skew']:.1f} vol points over calls: hedging demand, fear priced", "vote": 0.25}
    elif skew and skew["skew"] <= -3:
        read = {"label": f"calls {-skew['skew']:.1f} vol points over puts: upside chased", "vote": -0.25}
    return {"spot": spot, "put_call_oi": puts / calls if calls else None, "max_pain": pain,
            "call_walls": [{"strike": k, "oi": v} for k, v in call_walls],
            "put_walls": [{"strike": k, "oi": v} for k, v in put_walls], "skew_25d": skew,
            "dvol": closes[-1] if closes else None, "dvol_percentile_1y": T.percentile_rank(closes, closes[-1]) if closes else None,
            "basis": basis, "read": read}


# ---------------------------------------------------------------- order book

def book_walls(sym, live, band=0.05, bucket=0.0025):
    try:
        b = fetch(f"https://api.exchange.coinbase.com/products/{ALIASES.get(sym, {}).get('coinbase', sym)}-USD/book?level=2")
        bids = [(float(p), float(s)) for p, s, _ in b["bids"]]
        asks = [(float(p), float(s)) for p, s, _ in b["asks"]]
        venue = "Coinbase"
    except (FetchError, KeyError):
        b = fetch(HL_INFO, {"type": "l2Book", "coin": hl_coin(sym)})
        bids = [(float(x["px"]), float(x["sz"])) for x in b["levels"][0]]
        asks = [(float(x["px"]), float(x["sz"])) for x in b["levels"][1]]
        venue = "Hyperliquid (top 20 levels)"

    def agg(rows):
        out = {}
        for p, s in rows:
            if abs(p / live - 1) > band:
                continue
            k = round((p / live - 1) / bucket)
            out[k] = out.get(k, 0) + p * s
        return out

    ab, aa = agg(bids), agg(asks)
    bid_walls = sorted(ab.items(), key=lambda x: -x[1])[:3]
    ask_walls = sorted(aa.items(), key=lambda x: -x[1])[:3]
    return {"venue": venue, "bid_usd_within_2pct": sum(v for k, v in ab.items() if abs(k * bucket) <= 0.02),
            "ask_usd_within_2pct": sum(v for k, v in aa.items() if abs(k * bucket) <= 0.02),
            "bid_walls": [{"price": live * (1 + k * bucket), "usd": v} for k, v in bid_walls],
            "ask_walls": [{"price": live * (1 + k * bucket), "usd": v} for k, v in ask_walls]}


# ---------------------------------------------------------------- Coinbase premium

def coinbase_premium(sym="BTC"):
    """Coinbase USD price vs Binance USDT price converted at Coinbase's USDT-USD rate (CoinGlass's premium index)."""
    cb = float(fetch(f"https://api.exchange.coinbase.com/products/{sym}-USD/ticker")["price"])
    bn = float(fetch(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}USDT")["price"])
    usdt = float(fetch("https://api.exchange.coinbase.com/products/USDT-USD/ticker")["price"])
    prem = cb / (bn * usdt) - 1
    read = ("US spot buyers paying up" if prem > 0.001 else "US spot discount: US selling or weak US demand" if prem < -0.001
            else "flat")
    return {"coinbase": cb, "binance_usdt": bn, "usdt_usd": usdt, "premium": prem, "premium_usd": cb - bn * usdt, "read": read}


# ---------------------------------------------------------------- market-wide sentiment

def yahoo_closes(symbol, rng="6mo", tries=3):
    """Daily closes from Yahoo's chart API, retrying briefly because it rate-limits bursts."""
    for k in range(tries):
        try:
            y = fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d")
            return [x for x in y["chart"]["result"][0]["indicators"]["quote"][0]["close"] if x]
        except FetchError:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))


def sentiment():
    out = {}
    fg = safe(fetch, "https://api.alternative.me/fng/?limit=30")
    if isinstance(fg, dict) and fg.get("data"):
        vals = [int(x["value"]) for x in fg["data"]]
        out["fear_greed"] = {"now": vals[0], "label": fg["data"][0]["value_classification"],
                             "avg_7d": sum(vals[:7]) / 7, "avg_30d": sum(vals) / len(vals)}
    cm = safe(fetch, "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc"
                     "&metrics=CapMVRVCur,CapMrktCurUSD&frequency=1d&page_size=10000&start_time=2011-01-01")
    if isinstance(cm, dict) and cm.get("data"):
        mc = [float(r["CapMrktCurUSD"]) for r in cm["data"] if r.get("CapMrktCurUSD") and r.get("CapMVRVCur")]
        mv = [float(r["CapMVRVCur"]) for r in cm["data"] if r.get("CapMrktCurUSD") and r.get("CapMVRVCur")]
        rc = [m / v for m, v in zip(mc, mv)]
        mean = sum(mc) / len(mc)
        sd = math.sqrt(sum((m - mean) ** 2 for m in mc) / len(mc))
        out["mvrv"] = {"mvrv": mv[-1], "z_score": (mc[-1] - rc[-1]) / sd, "realized_price_ratio": mv[-1],
                       "as_of": cm["data"][-1]["time"][:10],
                       "note": "Z = (market cap - realized cap) / std of market cap since 2011; under 0 marked cycle lows, over 7 cycle tops"}
    cg = safe(fetch, "https://api.coingecko.com/api/v3/global")
    if isinstance(cg, dict) and cg.get("data"):
        d = cg["data"]
        out["dominance"] = {"btc": d["market_cap_percentage"].get("btc"), "eth": d["market_cap_percentage"].get("eth"),
                            "total_mcap_change_24h": d.get("market_cap_change_percentage_24h_usd")}
    st = safe(fetch, "https://stablecoins.llama.fi/stablecoincharts/all")
    if isinstance(st, list) and st:
        v = [float(x["totalCirculatingUSD"]["peggedUSD"]) for x in st if x.get("totalCirculatingUSD")]
        out["stablecoins"] = {"now": v[-1], "30d": v[-1] / v[-31] - 1, "90d": v[-1] / v[-91] - 1,
                              "vs_peak": v[-1] / max(v) - 1}
    missing = []
    for name, sym in (("dxy", "DX-Y.NYB"), ("spx", "%5EGSPC")):
        c = safe(yahoo_closes, sym)
        if isinstance(c, list) and len(c) >= 50:
            out[name] = {"last": c[-1], "20d": c[-1] / c[-21] - 1, "vs_50d": c[-1] / (sum(c[-50:]) / 50) - 1}
        else:
            missing.append({"dxy": "dollar index", "spx": "S&P 500"}[name])
    c = safe(yahoo_closes, "%5ETNX", "3mo")
    if isinstance(c, list) and len(c) > 21:
        out["us10y"] = {"last": c[-1], "20d_change_bp": (c[-1] - c[-21]) * 100}
    else:
        missing.append("10-year yield")
    for key, label in (("fear_greed", "Fear & Greed"), ("mvrv", "MVRV"), ("dominance", "dominance"), ("stablecoins", "stablecoins")):
        if key not in out:
            missing.append(label)
    out["missing"] = missing
    gate, why = 0, []
    if out.get("stablecoins"):
        s = out["stablecoins"]["30d"]
        gate += 1 if s > 0.01 else -1 if s < 0 else 0
        why.append(f"stablecoins {s * 100:+.1f}% in 30 days")
    if out.get("dxy"):
        gate += -1 if out["dxy"]["vs_50d"] > 0.01 else 1 if out["dxy"]["vs_50d"] < -0.01 else 0
        why.append(f"dollar {out['dxy']['vs_50d'] * 100:+.1f}% vs its 50-day")
    if out.get("spx"):
        gate += 1 if out["spx"]["vs_50d"] > 0 else -1
        why.append(f"S&P 500 {out['spx']['vs_50d'] * 100:+.1f}% vs its 50-day")
    if out.get("fear_greed"):
        f = out["fear_greed"]["now"]
        why.append(f"Fear & Greed {f}")
    out["risk_gate"] = {"score": gate, "label": "risk-on backdrop" if gate >= 2 else "risk-off backdrop" if gate <= -2 else "mixed backdrop",
                        "why": why}
    return out


# ---------------------------------------------------------------- one-coin snapshot

def snapshot(sym, live, hourly=None, include_options=True):
    sym = sym.upper()
    if hourly is None:
        try:
            hourly, _ = load_candles(sym, "1h", 35)
        except FetchError:
            hourly = None
    hl = safe(hl_contexts)
    pred = safe(fetch, HL_INFO, {"type": "predictedFundings"})
    out = {"symbol": sym, "as_of": T.utc(time.time()).strftime("%Y-%m-%d %H:%M UTC")}
    out["funding"] = safe(funding, sym, hl if isinstance(hl, dict) else None, pred)
    out["positioning"] = safe(positioning, sym, hourly)
    w = safe(whales, sym, live)
    out["whales"] = w
    oi_total = out["funding"].get("oi_total_usd") if isinstance(out["funding"], dict) else None
    out["liquidation_map"] = safe(liquidation_model, sym, live, hourly, oi_total)
    out["realized_liquidations"] = safe(realized_liquidations, sym)
    if include_options and sym in ("BTC", "ETH"):
        out["options"] = safe(options, sym)
    out["book"] = safe(book_walls, sym, live)
    if sym in ("BTC", "ETH", "SOL"):
        out["premium"] = safe(coinbase_premium, sym)
    out["levels"] = levels_from(out, live)
    out["votes"] = votes_from(out)
    return out


def levels_from(s, live):
    L = []
    lm = s.get("liquidation_map")
    if isinstance(lm, dict) and "error" not in lm:
        tops = sorted(lm["top_long_clusters"] + lm["top_short_clusters"], key=lambda c: -c["notional"])[:6]
        biggest = tops[0]["notional"] if tops else 1
        for c in tops:
            L.append({"price": c["price"], "label": f"{c['side']} liquidation cluster ({usd(c['notional'])} modeled)",
                      "weight": 0.75 + 0.75 * c["notional"] / biggest})
    w = s.get("whales")
    if isinstance(w, dict) and "error" not in w:
        buckets = {}
        for q in w["liquidations_within_30pct"]:
            k = round(q["price"] / live / 0.01)
            b = buckets.setdefault(k, {"price": 0, "n": 0, "side": q["side"]})
            b["price"] += q["price"] * q["notional"]
            b["n"] += q["notional"]
        for b in sorted(buckets.values(), key=lambda b: -b["n"])[:3]:
            if b["n"] > 0:
                L.append({"price": b["price"] / b["n"], "label": f"whale {b['side']} liquidations ({usd(b['n'])})", "weight": 1.0})
    o = s.get("options")
    if isinstance(o, dict) and "error" not in o:
        for c in o["call_walls"][:2]:
            L.append({"price": c["strike"], "label": f"call OI wall ({c['oi']:,.0f} contracts)", "weight": 1.0})
        for p in o["put_walls"][:2]:
            L.append({"price": p["strike"], "label": f"put OI wall ({p['oi']:,.0f} contracts)", "weight": 1.0})
        if o["max_pain"]:
            mp = o["max_pain"][0]
            L.append({"price": mp["max_pain"], "label": f"max pain {mp['expiry']}", "weight": 0.75})
    b = s.get("book")
    if isinstance(b, dict) and "error" not in b:
        for x in b["bid_walls"][:1]:
            L.append({"price": x["price"], "label": f"bid wall {usd(x['usd'])} ({b['venue']})", "weight": 0.75})
        for x in b["ask_walls"][:1]:
            L.append({"price": x["price"], "label": f"ask wall {usd(x['usd'])} ({b['venue']})", "weight": 0.75})
    return L


def votes_from(s):
    v = []
    f = s.get("funding")
    if isinstance(f, dict) and f.get("read"):
        v.append(("funding", f["read"]["vote"], f["read"]["label"]))
    p = s.get("positioning")
    if isinstance(p, dict) and p.get("quadrant"):
        v.append(("open interest", p["quadrant"]["vote"], p["quadrant"]["label"]))
    lm = s.get("liquidation_map")
    if isinstance(lm, dict) and lm.get("read"):
        v.append(("liquidation map", lm["read"]["vote"], lm["read"]["label"]))
    w = s.get("whales")
    if isinstance(w, dict) and w.get("long_short") is not None and isinstance(f, dict) and f.get("read"):
        ls = w["long_short"]
        if ls <= 0.5 and f["read"]["vote"] > 0:
            v.append(("whales", 0.5, f"top Hyperliquid traders net short ({ls:.2f} L/S) while shorts pay funding: squeeze setup"))
        elif ls >= 2 and f["read"]["vote"] < 0:
            v.append(("whales", -0.5, f"top Hyperliquid traders net long ({ls:.2f} L/S) with crowded funding: blow-off risk"))
        else:
            v.append(("whales", 0, f"top Hyperliquid traders {ls:.2f} long/short by notional"))
    o = s.get("options")
    if isinstance(o, dict) and o.get("read"):
        v.append(("options", o["read"]["vote"], o["read"]["label"]))
    return v


# ---------------------------------------------------------------- rendering

def render_snapshot(s):
    L = [f"### Derivatives and liquidity ({s['as_of']})"]
    f = s.get("funding")
    if isinstance(f, dict) and "error" not in f:
        L.append("| Venue | Funding /8h | Annualized | Open interest |\n|---|---|---|---|")
        for r in f["venues"]:
            L.append(f"| {r['venue']} | {r['rate_8h'] * 100:+.4f}% | {r['apr'] * 100:+.1f}% | {usd(r.get('oi_usd'))} |")
        extra = ""
        if f.get("hl_percentile_30d") is not None:
            extra = (f" Hyperliquid funding sits at percentile {f['hl_percentile_30d']:.0f} of the last 30 days "
                     f"(7-day average {f['hl_history']['mean_7d'] * 100:+.1f}% a year).")
        L.append(f"\nOI-weighted funding {pct(f.get('apr_oi_weighted'))} a year on {usd(f.get('oi_total_usd'))} of open interest "
                 f"(venues reachable here).{extra} **Read: {f['read']['label']}.**")
    p = s.get("positioning")
    if isinstance(p, dict) and "error" not in p and p:
        bits = []
        if p.get("okx_oi"):
            o = p["okx_oi"]
            bits.append(f"OKX open interest {usd(o['usd'])} ({pct(o['4h'])} 4h, {pct(o['24h'])} 24h, {pct(o['7d'])} 7d)")
        if p.get("quadrant"):
            bits.append(f"**{p['quadrant']['label']}**")
        if p.get("okx_accounts_long_short"):
            bits.append(f"OKX accounts long/short {p['okx_accounts_long_short']['now']:.2f}")
        if p.get("okx_top_traders_position_ratio"):
            bits.append(f"OKX top traders' positions long/short {p['okx_top_traders_position_ratio']['now']:.2f}")
        if p.get("okx_taker_buy_share_24h") is not None:
            bits.append(f"taker buys {p['okx_taker_buy_share_24h'] * 100:.0f}% of 24h perp volume")
        L.append("\n- Positioning: " + "; ".join(bits))
    w = s.get("whales")
    if isinstance(w, dict) and "error" not in w:
        ls = f"{w['long_short']:.2f}" if w["long_short"] is not None else "n/a (no shorts)"
        L.append(f"- Whales ({w['universe']}): {w['accounts']} accounts hold {usd(w['long_usd'])} long and "
                 f"{usd(w['short_usd'])} short (L/S {ls}); average entry long {px(w['avg_entry_long'])}, "
                 f"short {px(w['avg_entry_short'])}; unrealized PnL longs {usd(w['upnl_long'])}, shorts {usd(w['upnl_short'])}.")
        near = [q for q in w["liquidations_within_30pct"] if abs(q["distance"]) <= 0.15]
        if near:
            near.sort(key=lambda q: -q["notional"])
            L.append("  Largest whale liquidation prices within 15%: " + ", ".join(
                f"{q['side']} {px(q['price'])} ({usd(q['notional'])}, {pct(q['distance'])})" for q in near[:5]))
    lm = s.get("liquidation_map")
    if isinstance(lm, dict) and "error" not in lm:
        L.append(f"- Liquidation map ({lm['model']}): **{lm['read']['label']}**.")
        L.append("  Longs would be liquidated near " + ", ".join(
            f"{px(c['price'])} ({usd(c['notional'])})" for c in lm["top_long_clusters"][:4]) +
            "; shorts near " + ", ".join(f"{px(c['price'])} ({usd(c['notional'])})" for c in lm["top_short_clusters"][:4]) + ".")
        cu = lm["cumulative"]
        L.append("  Cumulative: " + "; ".join(f"within {k}: {usd(v['long_liqs_below'])} longs below, "
                                              f"{usd(v['short_liqs_above'])} shorts above" for k, v in cu.items() if k in ("5%", "10%")))
    rl = s.get("realized_liquidations")
    if isinstance(rl, dict) and rl.get("hours"):
        L.append(f"- Realized liquidations, OKX, last {rl['hours']:.1f}h: longs {usd(rl['long_usd'])}, shorts {usd(rl['short_usd'])}.")
    o = s.get("options")
    if isinstance(o, dict) and "error" not in o:
        sk = o.get("skew_25d") or {}
        mp = "; ".join(f"{m['expiry']} max pain {px(m['max_pain'])} (P/C {m['put_call_oi']:.2f})" for m in o["max_pain"])
        L.append(f"- Options (Deribit): put/call OI {o['put_call_oi']:.2f}; {mp}; call walls "
                 + ", ".join(px(c["strike"]) for c in o["call_walls"]) + "; put walls "
                 + ", ".join(px(p["strike"]) for p in o["put_walls"])
                 + (f"; 25-delta skew {sk['skew']:+.1f} vol pts ({sk['expiry']}), ATM IV {sk['atm_iv']:.0f}" if sk else "")
                 + f"; DVOL {o['dvol']:.1f} (percentile {o['dvol_percentile_1y']:.0f} of the past year)"
                 + ("; basis " + ", ".join(f"{b['expiry']} {b['annualized'] * 100:+.1f}%" for b in o["basis"][:3]) if o["basis"] else "")
                 + f". **Read: {o['read']['label']}.**")
    cp = s.get("premium")
    if isinstance(cp, dict) and "error" not in cp:
        L.append(f"- Coinbase premium: {cp['premium'] * 100:+.3f}% ({'+' if cp['premium_usd'] >= 0 else '-'}${abs(cp['premium_usd']):,.2f}) vs Binance: {cp['read']}.")
    b = s.get("book")
    if isinstance(b, dict) and "error" not in b:
        L.append(f"- Order book ({b['venue']}): {usd(b['bid_usd_within_2pct'])} bids vs {usd(b['ask_usd_within_2pct'])} asks within 2%; "
                 f"largest bid wall {', '.join(px(x['price']) + ' ' + usd(x['usd']) for x in b['bid_walls'][:2])}; "
                 f"largest ask wall {', '.join(px(x['price']) + ' ' + usd(x['usd']) for x in b['ask_walls'][:2])}.")
    return L


def render_sentiment(s):
    L = ["## Market backdrop\n", "| Gauge | Reading |", "|---|---|"]
    if s.get("fear_greed"):
        f = s["fear_greed"]
        L.append(f"| Fear & Greed | {f['now']} ({f['label']}); 7-day avg {f['avg_7d']:.0f}, 30-day {f['avg_30d']:.0f} |")
    if s.get("mvrv"):
        m = s["mvrv"]
        L.append(f"| BTC MVRV | {m['mvrv']:.2f}; Z-score {m['z_score']:.2f} ({m['as_of']}) |")
    if s.get("dominance"):
        d = s["dominance"]
        L.append(f"| Dominance | BTC {d['btc']:.1f}%, ETH {d['eth']:.1f}% |")
    if s.get("stablecoins"):
        st = s["stablecoins"]
        L.append(f"| Stablecoin supply | {usd(st['now'])}; {pct(st['30d'])} 30d, {pct(st['90d'])} 90d, {pct(st['vs_peak'])} vs peak |")
    if s.get("dxy"):
        L.append(f"| Dollar index | {s['dxy']['last']:.2f}; {pct(s['dxy']['20d'])} 20d, {pct(s['dxy']['vs_50d'])} vs 50-day |")
    if s.get("spx"):
        L.append(f"| S&P 500 | {s['spx']['last']:,.0f}; {pct(s['spx']['20d'])} 20d, {pct(s['spx']['vs_50d'])} vs 50-day |")
    if s.get("us10y"):
        L.append(f"| US 10-year yield | {s['us10y']['last']:.2f}% ({s['us10y']['20d_change_bp']:+.0f} bp in 20 days) |")
    g = s["risk_gate"]
    L.append(f"\n**{g['label']}** (score {g['score']:+d}): {'; '.join(g['why'])}.")
    if s.get("missing"):
        L.append(f"Not reached this run (the gate scores without them): {', '.join(s['missing'])}.")
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("funding", "positioning", "liqmap", "options", "book", "premium", "snapshot"):
        sp = sub.add_parser(name)
        sp.add_argument("symbols", nargs="+")
    sub.add_parser("sentiment")
    T.json_anywhere(sub)
    args = p.parse_args()
    if args.cmd == "sentiment":
        s = sentiment()
        print(json.dumps(s, indent=2) if args.json else render_sentiment(s))
        return
    hl = safe(hl_contexts)
    pred = safe(fetch, HL_INFO, {"type": "predictedFundings"})
    for sym in [s.upper() for s in args.symbols]:
        live = None
        if isinstance(hl, dict) and hl.get(hl_coin(sym)):
            live = float(hl[hl_coin(sym)]["markPx"])
        if live is None:
            try:
                d, _ = load_candles(sym, "1d", 3)
                live = d["c"][-1]
            except FetchError as e:
                print(f"{sym}: {e}", file=sys.stderr)
                continue
        if args.cmd == "funding":
            r = funding(sym, hl if isinstance(hl, dict) else None, pred)
            r.get("hl_history", {}).pop("aprs", None)
            out = {"symbol": sym, "funding": r}
        elif args.cmd == "positioning":
            h, _ = load_candles(sym, "1h", 3)
            out = {"symbol": sym, "positioning": positioning(sym, h), "whales": safe(whales, sym, live)}
        elif args.cmd == "liqmap":
            f = funding(sym, hl if isinstance(hl, dict) else None, pred)
            lm = liquidation_model(sym, live, None, f.get("oi_total_usd"))
            if not args.json:
                lm.pop("bins", None)
            out = {"symbol": sym, "liquidation_map": lm, "whales": safe(whales, sym, live),
                   "realized_liquidations": safe(realized_liquidations, sym)}
        elif args.cmd == "options":
            out = {"symbol": sym, "options": options(sym)}
        elif args.cmd == "book":
            out = {"symbol": sym, "book": book_walls(sym, live)}
        elif args.cmd == "premium":
            out = {"symbol": sym, "premium": coinbase_premium(sym)}
        else:
            out = snapshot(sym, live)
            out.get("funding", {}).get("hl_history", {}).pop("aprs", None)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
        else:
            out.setdefault("as_of", T.utc(time.time()).strftime("%Y-%m-%d %H:%M UTC"))
            print(f"## {sym} (live {px(live)})")
            print("\n".join(render_snapshot(out)))
            print()


if __name__ == "__main__":
    main()
