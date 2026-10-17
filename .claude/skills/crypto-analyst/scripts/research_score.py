#!/usr/bin/env python3
"""
Compute Research Scores from per-dimension ratings so every finalist's score
is reproducible, shows its full breakdown, and has its caps applied the same
way every time.

Standard library only. Weights and caps follow references/report-template.md.

Input: a CSV (or a JSON list of objects) with these columns:

  asset, ticker
  traction        Organic traction, 1-10              (weight 25%)
  value_accrual   Value accrual & tokenomics, 1-10    (weight 20%)
  valuation       Relative valuation, 1-10            (weight 20%)
  catalysts       Catalysts, 1-10                     (weight 15%)
  moat            Competitive position, 1-10          (weight 10%)
  evidence        Evidence quality, 1-10              (weight 10%)
  caps            Optional. Cap codes separated by ";" (see CAPS below)
  previous        Optional. The score from the last report, to show the change
  note            Optional. One line on what moved the score

Cap codes:
  EXPLOIT_OR_ENFORCEMENT  active enforcement, or an unresolved exploit / solvency issue -> max 5
  UNLOCK_6M               >25% of circulating supply unlocking within 6 months, no credible absorption -> max 6
  MULTISIG_NO_TIMELOCK    critical contracts upgradeable by a small multisig with no timelock -> max 7
  UNVERIFIABLE_METRIC     key thesis metric not independently verifiable -> max 7

Usage:
  python research_score.py --template > scores.csv
  python research_score.py scores.csv
  python research_score.py scores.csv --json
"""

import argparse
import csv
import json
import sys
from decimal import Decimal, ROUND_HALF_UP

WEIGHTS = {
    "traction": Decimal("0.25"),
    "value_accrual": Decimal("0.20"),
    "valuation": Decimal("0.20"),
    "catalysts": Decimal("0.15"),
    "moat": Decimal("0.10"),
    "evidence": Decimal("0.10"),
}
CAPS = {
    "EXPLOIT_OR_ENFORCEMENT": 5,
    "UNLOCK_6M": 6,
    "MULTISIG_NO_TIMELOCK": 7,
    "UNVERIFIABLE_METRIC": 7,
}
FIELDS = ["asset", "ticker", *WEIGHTS, "caps", "previous", "note"]


def band(score):
    if score >= 8:
        return "Strong"
    if score >= 6:
        return "Promising, with gaps"
    return "Below finalist bar"


def one_dp(x):
    return x.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def load_rows(path):
    stream = sys.stdin if path == "-" else open(path, newline="", encoding="utf-8")
    with stream:
        text = stream.read()
    raw = json.loads(text) if text.lstrip().startswith("[") else list(csv.DictReader(text.splitlines()))
    rows = []
    for i, r in enumerate(raw, 1):
        if None in r:
            sys.exit(f"row {i} has more columns than the header; quote fields that contain commas")
        r = {k.strip(): v for k, v in r.items() if k}
        asset = str(r.get("asset") or "").strip()
        if not asset:
            continue
        row = {"asset": asset, "ticker": str(r.get("ticker") or "").strip(),
               "note": str(r.get("note") or "").strip()}
        for dim in WEIGHTS:
            try:
                v = Decimal(str(r.get(dim)).strip())
            except Exception:
                sys.exit(f"row {i} ({asset}): {dim} is missing or not a number")
            if not 1 <= v <= 10:
                sys.exit(f"row {i} ({asset}): {dim} must be between 1 and 10, got {v}")
            row[dim] = v
        codes = [c.strip().upper() for c in str(r.get("caps") or "").replace(",", ";").split(";") if c.strip()]
        unknown = [c for c in codes if c not in CAPS]
        if unknown:
            sys.exit(f"row {i} ({asset}): unknown cap code(s) {unknown}; use {sorted(CAPS)}")
        row["caps"] = codes
        prev = str(r.get("previous") or "").strip()
        row["previous"] = Decimal(prev) if prev else None
        rows.append(row)
    return rows


def score(row):
    weighted = one_dp(sum(row[d] * w for d, w in WEIGHTS.items()))
    cap = min((CAPS[c] for c in row["caps"]), default=None)
    final = min(weighted, Decimal(cap).quantize(Decimal("0.1"))) if cap is not None else weighted
    return {
        "weighted": weighted,
        "cap": cap,
        "cap_codes": row["caps"],
        "score": final,
        "band": band(final),
        "change": final - row["previous"] if row["previous"] is not None else None,
    }


def to_markdown(rows):
    headers = ["Asset", "Traction", "Value", "Valuation", "Catalysts", "Moat", "Evidence",
               "Weighted", "Cap", "Score", "Band", "Change"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        s = r["result"]
        name = f"{r['asset']} ({r['ticker']})" if r["ticker"] else r["asset"]
        cap = "-" if s["cap"] is None else f"{s['cap']} ({', '.join(s['cap_codes'])})"
        chg = "n/a" if s["change"] is None else f"{s['change']:+.1f}"
        dims = [f"{r[d]:g}" for d in WEIGHTS]
        lines.append("| " + " | ".join([name, *dims, f"{s['weighted']}", cap, f"**{s['score']}**",
                                        s["band"], chg]) + " |")
    lines.append("")
    lines.append("Weights: traction 25%, value accrual 20%, valuation 20%, catalysts 15%, moat 10%, "
                 "evidence 10%. The lowest applicable cap wins. Bands: 8-10 strong, 6-7.9 promising "
                 "with gaps, below 6 normally not a finalist.")
    notes = [f"- **{r['asset']}:** {r['note']}" for r in rows if r["note"]]
    if notes:
        lines += ["", *notes]
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
        row["result"] = score(row)
    rows.sort(key=lambda r: (-r["result"]["score"], -r["result"]["weighted"]))

    if args.json:
        print(json.dumps(rows, indent=2, default=lambda d: float(d) if isinstance(d, Decimal) else str(d)))
    else:
        print(to_markdown(rows))


if __name__ == "__main__":
    main()
