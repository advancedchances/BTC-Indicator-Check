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
  base_price      USD price quoted in the earlier report
  current_price   USD price now
  thin_venue      Optional. "yes" when the current price comes from a venue with
                  little volume and no second source agrees

Numbers may be written as 1234.5, $1.2k or 0.0043. The benchmark row
(--benchmark, default BTC) must be present.

Usage:
  python call_scorecard.py calls.csv
  python call_scorecard.py calls.csv --benchmark ETH --json
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
from valuation_calc import parse_num  # noqa: E402

FIELDS = ["ticker", "group", "base_price", "current_price", "thin_venue"]


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
        if not base or now is None:
            sys.exit(f"row {i} ({ticker}): base_price and current_price are required and base_price must be > 0")
        rows.append({"ticker": ticker,
                     "group": str(r.get("group") or "").strip() or "Ungrouped",
                     "base_price": base, "current_price": now,
                     "thin_venue": str(r.get("thin_venue") or "").strip().lower() in ("y", "yes", "true", "1")})
    return rows


def score(rows, benchmark):
    bench = next((r for r in rows if r["ticker"] == benchmark), None)
    if bench is None:
        sys.exit(f"benchmark {benchmark} not found in the input")
    b_ret = bench["current_price"] / bench["base_price"] - 1
    for r in rows:
        r["return"] = r["current_price"] / r["base_price"] - 1
        r["vs_benchmark"] = r["return"] - b_ret
    groups = {}
    for r in rows:
        if r["ticker"] == benchmark:
            continue
        groups.setdefault(r["group"], []).append(r)
    summary = []
    for g, members in groups.items():
        rets = [m["return"] for m in members]
        summary.append({"group": g, "count": len(members),
                        "basket_return": sum(rets) / len(rets),
                        "basket_vs_benchmark": sum(rets) / len(rets) - b_ret,
                        "beat_benchmark": sum(m["vs_benchmark"] > 0 for m in members),
                        "best": max(members, key=lambda m: m["return"])["ticker"],
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
    lines += ["", f"| Group | Names | Equal-weight return | vs {benchmark} | Beat {benchmark} | Best | Worst |",
              "|---|---|---|---|---|---|---|"]
    for s in summary:
        lines.append(f"| {s['group']} | {s['count']} | {pct(s['basket_return'])} | {pct(s['basket_vs_benchmark'])} "
                     f"| {s['beat_benchmark']} of {s['count']} | {s['best']} | {s['worst']} |")
    lines += ["", f"{benchmark} itself: {pct(b_ret)}. Equal-weight baskets, no fees or slippage. "
                  "Short windows are noise: say how many days the window covers."]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", nargs="?", help="CSV or JSON file, or '-' for stdin")
    p.add_argument("--benchmark", default="BTC", help="ticker to measure against (default BTC)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of markdown tables")
    p.add_argument("--template", action="store_true", help="print an empty CSV header and exit")
    args = p.parse_args()

    if args.template:
        print(",".join(FIELDS))
        return
    if not args.input:
        p.error("input file required (or use --template)")

    rows = load_rows(args.input)
    benchmark = args.benchmark.upper()
    b_ret, summary = score(rows, benchmark)
    if args.json:
        print(json.dumps({"benchmark": benchmark, "benchmark_return": b_ret, "rows": rows, "groups": summary}, indent=2))
    else:
        print(to_markdown(rows, summary, benchmark, b_ret))


if __name__ == "__main__":
    main()
