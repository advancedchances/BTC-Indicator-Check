#!/usr/bin/env python3
"""
Score earlier calls: how each finalist, watchlist name and eliminated
candidate has moved since a report, against a benchmark (BTC by default),
and how each group did as an equal-weighted basket.

Standard library only. Use it when the user asks to update, re-check or
grade a previous scan. A few weeks of returns is noise, not validation, so
report the window length next to the result.

Input: a CSV (or a JSON list of objects) with these columns:

  ticker          Symbol
  group           e.g. Finalist, Watchlist, Eliminated, Benchmark
  reason          Optional. Why the name landed in its group, as a short rule code
                  (e.g. UNLOCK, NO_HOLDER_REVENUE, POW_NO_CASHFLOW, RICH_MULTIPLE)
  base_price      USD price quoted in the earlier report
  base_time       Optional. When base_price was taken (UTC), for the record
  horizon_days    Optional. The grading horizon set at report time
  current_price   USD price now (leave blank with --fetch)
  thin_venue      Optional. "yes" when the current price comes from a venue with
                  little volume and no second source agrees

Numbers may be written as 1234.5, $1.2k or 0.0043. The benchmark row
(--benchmark, default BTC) must be present.

Write this file at report time (a calls ledger) so a follow-up is one command.
Medians and hit rates sit next to equal-weight means because one outlier can
flip a mean: in the 25 Sep 2026 scan, one name doubling turned a -8.7% group
into +2.4%.

Usage:
  python call_scorecard.py calls.csv
  python call_scorecard.py calls.csv --fetch            # fill current prices (Coinbase first, then fallbacks)
  python call_scorecard.py calls.csv --by reason        # grade each elimination rule
  python call_scorecard.py calls.csv --benchmark ETH --json
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
from ta_core import median  # noqa: E402
from valuation_calc import parse_num  # noqa: E402

FIELDS = ["ticker", "group", "reason", "base_price", "base_time", "horizon_days", "current_price", "thin_venue"]


def load_rows(path):
    stream = sys.stdin if path == "-" else open(path, newline="", encoding="utf-8")
    with stream:
        text = stream.read()
    raw = json.loads(text) if text.lstrip().startswith("[") else list(csv.DictReader(text.splitlines()))
    rows = []
    for i, r in enumerate(raw, 1):
        if None in r:
            sys.exit(f"row {i} has more columns than the header; quote numbers that contain commas")
        r = {k.strip(): v for k, v in r.items() if k}
        ticker = str(r.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            base, now = parse_num(r.get("base_price")), parse_num(r.get("current_price"))
        except ValueError as e:
            sys.exit(f"row {i} ({ticker}): {e}")
        if not base:
            sys.exit(f"row {i} ({ticker}): base_price is required and must be > 0")
        rows.append({"ticker": ticker,
                     "group": str(r.get("group") or "").strip() or "Ungrouped",
                     "reason": str(r.get("reason") or "").strip() or "-",
                     "base_price": base, "current_price": now,
                     "base_time": str(r.get("base_time") or "").strip() or None,
                     "horizon_days": parse_num(r.get("horizon_days")),
                     "thin_venue": str(r.get("thin_venue") or "").strip().lower() in ("y", "yes", "true", "1")})
    return rows


def fill_prices(rows):
    from market_data import FetchError, load_candles
    for r in rows:
        if r["current_price"] is not None:
            continue
        try:
            s, src = load_candles(r["ticker"], "1d", 3)
            r["current_price"], r["price_source"] = s["c"][-1], src
        except FetchError as e:
            sys.exit(f"{r['ticker']}: no current price ({e}); fill current_price by hand")


def score(rows, benchmark, by="group"):
    bench = next((r for r in rows if r["ticker"] == benchmark), None)
    if bench is None:
        sys.exit(f"benchmark {benchmark} not found in the input")
    missing = [r["ticker"] for r in rows if r["current_price"] is None]
    if missing:
        sys.exit(f"no current_price for {', '.join(missing)}: fill it in or pass --fetch")
    b_ret = bench["current_price"] / bench["base_price"] - 1
    for r in rows:
        r["return"] = r["current_price"] / r["base_price"] - 1
        r["vs_benchmark"] = r["return"] - b_ret
    groups = {}
    for r in rows:
        if r["ticker"] == benchmark:
            continue
        key = r[by] if by == "group" else f"{r['group']}: {r['reason']}"
        groups.setdefault(key, []).append(r)
    summary = []
    for g, members in groups.items():
        rets = [m["return"] for m in members]
        best = max(members, key=lambda m: m["return"])
        rest = [m["return"] for m in members if m is not best]
        summary.append({"group": g, "count": len(members),
                        "basket_return": sum(rets) / len(rets),
                        "median_return": median(rets),
                        "mean_without_best": sum(rest) / len(rest) if rest else None,
                        "basket_vs_benchmark": sum(rets) / len(rets) - b_ret,
                        "beat_benchmark": sum(m["vs_benchmark"] > 0 for m in members),
                        "best": best["ticker"],
                        "worst": min(members, key=lambda m: m["return"])["ticker"]})
    return b_ret, summary


def pct(x):
    return f"{x * 100:+.1f}%"


def to_markdown(rows, summary, benchmark, b_ret):
    lines = ["| Ticker | Group | Then | Now | Return | vs " + benchmark + " |", "|---|---|---|---|---|---|"]
    order = {g: i for i, g in enumerate(dict.fromkeys(r["group"] for r in rows))}
    for r in sorted(rows, key=lambda r: (order[r["group"]], -r["return"])):
        flag = " (thin venue)" if r["thin_venue"] else ""
        lines.append(f"| {r['ticker']}{flag} | {r['group']} | {r['base_price']:,.6g} | {r['current_price']:,.6g} "
                     f"| {pct(r['return'])} | {pct(r['vs_benchmark'])} |")
    lines += ["", f"| Group | Names | Equal-weight return | Median | Mean without best | vs {benchmark} | Beat {benchmark} | Best | Worst |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        nb = pct(s["mean_without_best"]) if s["mean_without_best"] is not None else "n/a"
        lines.append(f"| {s['group']} | {s['count']} | {pct(s['basket_return'])} | {pct(s['median_return'])} | {nb} "
                     f"| {pct(s['basket_vs_benchmark'])} | {s['beat_benchmark']} of {s['count']} | {s['best']} | {s['worst']} |")
    lines += ["", f"{benchmark} itself: {pct(b_ret)}. Equal-weight baskets, no fees or slippage. "
                  "Short windows are noise: say how many days the window covers."]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", nargs="?", help="CSV or JSON file, or '-' for stdin")
    p.add_argument("--benchmark", default="BTC", help="ticker to measure against (default BTC)")
    p.add_argument("--by", choices=["group", "reason"], default="group",
                   help="summarize by group, or by group and reason to grade each elimination rule")
    p.add_argument("--fetch", action="store_true", help="fill blank current prices from Coinbase (then Binance, Hyperliquid, Kraken)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of markdown tables")
    p.add_argument("--template", action="store_true", help="print an empty CSV header and exit")
    args = p.parse_args()

    if args.template:
        print(",".join(FIELDS))
        return
    if not args.input:
        p.error("input file required (or use --template)")

    rows = load_rows(args.input)
    if args.fetch:
        fill_prices(rows)
    benchmark = args.benchmark.upper()
    b_ret, summary = score(rows, benchmark, args.by)
    if args.json:
        print(json.dumps({"benchmark": benchmark, "benchmark_return": b_ret, "rows": rows, "groups": summary}, indent=2))
    else:
        print(to_markdown(rows, summary, benchmark, b_ret))


if __name__ == "__main__":
    main()
