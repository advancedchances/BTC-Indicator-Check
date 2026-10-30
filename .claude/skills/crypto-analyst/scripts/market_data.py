#!/usr/bin/env python3
"""
Check which free crypto data sources this session can reach, then pull
prices and protocol revenue from them. Standard library only.

Run `check` at the start of every session: network policies, rate limits
and site rules change between sessions, so a source that failed last time
may work now, and the other way round. It tells apart a block by the
session's network policy (don't route around it), a refusal by the site
itself, a rate limit and a timeout.

Usage:
  python market_data.py check
  python market_data.py prices BTC ETH SKY AERO          # Coinbase Exchange spot, 24h volume, thin-venue flag
  python market_data.py candles SKY --start 2026-09-25 --end 2026-10-17
  python market_data.py candles SKY --start 2026-09-25T05:00 --end 2026-09-25T06:00 --granularity 3600
  python market_data.py revenue aave sky hyperliquid     # DefiLlama fees, revenue, holders revenue
  python market_data.py mcaps sky aave hyperliquid       # CoinGecko market caps via DefiLlama (CoinGecko ids)
  python market_data.py hl-buybacks                      # HYPE held by Hyperliquid's buyback address
  add --json to any command for machine-readable output

Prices use Coinbase Exchange products quoted in USD (SKY-USD). A product
Coinbase doesn't list is reported as such, not guessed. Revenue uses DefiLlama
protocol slugs (the last part of a defillama.com/protocol/<slug> URL);
monthly figures are summed from daily data, so they are 30-day sums, not
calendar months. Market caps use CoinGecko ids (the last part of a
coingecko.com/en/coins/<id> URL) through DefiLlama's coins API, which works
when CoinGecko's own free tier is rate-limiting you.
"""

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request

USER_AGENT = "crypto-analyst-skill/1.0 (+https://github.com/advancedchances/BTC-Indicator-Check)"
TIMEOUT = 20
THIN_VENUE_USD = 100_000  # 24h volume below this: confirm the price with a second source

SOURCES = [
    ("Coinbase Exchange (prices, candles)", "https://api.exchange.coinbase.com/products/BTC-USD/ticker"),
    ("Coinbase retail (spot price)", "https://api.coinbase.com/v2/prices/BTC-USD/spot"),
    ("Kraken (prices)", "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"),
    ("CoinGecko (market cap, supply)", "https://api.coingecko.com/api/v3/ping"),
    ("DefiLlama (fees, revenue, TVL)", "https://api.llama.fi/summary/fees/aave?dataType=dailyFees&excludeTotalDataChart=true"),
    ("DefiLlama coins (prices)", "https://coins.llama.fi/prices/current/coingecko:bitcoin"),
    ("DefiLlama stablecoins", "https://stablecoins.llama.fi/stablecoinchains"),
    ("Hyperliquid (perps, positioning)", "https://api.hyperliquid.xyz/info"),
    ("Blockscout Ethereum (on-chain)", "https://eth.blockscout.com/api/v2/stats"),
    ("mempool.space (BTC fees)", "https://mempool.space/api/v1/fees/recommended"),
    ("Binance spot mirror (candles fallback)", "https://data-api.binance.vision/api/v3/ping"),
    ("OKX (funding, OI history, liquidations)", "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP"),
    ("Bitget (funding, OI)", "https://api.bitget.com/api/v2/mix/market/current-fund-rate?symbol=BTCUSDT&productType=usdt-futures"),
    ("Gate (funding)", "https://api.gateio.ws/api/v4/futures/usdt/contracts/BTC_USDT"),
    ("dYdX (funding, OI)", "https://indexer.dydx.trade/v4/perpetualMarkets?ticker=BTC-USD"),
    ("Deribit (options, DVOL, basis)", "https://www.deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL"),
    ("alternative.me (Fear & Greed)", "https://api.alternative.me/fng/?limit=1"),
    ("CoinMetrics community (MVRV)", "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMVRVCur&page_size=1&paging_from=end"),
    ("CoinGecko global (dominance)", "https://api.coingecko.com/api/v3/global"),
    ("Yahoo Finance (DXY, S&P 500)", "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=5d&interval=1d"),
    ("Binance futures (funding, OI)", "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"),
    ("Bybit (funding, OI)", "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"),
]
POST_BODIES = {"https://api.hyperliquid.xyz/info": {"type": "allMids"}}
HL_INFO = "https://api.hyperliquid.xyz/info"


class FetchError(Exception):
    def __init__(self, kind, detail):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


def fetch(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        text = e.read()[:200].decode(errors="replace").strip()
        if e.code == 429:
            raise FetchError("rate-limited", "the site's free tier refused; wait or use another source")
        if e.code == 451 or (e.code == 403 and "country" in text.lower()):
            raise FetchError("geo-blocked", "the site refuses this region")
        if e.code in (401, 403):
            raise FetchError("refused by site", f"HTTP {e.code} {text[:80]} (API key or User-Agent rule)")
        raise FetchError("site error", f"HTTP {e.code} {text[:80]}")
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if "Tunnel connection failed" in reason and ("403" in reason or "407" in reason):
            raise FetchError("blocked by network policy", "the session's proxy denied this host; don't route around it")
        if "timed out" in reason.lower():
            raise FetchError("timeout", reason)
        raise FetchError("unreachable", reason)
    except TimeoutError as e:
        raise FetchError("timeout", str(e))
    except json.JSONDecodeError:
        raise FetchError("not JSON", "the response was not JSON (an HTML error page?)")


def cmd_check(args):
    rows = []
    for name, url in SOURCES:
        try:
            fetch(url, POST_BODIES.get(url))
            rows.append({"source": name, "status": "ok", "detail": ""})
        except FetchError as e:
            rows.append({"source": name, "status": e.kind, "detail": e.detail})
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print("| Source | Status | Detail |\n|---|---|---|")
    for r in rows:
        print(f"| {r['source']} | {r['status']} | {r['detail']} |")
    print(f"\nChecked {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC. Re-run in each new session; "
          "results change with network policy and rate limits.")


def cmd_prices(args):
    rows = []
    for sym in args.symbols:
        product = sym.upper() if "-" in sym else f"{sym.upper()}-USD"
        try:
            t = fetch(f"https://api.exchange.coinbase.com/products/{product}/ticker")
            if "price" not in t:
                raise FetchError("not listed", t.get("message", "no price in response"))
            price, vol = float(t["price"]), float(t.get("volume") or 0)
            rows.append({"symbol": sym.upper(), "product": product, "price": price, "volume_base_24h": vol,
                         "volume_usd_24h": price * vol, "time": t.get("time"),
                         "thin_venue": price * vol < THIN_VENUE_USD, "status": "ok"})
        except FetchError as e:
            kind = "not listed" if "delisted" in e.detail.lower() or "notfound" in e.detail.lower().replace(" ", "") else e.kind
            rows.append({"symbol": sym.upper(), "product": product, "status": kind, "detail": e.detail})
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print("| Symbol | Price (USD) | 24h volume (USD) | Thin venue | Time (UTC) | Status |\n|---|---|---|---|---|---|")
    for r in rows:
        if r["status"] == "ok":
            print(f"| {r['symbol']} | {r['price']:,.6g} | {r['volume_usd_24h']:,.0f} | {'yes' if r['thin_venue'] else '-'} "
                  f"| {r['time'][:19]} | ok |")
        else:
            print(f"| {r['symbol']} | n/a | n/a | n/a | - | {r['status']}: {r.get('detail', '')[:60]} |")
    print(f"\nSource: Coinbase Exchange public API. Thin venue = under ${THIN_VENUE_USD:,} of 24h volume.")


def parse_time(s):
    s = s.strip()
    fmt = "%Y-%m-%dT%H:%M" if "T" in s else "%Y-%m-%d"
    return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------- candle history with fallbacks

INTERVALS = {"1h": 3600, "6h": 21600, "1d": 86400}
# Tickers that differ by venue: {symbol: {venue: ticker}}
ALIASES = {"LIGHTER": {"hyperliquid": "LIT"}, "LIT": {"coinbase": "LIGHTER"}}


def _series(rows):
    """rows: [(t, o, h, l, c, v)] -> dict of lists, deduplicated and sorted by time."""
    rows = sorted({int(r[0]): r for r in rows}.values())
    keys = "tohlcv"
    return {k: [int(r[0]) if k == "t" else float(r[i]) for r in rows] for i, k in enumerate(keys)}


def _coinbase(symbol, interval, days):
    product = ALIASES.get(symbol, {}).get("coinbase", symbol) + "-USD"
    g = INTERVALS[interval]
    end = int(time.time()) // g * g + g
    stop, rows = end - days * 86400, []
    while end > stop:
        start = max(end - 300 * g, stop)
        data = fetch(f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={g}"
                     f"&start={dt.datetime.fromtimestamp(start, dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
                     f"&end={dt.datetime.fromtimestamp(end, dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
        if isinstance(data, dict):
            raise FetchError("not listed", data.get("message", "unknown product"))
        if not data and rows:
            break  # walked back past the listing date
        rows += [(c[0], c[3], c[2], c[1], c[4], c[5]) for c in data]
        end = start
        time.sleep(0.12)  # public limit is 10 requests a second
    return _series(rows)


def _binance(symbol, interval, days):
    pair = ALIASES.get(symbol, {}).get("binance", symbol) + "USDT"
    step = INTERVALS[interval] * 1000
    cursor, now, rows = int(time.time() * 1000) - days * 86400 * 1000, int(time.time() * 1000), []
    while cursor < now:
        data = fetch(f"https://data-api.binance.vision/api/v3/klines?symbol={pair}&interval={interval}"
                     f"&startTime={cursor}&limit=1000")
        if not data:
            break
        rows += [(k[0] // 1000, k[1], k[2], k[3], k[4], k[5]) for k in data]
        cursor = data[-1][0] + step
        if len(data) < 1000:
            break
    return _series(rows)


def _hyperliquid(symbol, interval, days):
    coin = ALIASES.get(symbol, {}).get("hyperliquid", symbol)
    now = int(time.time() * 1000)
    data = fetch(HL_INFO, {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                             "startTime": now - days * 86400 * 1000, "endTime": now}})
    return _series([(c["t"] // 1000, c["o"], c["h"], c["l"], c["c"], c["v"]) for c in data or []])


def _kraken(symbol, interval, days):
    pair = ALIASES.get(symbol, {}).get("kraken", symbol) + "USD"
    data = fetch(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={INTERVALS[interval] // 60}")
    if data.get("error"):
        raise FetchError("not listed", "; ".join(data["error"]))
    rows = next(v for k, v in data["result"].items() if k != "last")
    return _series([(r[0], r[1], r[2], r[3], r[4], r[6]) for r in rows])


def _yahoo(symbol, interval, days):
    """Stocks, ETFs and indexes from Yahoo Finance's chart API. Daily bars are stamped at 00:00 UTC of the
    exchange's trading date, and `forming_last` says whether that session is still open."""
    sym = ALIASES.get(symbol, {}).get("yahoo", symbol)
    end = int(time.time())
    start = end - min(days, 729 if interval == "1h" else days) * 86400
    # Explicit period1/period2: `range=max` silently switches to monthly bars.
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={start}&period2={end}"
           f"&interval={interval}&includePrePost=false")
    data = None
    for k in range(3):  # Yahoo rate-limits bursts
        try:
            data = fetch(url)
            break
        except FetchError as e:
            if e.kind != "rate-limited" or k == 2:
                raise
            time.sleep(1.5 * (k + 1))
    res = (data.get("chart") or {}).get("result") or []
    if not res or not res[0].get("timestamp"):
        raise FetchError("not listed", "no chart data for this symbol")
    res = res[0]
    if res["meta"].get("dataGranularity") not in (None, interval):
        raise FetchError("site error", f"Yahoo returned {res['meta'].get('dataGranularity')} bars, not {interval}")
    off = res["meta"].get("gmtoffset") or 0
    q = res["indicators"]["quote"][0]
    rows = []
    for t, o, h, l, c, v in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"]):
        if None in (o, h, l, c):
            continue
        if interval == "1d":
            local = t + off
            t = local - local % 86400
        rows.append((t, o, h, l, c, v or 0))
    s = _series(rows)
    if interval == "1d" and s["t"]:
        end = ((res["meta"].get("currentTradingPeriod") or {}).get("regular") or {}).get("end")
        session_day = (end + off) - (end + off) % 86400 if end else None
        s["forming_last"] = bool(end and time.time() < end and session_day == s["t"][-1])
    return s


CANDLE_SOURCES = (("Coinbase Exchange", _coinbase), ("Binance spot (USDT)", _binance),
                  ("Hyperliquid perp", _hyperliquid), ("Kraken", _kraken), ("Yahoo Finance", _yahoo))
CRYPTO_SOURCES = tuple(n for n, _ in CANDLE_SOURCES if n != "Yahoo Finance")


def load_candles(symbol, interval="1d", days=1500, sources=None, asset=None):
    """Candles for `symbol` from the first venue that lists it, oldest first.

    Returns (series, source_name). The last bar may still be forming; callers
    decide whether to drop it (see forming()). Venue order: Coinbase, Binance
    spot, Hyperliquid perps, Kraken (last 720 bars only), then Yahoo Finance
    for stocks and ETFs. asset="stock" goes straight to Yahoo; asset="crypto"
    never uses it. When the first venue that lists the symbol covers under 80%
    of the requested days (a recent listing), the other crypto venues are
    tried too and the longest history wins, because indicators like the
    200-week average need the depth.
    """
    if asset == "stock":
        sources = ("Yahoo Finance",)
    elif asset == "crypto":
        sources = CRYPTO_SOURCES
    symbol, errors, best = symbol.upper(), [], None
    wanted = days * 86400 / INTERVALS[interval]
    for name, fn in CANDLE_SOURCES:
        if sources and name not in sources:
            continue
        try:
            s = fn(symbol, interval, days)
        except FetchError as e:
            errors.append(f"{name}: {e.kind}")
            continue
        if len(s["t"]) < 2:
            errors.append(f"{name}: no candles")
            continue
        if name == "Yahoo Finance":
            if best is None:  # a stock or ETF: only when no crypto venue lists the symbol
                best = (s, name)
            break
        if best is None or len(s["t"]) > len(best[0]["t"]) * 1.2:
            best = (s, name)
        if len(best[0]["t"]) >= 0.8 * wanted:
            break
    if best:
        return best
    raise FetchError("no candles", f"{symbol}: " + "; ".join(errors))


def forming(series, interval="1d"):
    """True when the last candle's period has not ended yet (for stocks: its session is still open)."""
    if interval == "1d" and "forming_last" in series:
        return series["forming_last"]
    return bool(series["t"]) and series["t"][-1] + INTERVALS[interval] > time.time()


def cmd_candles(args):
    product = args.symbol.upper() if "-" in args.symbol else f"{args.symbol.upper()}-USD"
    start, end = parse_time(args.start), parse_time(args.end)
    if end <= start:
        sys.exit("--end must be after --start")
    out, cursor, step = [], start, dt.timedelta(seconds=args.granularity * 300)  # Coinbase returns up to 300 candles
    while cursor < end:
        chunk_end = min(cursor + step, end)
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={args.granularity}"
               f"&start={cursor:%Y-%m-%dT%H:%M:%SZ}&end={chunk_end:%Y-%m-%dT%H:%M:%SZ}")
        try:
            data = fetch(url)
        except FetchError as e:
            sys.exit(f"{product}: {e}")
        if isinstance(data, dict):
            sys.exit(f"{product}: {data.get('message', data)}")
        out += data
        cursor = chunk_end
    rows = sorted({c[0]: c for c in out}.values())  # [time, low, high, open, close, volume]
    rows = [{"time": dt.datetime.fromtimestamp(c[0], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "open": c[3], "high": c[2], "low": c[1], "close": c[4], "volume": c[5]}
            for c in rows if start.timestamp() <= c[0] < end.timestamp()]
    if args.json:
        print(json.dumps({"product": product, "granularity": args.granularity, "candles": rows}, indent=2))
        return
    print("time,open,high,low,close,volume")
    for r in rows:
        print(f"{r['time']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}")


def windows(chart):
    """30-day sums from DefiLlama's daily chart: latest, prior, and the same 30 days a year earlier."""
    if not chart:
        return {}
    end = chart[-1][0] + 86400
    day = 86400

    def total(a, b):
        return sum(v for t, v in chart if a <= t < b)

    first = chart[0][0]
    last30, prior30 = total(end - 30 * day, end), total(end - 60 * day, end - 30 * day)
    year_ago = total(end - 395 * day, end - 365 * day) if first <= end - 395 * day else None
    return {
        "last_day": dt.datetime.fromtimestamp(chart[-1][0], dt.timezone.utc).strftime("%Y-%m-%d"),
        "30d": last30,
        "prior_30d": prior30,
        "30d_year_ago": year_ago,
        "365d": total(end - 365 * day, end) if first <= end - 365 * day else None,
        "vs_prior": last30 / prior30 - 1 if prior30 else None,
        "vs_year_ago": last30 / year_ago - 1 if year_ago else None,
    }


def cmd_revenue(args):
    results = []
    for slug in args.slugs:
        row = {"slug": slug}
        for key, dtype in (("fees", "dailyFees"), ("revenue", "dailyRevenue"), ("holders_revenue", "dailyHoldersRevenue")):
            try:
                j = fetch(f"https://api.llama.fi/summary/fees/{slug}?dataType={dtype}")
                row[key] = windows(j.get("totalDataChart") or [])
                row.setdefault("name", j.get("name"))
            except FetchError as e:
                row[key] = {"error": e.kind if e.kind != "site error" else "no data for this slug or measure"}
        results.append(row)
    if args.json:
        print(json.dumps(results, indent=2))
        return

    def m(x):
        return "n/a" if x is None else f"${x / 1e6:,.2f}M"

    def p(x):
        return "n/a" if x is None else f"{x * 100:+.0f}%"

    print("| Protocol | Measure | Last 30d | Prior 30d | vs prior | Same 30d a year ago | vs year ago | Trailing 365d | Data through |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        for key in ("fees", "revenue", "holders_revenue"):
            w = r.get(key, {})
            if "error" in w or not w:
                print(f"| {r.get('name') or r['slug']} | {key} | {w.get('error', 'no data')} | | | | | | |")
                continue
            print(f"| {r.get('name') or r['slug']} | {key} | {m(w['30d'])} | {m(w['prior_30d'])} | {p(w['vs_prior'])} "
                  f"| {m(w['30d_year_ago'])} | {p(w['vs_year_ago'])} | {m(w['365d'])} | {w['last_day']} |")
    print("\nSource: DefiLlama /summary/fees. Revenue = what the protocol keeps; holders revenue = what reaches token "
          "holders (buybacks, burns, distributions). The latest day may be partial.")


def cmd_mcaps(args):
    ids = [i.strip().lower() for i in args.ids]
    try:
        data = fetch("https://coins.llama.fi/mcaps", {"coins": [f"coingecko:{i}" for i in ids]})
    except FetchError as e:
        sys.exit(f"mcaps: {e}")
    rows = []
    for i in ids:
        d = data.get(f"coingecko:{i}") or {}
        ts = d.get("timestamp")
        rows.append({"id": i, "market_cap": d.get("mcap"),
                     "as_of": dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else None})
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print("| CoinGecko id | Market cap (USD) | As of (UTC) |\n|---|---|---|")
    for r in rows:
        mc = "not found (check the id)" if r["market_cap"] is None else f"${r['market_cap'] / 1e9:,.3f}B"
        print(f"| {r['id']} | {mc} | {r['as_of'] or '-'} |")
    print("\nSource: CoinGecko circulating market cap, served by DefiLlama's coins API.")


HL_ASSISTANCE_FUND = "0xfefefefefefefefefefefefefefefefefefefefe"


def cmd_hl_buybacks(args):
    """HYPE held by Hyperliquid's Assistance Fund, which buys HYPE with protocol fees (what ASXN charts)."""
    try:
        st = fetch(HL_INFO, {"type": "spotClearinghouseState", "user": HL_ASSISTANCE_FUND})
        mids = fetch(HL_INFO, {"type": "allMids"})
    except FetchError as e:
        sys.exit(f"hl-buybacks: {e}")
    bal = next((b for b in st.get("balances", []) if b["coin"] == "HYPE"), None)
    if not bal:
        sys.exit("hl-buybacks: no HYPE balance found at the Assistance Fund address")
    held, price = float(bal["total"]), float(mids.get("HYPE") or 0)
    out = {"address": HL_ASSISTANCE_FUND, "hype_held": held, "hype_price": price, "value_usd": held * price,
           "as_of": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")}
    if args.json:
        print(json.dumps(out, indent=2))
        return
    print(f"Hyperliquid Assistance Fund ({HL_ASSISTANCE_FUND}) holds {held:,.0f} HYPE, worth ${held * price / 1e9:,.2f}B at "
          f"${price:,.2f} ({out['as_of']} UTC). Run it again later and diff the balance to measure buybacks over that window; "
          "compare with DefiLlama's holders revenue for Hyperliquid.")


def _json_anywhere(sub):
    """Let --json follow the subcommand too (`liqmap BTC --json`), not only precede it."""
    for sp in sub.choices.values():
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="emit JSON")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="test which data sources this session can reach")
    sp = sub.add_parser("prices", help="Coinbase Exchange spot prices")
    sp.add_argument("symbols", nargs="+")
    sc = sub.add_parser("candles", help="Coinbase Exchange candles as CSV")
    sc.add_argument("symbol")
    sc.add_argument("--start", required=True, help="YYYY-MM-DD or YYYY-MM-DDTHH:MM (UTC)")
    sc.add_argument("--end", required=True, help="YYYY-MM-DD or YYYY-MM-DDTHH:MM (UTC)")
    sc.add_argument("--granularity", type=int, default=86400, choices=[60, 300, 900, 3600, 21600, 86400])
    sr = sub.add_parser("revenue", help="DefiLlama fees, revenue and holders revenue")
    sr.add_argument("slugs", nargs="+")
    sm = sub.add_parser("mcaps", help="CoinGecko market caps via DefiLlama")
    sm.add_argument("ids", nargs="+")
    sub.add_parser("hl-buybacks", help="HYPE held by Hyperliquid's buyback (Assistance Fund) address")
    _json_anywhere(sub)
    args = p.parse_args()
    {"check": cmd_check, "prices": cmd_prices, "candles": cmd_candles, "revenue": cmd_revenue,
     "mcaps": cmd_mcaps, "hl-buybacks": cmd_hl_buybacks}[args.cmd](args)


if __name__ == "__main__":
    main()
