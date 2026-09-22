#!/usr/bin/env python3
"""
Compute valuation ratios, sector-peer comparisons and screening flags for
crypto candidates from raw inputs you have already researched.

Standard library only. Missing inputs stay missing: any ratio that depends
on a blank field is reported as "n/a" and never treated as zero.

Input: a CSV (or a JSON list of objects) with these columns. Only `asset`
is required; fill in whatever you have.

  asset              Name
  ticker             Symbol
  sector             Peer group used for median comparisons (e.g. "DEX", "Lending")
  price              USD
  market_cap         USD, circulating
  fdv                USD, fully diluted
  fees_30d           USD, total fees paid by users over the last 30 days
  revenue_30d        USD, revenue kept by protocol / token holders over the last 30 days
  revenue_30d_prior  USD, the same measure for the previous 30-day window
  incentives_30d     USD value of token incentives paid over the last 30 days
  tvl                USD
  active_users_30d   Monthly active users / addresses
  unlocks_12m_usd    USD value of tokens unlocking in the next 12 months

Numbers may be written as 1234567, $1.2M, 850k, 3.4B or 1.1T. In a CSV,
quote any number containing commas ("1,234,567").

Usage:
  python valuation_calc.py --template > candidates.csv
  python valuation_calc.py candidates.csv
  python valuation_calc.py candidates.csv --json
  cat candidates.csv | python valuation_calc.py -
"""

import argparse
import csv
import json
import statistics
import sys

FIELDS = [
    "asset", "ticker", "sector", "price", "market_cap", "fdv",
    "fees_30d", "revenue_30d", "revenue_30d_prior", "incentives_30d",
    "tvl", "active_users_30d", "unlocks_12m_usd",
]
NUMERIC = FIELDS[3:]

# Screening heuristics: they prompt a closer look, they are not verdicts.
LOW_FLOAT_BELOW = 0.30         # market cap / FDV
UNLOCK_OVERHANG_AT = 0.10      # 12-month unlocks / market cap
REV_DECLINE_BELOW = -0.20      # 30d revenue vs. prior 30d

SUFFIXES = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("$", "").replace("_", "")
    if s.lower() in ("", "n/a", "na", "none", "null", "-", "?"):
        return None
    mult = 1.0
    if s[-1].lower() in SUFFIXES:
        mult = SUFFIXES[s[-1].lower()]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        raise ValueError(f"cannot parse number: {value!r}")


def div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def load_rows(path):
    stream = sys.stdin if path == "-" else open(path, newline="", encoding="utf-8")
    with stream:
        text = stream.read()
    if text.lstrip().startswith("["):
        raw = json.loads(text)
    else:
        raw = list(csv.DictReader(text.splitlines()))
    rows = []
    for i, r in enumerate(raw, 1):
        if None in r:
            sys.exit(f"row {i} has more columns than the header; quote numbers that contain "
                     f"commas (\"1,200,000\") or write them as 1.2M")
        r = {k.strip(): v for k, v in r.items() if k}
        if not (r.get("asset") or "").strip():
            continue
        row = {"asset": r["asset"].strip(),
               "ticker": (r.get("ticker") or "").strip(),
               "sector": (r.get("sector") or "").strip() or "Unclassified"}
        for f in NUMERIC:
            try:
                row[f] = parse_num(r.get(f))
            except ValueError as e:
                sys.exit(f"row {i} ({row['asset']}), {f}: {e}")
        rows.append(row)
    return rows


def compute(row):
    mc, fdv = row["market_cap"], row["fdv"]
    rev, prior = row["revenue_30d"], row["revenue_30d_prior"]
    ann_rev = rev * 365 / 30 if rev is not None else None
    ann_fees = row["fees_30d"] * 365 / 30 if row["fees_30d"] is not None else None
    earnings = rev - row["incentives_30d"] if rev is not None and row["incentives_30d"] is not None else None

    m = {
        "float": div(mc, fdv),
        "ann_revenue": ann_rev,
        "mc_rev": div(mc, ann_rev),
        "fdv_rev": div(fdv, ann_rev),
        "mc_fees": div(mc, ann_fees),
        "mc_tvl": div(mc, row["tvl"]),
        "mc_per_user": div(mc, row["active_users_30d"]),
        "earnings_30d": earnings,
        "rev_growth": div(rev, prior) - 1 if div(rev, prior) is not None else None,
        "unlock_overhang": div(row["unlocks_12m_usd"], mc),
    }

    flags = []
    if m["float"] is not None and m["float"] < LOW_FLOAT_BELOW:
        flags.append("LOW_FLOAT")
    if m["unlock_overhang"] is not None and m["unlock_overhang"] >= UNLOCK_OVERHANG_AT:
        flags.append("UNLOCK_OVERHANG")
    if earnings is not None and earnings < 0:
        flags.append("SUBSIDIZED")
    if m["rev_growth"] is not None and m["rev_growth"] < REV_DECLINE_BELOW:
        flags.append("REV_DECLINING")
    if rev is None and row["fees_30d"] is None:
        flags.append("NO_REVENUE_DATA")
    m["flags"] = flags
    return m


def add_peer_comparison(rows):
    """MC/revenue premium (+) or discount (-) vs. the median of *other* assets in the same sector."""
    for row in rows:
        peers = [r["metrics"]["mc_rev"] for r in rows
                 if r is not row and r["sector"] == row["sector"]
                 and r["metrics"]["mc_rev"] is not None and r["metrics"]["mc_rev"] > 0]
        own = row["metrics"]["mc_rev"]
        median = statistics.median(peers) if peers else None
        row["metrics"]["peer_median_mc_rev"] = median
        row["metrics"]["peer_count"] = len(peers)
        row["metrics"]["vs_peers"] = own / median - 1 if own is not None and own > 0 and median else None


def fmt_usd(x):
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    x = abs(x)
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= cut:
            return f"{sign}${x / cut:.2f}{suffix}"
    return f"{sign}${x:,.2f}" if x < 100 else f"{sign}${x:,.0f}"


def fmt_x(x):
    return "n/a" if x is None else f"{x:,.1f}x"


def fmt_pct(x, signed=False):
    if x is None:
        return "n/a"
    return f"{x * 100:+.0f}%" if signed else f"{x * 100:.0f}%"


def to_markdown(rows):
    headers = ["Asset", "Sector", "Mkt Cap", "FDV", "Float", "Ann. Rev", "MC/Rev",
               "FDV/Rev", "MC/Fees", "MC/TVL", "MC/User", "Earnings 30d",
               "Rev Growth", "Unlock 12m", "MC/Rev vs Peers", "Flags"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        m = r["metrics"]
        name = f"{r['asset']} ({r['ticker']})" if r["ticker"] else r["asset"]
        vs = "n/a" if m["vs_peers"] is None else f"{fmt_pct(m['vs_peers'], True)} (n={m['peer_count']})"
        cells = [name, r["sector"], fmt_usd(r["market_cap"]), fmt_usd(r["fdv"]),
                 fmt_pct(m["float"]), fmt_usd(m["ann_revenue"]), fmt_x(m["mc_rev"]),
                 fmt_x(m["fdv_rev"]), fmt_x(m["mc_fees"]), fmt_x(m["mc_tvl"]),
                 fmt_usd(m["mc_per_user"]), fmt_usd(m["earnings_30d"]),
                 fmt_pct(m["rev_growth"], True), fmt_pct(m["unlock_overhang"]), vs,
                 ", ".join(m["flags"]) or "-"]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Annualized = 30d x 365/30. Peer comparison uses the median MC/Rev of other assets "
                 "in the same sector; with n<3 treat it as weak. Flags are screening heuristics: "
                 f"LOW_FLOAT (<{LOW_FLOAT_BELOW:.0%} of FDV circulating), UNLOCK_OVERHANG "
                 f"(>={UNLOCK_OVERHANG_AT:.0%} of MC unlocking in 12m), SUBSIDIZED (incentives > revenue), "
                 f"REV_DECLINING (<{REV_DECLINE_BELOW:.0%} vs prior 30d), NO_REVENUE_DATA.")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", nargs="?", help="CSV or JSON file, or '-' for stdin")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a markdown table")
    p.add_argument("--template", action="store_true", help="print an empty CSV header and exit")
    args = p.parse_args()

    if args.template:
        print(",".join(FIELDS))
        return
    if not args.input:
        p.error("input file required (or use --template)")

    rows = load_rows(args.input)
    if not rows:
        sys.exit("no rows with an 'asset' value found")
    for row in rows:
        row["metrics"] = compute(row)
    add_peer_comparison(rows)
    rows.sort(key=lambda r: (r["sector"].lower(),
                             r["metrics"]["mc_rev"] if r["metrics"]["mc_rev"] is not None else float("inf")))

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(to_markdown(rows))


if __name__ == "__main__":
    main()
