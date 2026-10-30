#!/usr/bin/env python3
"""
Grade a trade journal: which setups, confidence grades, trade types and
holding habits actually made money. Standard library only.

Usage:
  python journal_stats.py trades.csv                 # markdown report
  python journal_stats.py q1.csv q3.csv --json       # several exports at once
  python journal_stats.py trades.csv --min-n 5       # hide groups with fewer trades

Input: one or more CSV exports, one row per closed trade. Columns are found
by name, case-insensitively, so a typical journal works without changes:

  outcome      W / L / SL / BE / Big W ... (used only when pnl is missing)
  pnl          realized return of the trade, as 0.05, 5% or 5
  entry        entry price            stop     stop-loss price
  open date    exit date              ticker
  rationale    setup tags, comma separated ("Cloud - TK Cross, Chart Pattern - iHS")
  type         Breakout / Trend / ...  setup confidence   A++ / A / B ...
  % port       position size as a share of the account
  actual hold time (days)             expected hold time
  notes        free text; mentions of "neckline" or "stall" are tagged

Rows without a realized return are skipped (open trades, blank template rows).
R multiple = realized return / planned risk, where planned risk is
|entry - stop| / entry. A trade's account impact = return x position size.

Groups are ranked by expectancy (average return per trade). Win rates carry
a 90% lower bound (Wilson), because a 70% win rate on six trades proves
little. Profit factor = gross gains / gross losses.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
from ta_core import median, wilson_lower  # noqa: E402

COLUMNS = {
    "outcome": ("outcome", "result"),
    "pnl": ("pnl", "p&l", "return", "pnl %"),
    "entry": ("entry / cost basis", "entry", "entry price"),
    "stop": ("stop loss", "stop"),
    "ticker": ("ticker", "symbol"),
    "open": ("open date", "entry date", "opened"),
    "close": ("exit date", "close date", "closed"),
    "tags": ("rationale", "setup", "setups", "tags"),
    "type": ("type",),
    "confidence": ("setup confidence", "confidence", "grade"),
    "size": ("% port", "size %", "position %"),
    "hold": ("actual hold time (days)", "hold days", "days held"),
    "expected": ("expected hold time", "expected hold"),
    "notes": ("notes", "note", "comment"),
    "rr": ("r:r", "rr", "reward/risk"),
}
# Common journal shorthand, so "iHS", "Chart Pattern - iHS" and "ihs" count as one setup.
TAG_ALIASES = (("ihs", "Inverse head and shoulders"), ("inverse head", "Inverse head and shoulders"),
               ("e2e", "Cloud edge-to-edge"), ("edge to edge", "Cloud edge-to-edge"),
               ("tk cross", "Cloud TK cross"), ("cloud", "Cloud breakout"),
               ("falling wedge", "Falling wedge"), ("fw", "Falling wedge"),
               ("asc tri", "Ascending triangle"), ("ascending", "Ascending triangle"),
               ("c&h", "Cup and handle"), ("cup", "Cup and handle"), ("pennant", "Pennant"), ("flag", "Flag"),
               ("rounded bottom", "Rounded bottom"), ("double bottom", "Double bottom"), ("w", "Double bottom"),
               ("tri", "Triangle"))
CRYPTO = {"BTC", "ETH", "SOL", "ZEC", "XMR", "HYPE", "PENGU", "SKY", "LIGHTER", "LIT", "DOGE", "XRP", "ADA", "LINK",
          "AVAX", "SUI", "PUMP", "AAVE", "ENA", "TAO", "FARTCOIN", "BONK", "WIF", "PEPE", "LTC", "BCH", "NEAR", "ONDO"}
OPTION_RE = re.compile(r"\b\d+(\.\d+)?[CP]\b|\b(CALL|PUT)S?\b", re.I)


def parse_num(x):
    if x is None:
        return None
    s = str(x).strip().replace(",", "").replace("$", "")
    if not s or s.lower() in ("none", "nan", "n/a", "-"):
        return None
    pct = s.endswith("%")
    try:
        v = float(s.rstrip("%"))
    except ValueError:
        return None
    return v / 100 if pct else v


def norm_tag(tag):
    t = re.sub(r"^(chart pattern|pattern)\s*-\s*", "", tag.strip(), flags=re.I)
    t = re.sub(r"^cloud\s*-\s*", "cloud ", t, flags=re.I).lower().strip()
    for key, label in TAG_ALIASES:
        if key == t or (len(key) > 3 and key in t) or re.search(rf"\b{re.escape(key)}\b", t):
            return label
    return tag.strip()


def instrument(ticker):
    if OPTION_RE.search(ticker) or " " in ticker.strip():
        return "Options"
    base = re.split(r"[-/ ]", ticker.strip().upper())[0]
    return "Crypto" if base in CRYPTO else "Stocks and ETFs"


def days_between(a, b):
    import datetime as dt
    try:
        return (dt.date.fromisoformat(b[:10]) - dt.date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return None


def find(header, names):
    low = {h.strip().lower(): h for h in header if h}
    for n in names:
        if n in low:
            return low[n]
    return None


def load(paths):
    trades = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            col = {k: find(rd.fieldnames or [], v) for k, v in COLUMNS.items()}
            for row in rd:
                get = lambda k: row.get(col[k]) if col[k] else None  # noqa: E731
                pnl = parse_num(get("pnl"))
                if pnl is not None and abs(pnl) > 3:  # written as 5 meaning 5%
                    pnl /= 100
                outcome = (get("outcome") or "").strip()
                if pnl is None:
                    continue
                entry, stop = parse_num(get("entry")), parse_num(get("stop"))
                # Planned risk only when the logged stop is below entry; a stop above entry was trailed
                # after the fact, so the original risk is unknown and R would mislead.
                risk = (entry - stop) / entry if entry and stop and stop < entry else None
                size = parse_num(get("size"))
                hold = parse_num(get("hold"))
                if hold is None:
                    hold = days_between(get("open") or "", get("close") or "")
                tags = sorted({norm_tag(t) for t in re.split(r"[,;]", get("tags") or "") if t.strip()})
                notes = (get("notes") or "").lower()
                exp = (get("expected") or "").strip()
                trades.append({
                    "file": os.path.basename(path), "ticker": (get("ticker") or "").strip().upper(),
                    "instrument": instrument(get("ticker") or ""),
                    "outcome": outcome, "pnl": pnl, "r": pnl / risk if risk else None, "risk": risk,
                    "size": size, "impact": pnl * size if size is not None and 0 < size <= 1 else None,
                    "tags": tags or ["(untagged)"], "type": (get("type") or "").strip() or "(none)",
                    "confidence": (get("confidence") or "").strip() or "(none)",
                    "hold": hold, "expected": exp if exp and not parse_num(exp) else ("(days given)" if exp else "(none)"),
                    "planned_rr": parse_num(get("rr")),
                    "stalled": any(w in notes for w in ("neckline", "stall")),
                    "open": (get("open") or "")[:10], "notes": notes,
                })
    return trades


def stats(ts):
    p = [t["pnl"] for t in ts]
    wins = [x for x in p if x > 0.002]
    losses = [x for x in p if x < -0.002]
    rs = [t["r"] for t in ts if t["r"] is not None]
    imp = [t["impact"] for t in ts if t["impact"] is not None]
    n = len(p)
    gl = -sum(losses)
    return {
        "n": n, "win_rate": len(wins) / n if n else None,
        "win_floor": wilson_lower(len(wins), n) if n else None,
        "avg": sum(p) / n if n else None, "median": median(p),
        "avg_win": sum(wins) / len(wins) if wins else None, "avg_loss": sum(losses) / len(losses) if losses else None,
        "profit_factor": sum(wins) / gl if gl else None,
        "avg_r": sum(rs) / len(rs) if rs else None, "r_n": len(rs),
        "account_impact": sum(imp) if imp else None,
        "hold_median": median([t["hold"] for t in ts if t["hold"] is not None]),
    }


def group(ts, key, multi=False):
    g = defaultdict(list)
    for t in ts:
        for k in (t[key] if multi else [t[key]]):
            g[k].append(t)
    return {k: stats(v) for k, v in g.items()}


def hold_bucket(t):
    h = t["hold"]
    if h is None:
        return "(unknown)"
    return "0-3 days" if h <= 3 else "4-10 days" if h <= 10 else "11-30 days" if h <= 30 else "over 30 days"


def rr_bucket(t):
    r = t["planned_rr"]
    if r is None:
        return "(none)"
    return "under 2" if r < 2 else "2-4" if r < 4 else "4-8" if r < 8 else "8 or more"


def report(trades, min_n):
    out = {"overall": stats(trades)}
    for t in trades:
        t["hold_bucket"] = hold_bucket(t)
        t["rr_bucket"] = rr_bucket(t)
        t["exit_note"] = "noted a stall at the neckline" if t["stalled"] else "other"
        t["month"] = t["open"][:7] or "(no date)"
    for key, multi in (("instrument", False), ("tags", True), ("type", False), ("confidence", False), ("hold_bucket", False),
                       ("rr_bucket", False), ("exit_note", False), ("month", False)):
        out[key] = {k: v for k, v in group(trades, key, multi).items() if v["n"] >= min_n}
    big = sorted(trades, key=lambda t: t["pnl"])
    out["worst"] = [{k: t[k] for k in ("ticker", "open", "pnl", "r", "type", "confidence")} for t in big[:5]]
    out["best"] = [{k: t[k] for k in ("ticker", "open", "pnl", "r", "type", "confidence")} for t in big[-5:][::-1]]
    losers = [t for t in trades if t["pnl"] < 0 and t["r"] is not None]
    out["losses_beyond_stop"] = {"count": sum(1 for t in losers if t["r"] < -1.25), "of": len(losers),
                                 "median_planned_risk": median([t["risk"] for t in losers]),
                                 "median_realized_loss": median([t["pnl"] for t in losers]),
                                 "avg_overshoot": (lambda o: sum(o) / len(o) if o else None)(
                                     [t["pnl"] + t["risk"] for t in losers if t["r"] < -1.25]),
                                 "note": "losses worse than 1.25R: the stop wasn't honoured, or it gapped"}
    return out


def pct(x, nd=1):
    return "n/a" if x is None else f"{x * 100:+.{nd}f}%"


def rate(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def num(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def table(title, groups):
    L = [f"### {title}", "| Group | Trades | Win rate | 90% floor | Avg return | Median | Avg win | Avg loss | Profit factor | Avg R | Account impact | Median hold |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in sorted(groups.items(), key=lambda kv: -(kv[1]["avg"] or -9)):
        L.append(f"| {k} | {s['n']} | {rate(s['win_rate'])} | {rate(s['win_floor'])} | {pct(s['avg'])} | {pct(s['median'])} "
                 f"| {pct(s['avg_win'])} | {pct(s['avg_loss'])} | {num(s['profit_factor'])} | {num(s['avg_r'])} "
                 f"| {pct(s['account_impact'], 2)} | {num(s['hold_median'], 0)} |")
    return L


def render(out):
    o = out["overall"]
    L = ["## Trade journal scorecard\n",
         f"{o['n']} closed trades. Win rate {rate(o['win_rate'])} (90% floor {rate(o['win_floor'])}); average return "
         f"{pct(o['avg'])}, median {pct(o['median'])}; average win {pct(o['avg_win'])} vs average loss {pct(o['avg_loss'])}; "
         f"profit factor {num(o['profit_factor'])}; average R {num(o['avg_r'])} over {o['r_n']} trades with a stop; "
         f"summed account impact {pct(o['account_impact'], 2)}.\n"]
    for key, title in (("instrument", "By instrument"), ("tags", "By setup tag (a trade counts once per tag)"), ("type", "By trade type"),
                       ("confidence", "By setup confidence"), ("rr_bucket", "By planned reward/risk"),
                       ("hold_bucket", "By holding time"), ("exit_note", "Trades whose notes mention a stall at the neckline"),
                       ("month", "By month opened")):
        L += table(title, out[key])
        L.append("")
    lb = out["losses_beyond_stop"]
    L.append(f"Losses beyond 1.25R: {lb['count']} of {lb['of']} losing trades with a stop below entry ({lb['note']}). "
             f"Median planned risk {rate(lb['median_planned_risk'])} vs median realized loss {pct(lb['median_realized_loss'])}; "
             f"on the trades that overshot, the loss ran {pct(lb['avg_overshoot'])} past the stop on average.")
    L.append("\nBest: " + "; ".join(f"{t['ticker']} {pct(t['pnl'])}" for t in out["best"]))
    L.append("Worst: " + "; ".join(f"{t['ticker']} {pct(t['pnl'])}" for t in out["worst"]))
    L.append("\nRanked by average return. Small groups are noisy: read the 90% floor before trusting a win rate.")
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+", help="CSV exports of closed trades")
    p.add_argument("--min-n", type=int, default=3, help="hide groups with fewer trades (default 3)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    trades = load(args.paths)
    if not trades:
        sys.exit("no closed trades with a realized return found; check the pnl column")
    out = report(trades, args.min_n)
    print(json.dumps(out, indent=2, default=str) if args.json else render(out))


if __name__ == "__main__":
    main()
