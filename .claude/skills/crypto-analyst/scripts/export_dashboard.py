#!/usr/bin/env python3
"""
Write the data files behind an entry-map dashboard for one ticker, plus an
alerts file saying whether anything needs a human today. Standard library only.

  python export_dashboard.py BTC --out /tmp/btc_map      # CSVs + alerts.json
  python export_dashboard.py SOL --out /tmp/sol_map --near 0.01

Files written (one CSV each, first row = column names):
  summary      bias, grade, average entry, stop, risk and reward/risk per horizon
  plan         entries, stop, exits and breakout rows per horizon (long side)
  zones        every level zone within 25% of price, per horizon
  evidence     the ten evidence votes per horizon
  daily        the last 200 daily candles (the last row may be forming)
  liqmap       modeled liquidation clusters (crypto with an OKX perp only)
  funding      funding and open interest by venue (crypto only)
  positioning  positioning readings (crypto only)
  signals      every tracked signal's record on this ticker
  watch        upcoming closes, expiries and trigger levels
  gauges       market backdrop (and BTC cycle gauges for BTC)
  alerts.json  {"alerts": [...], "live": ..., "as_of": ...}: an alert fires when
               price is inside (or within --near of) an entry zone, a daily close
               crossed the breakout level or the stop, or a weekly close fell
               below the 21-week EMA. An empty list means nothing to act on.

The BTC Entry Map dashboard reads these files; a scheduled task can upload
them each day and send the alerts.
"""

import argparse
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
import ta_core as T  # noqa: E402
import technicals as TA  # noqa: E402
import trade_plan as P  # noqa: E402
from market_data import FetchError  # noqa: E402


def r(x, n=6):
    return None if x is None else round(x, n)


def labels(z, n=5):
    return "; ".join(lv.rsplit(" ", 1)[0] for lv in z["levels"][:n])


def write(out, name, rows):
    if not rows:
        return
    with open(os.path.join(out, name), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def record(sig):
    hz = f"{sig['primary']} {'days' if sig['timeframe'] == '1D' else 'weeks'}"
    pr = sig[sig["primary"]]
    way = "lower" if sig["direction"] == "bearish" else "higher"
    move = -pr["median"] if sig["direction"] == "bearish" else pr["median"]
    return (f"price was {way} {hz} later in {pr['win_rate'] * 100:.0f}% of {pr['n']} cases, "
            f"median move {move * 100:+.1f}% ({sig['grade']})")


def export(sym, out, near=0.01):
    os.makedirs(out, exist_ok=True)
    backdrop = None
    try:
        import derivatives as D
        backdrop = D.sentiment()
    except Exception:  # the backdrop is context; the plan stands without it
        backdrop = None
    a = TA.analyze(sym, with_derivs=True, bench=None if sym == "BTC" else TA.bench_series())
    plans = [P.plan(sym, analysis=a, horizon=hz, side="long", backdrop=backdrop) for hz in ("swing", "position")]
    live = a["live"]
    summary, plan, zones, evidence = [], [], [], []
    for o in plans:
        hz, p = o["horizon"], o["plans"]["long"]
        if not p:
            continue
        t = p["targets"]
        summary.append({"horizon": hz, "bias": o["bias"].capitalize(), "bias_score": r(o["bias_score"], 2), "grade": p["grade"],
                        "live": r(live, 6), "atr": r(o["atr"], 6), "atr_pct": r(o["atr"] / live, 4),
                        "avg_entry": r(p["avg_entry"], 6), "stop": r(p["stop"], 6), "risk_pct": r(p["risk_pct"], 4),
                        "exit_2": r(t[1]["price"] if len(t) > 1 else t[0]["price"], 6) if t else None,
                        "rr_exit_2": r(t[1]["rr"] if len(t) > 1 else t[0]["rr"], 2) if t else None,
                        "expected_hold": p["expected_hold"], "as_of": o["as_of"]})
        for name, z in (("Entry 1", p["entry_1"]), ("Entry 2", p["entry_2"])):
            if z:
                plan.append({"horizon": hz, "step": name, "low": r(z["low"]), "high": r(z["high"]), "price": r(z["mid"]),
                             "from_live": r(z["mid"] / live - 1, 4), "rr": None, "strength": z["weight"], "why": labels(z)})
        why = "Beyond the deeper entry zone plus half a day's range"
        if p["stop_moved_for"]:
            why += f"; moved past the {p['stop_moved_for'].split(' (')[0]}"
        plan.append({"horizon": hz, "step": "Stop", "low": r(p["stop"]), "high": r(p["stop"]), "price": r(p["stop"]),
                     "from_live": r(p["stop"] / live - 1, 4), "rr": None, "strength": None, "why": why})
        for i, x in enumerate(t, 1):
            plan.append({"horizon": hz, "step": f"Exit {i}", "low": r(x["price"]), "high": r(x["price"]), "price": r(x["price"]),
                         "from_live": r(x["price"] / live - 1, 4), "rr": r(x["rr"], 2), "strength": x["zone"]["weight"],
                         "why": "Just below " + labels(x["zone"], 3)})
        b = p["breakout"]
        if b:
            plan.append({"horizon": hz, "step": "Breakout entry", "low": r(b["trigger"]), "high": r(b["trigger"]),
                         "price": r(b["trigger"]), "from_live": r(b["trigger"] / live - 1, 4), "rr": r(b["rr"], 2),
                         "strength": b["zone"]["weight"],
                         "why": "Daily close above " + labels(b["zone"], 3) + ", on volume at least 1.5x the 20-day average"})
            plan.append({"horizon": hz, "step": "Breakout stop", "low": r(b["stop"]), "high": r(b["stop"]), "price": r(b["stop"]),
                         "from_live": r(b["stop"] / live - 1, 4), "rr": None, "strength": None,
                         "why": "A close back inside the zone means the breakout failed"})
        for z in o["zones"]:
            if z["weight"] >= 3 and abs(z["distance"]) <= 0.25:
                zones.append({"horizon": hz, "side": z["side"], "low": r(z["low"]), "high": r(z["high"]), "mid": r(z["mid"]),
                              "strength": z["weight"], "distance": r(z["distance"], 4), "levels": labels(z, 6)})
        for e in o["evidence"]:
            evidence.append({"horizon": hz, "family": e["family"], "vote": e["vote"], "reading": e["reading"]})
    for name, rows in (("summary.csv", summary), ("plan.csv", plan), ("zones.csv", zones), ("evidence.csv", evidence)):
        write(out, name, rows)

    d = a["_series"]["dc"]
    n = len(d["t"])
    daily = [{"date": T.utc(d["t"][i]).strftime("%Y-%m-%d"), "open": d["o"][i], "high": d["h"][i], "low": d["l"][i],
              "close": d["c"][i], "forming": "no"} for i in range(max(0, n - 199), n)]
    if T.utc(time.time()).strftime("%Y-%m-%d") != daily[-1]["date"]:
        # today's bar is still forming: show the live price as its close
        daily.append({"date": T.utc(time.time()).strftime("%Y-%m-%d"), "open": live, "high": live, "low": live,
                      "close": live, "forming": "yes"})
    write(out, "daily.csv", daily)

    der = a.get("derivatives") or {}
    lm = der.get("liquidation_map") if isinstance(der.get("liquidation_map"), dict) else None
    if lm and "bins" in lm:
        write(out, "liqmap.csv", [{"price": r(b["price"]), "side": b["side"], "notional_usd": round(b["notional"]),
                                   "distance": r(b["distance"], 4)} for b in lm["bins"]])
    f = der.get("funding") if isinstance(der.get("funding"), dict) else None
    if f and "venues" in f:
        write(out, "funding.csv", [{"venue": v["venue"], "rate_8h_pct": r(v["rate_8h"] * 100, 5), "apr_pct": r(v["apr"] * 100, 2),
                                    "oi_usd": round(v["oi_usd"]) if v.get("oi_usd") else None} for v in f["venues"]])
    pos = []
    if f and f.get("apr_oi_weighted") is not None:
        pos.append({"metric": "funding_apr", "label": "OI-weighted funding, annualized", "value": r(f["apr_oi_weighted"] * 100, 2),
                    "unit": "pct", "read": f["read"]["label"]})
    pz = der.get("positioning") if isinstance(der.get("positioning"), dict) else {}
    if pz.get("okx_oi"):
        pos.append({"metric": "okx_oi_24h", "label": "OKX open interest, 24h change", "value": r(pz["okx_oi"]["24h"] * 100, 2),
                    "unit": "pct", "read": (pz.get("quadrant") or {}).get("label", "")})
    wh = der.get("whales") if isinstance(der.get("whales"), dict) else {}
    if wh.get("long_short") is not None or wh.get("short_usd"):
        pos += [{"metric": "whale_long", "label": "Hyperliquid whales long", "value": round(wh["long_usd"]), "unit": "usd", "read": wh["universe"]},
                {"metric": "whale_short", "label": "Hyperliquid whales short", "value": round(wh["short_usd"]), "unit": "usd", "read": ""}]
    if lm and "cumulative" in lm:
        pos += [{"metric": "liq_long_5", "label": "Modeled long liquidations within 5% below",
                 "value": round(lm["cumulative"]["5%"]["long_liqs_below"]), "unit": "usd", "read": lm["read"]["label"]},
                {"metric": "liq_short_5", "label": "Modeled short liquidations within 5% above",
                 "value": round(lm["cumulative"]["5%"]["short_liqs_above"]), "unit": "usd", "read": ""}]
    write(out, "positioning.csv", pos)

    order = {"proven": 0, "positive, unproven": 1, "coin-flip or worse": 2, "too few": 3}
    sig = []
    for s in a["backtest"]:
        pr = s[s["primary"]]
        sig.append({"signal": s["label"], "timeframe": s["timeframe"], "direction": s["direction"], "trades": pr["n"],
                    "win_rate": r(pr["win_rate"], 4), "floor_90": r(pr["wilson_lb"], 4), "median": r(pr["median"], 4),
                    "edge": r(pr.get("edge"), 4), "drawdown": r(pr["mae_median"], 4), "grade": s["grade"],
                    "horizon": f"{s['primary']}{'d' if s['timeframe'] == '1D' else 'w'}", "last_fired": s["last"] or "",
                    "firing_now": "yes" if s["active"] else "no"})
    sig.sort(key=lambda x: (order[x["grade"]], -(x["edge"] or -9)))
    write(out, "signals.csv", sig)
    by_label = {s["label"]: s for s in a["backtest"]}

    ma = a["moving_averages"]
    swing = next((o for o in plans if o["horizon"] == "swing"), None)
    sp = swing["plans"]["long"] if swing else None
    watch = []
    if "21W EMA" in ma:
        watch.append({"when": "Next weekly close (Sunday 23:59 UTC)", "trigger": "Weekly close below the 21-week EMA",
                      "level": r(ma["21W EMA"]["value"]),
                      "why": "History: " + record(by_label["Weekly close below the 21-week EMA"])})
    ich = a["ichimoku"].get("D default")
    if ich:
        watch.append({"when": "Any daily close", "trigger": "Close above the daily Ichimoku cloud", "level": r(ich["cloud_top"]),
                      "why": "History: " + record(by_label["Daily close breaks above the Ichimoku cloud"])})
    if a["breakout_levels"].get("55D high"):
        watch.append({"when": "Any daily close", "trigger": "Close above the prior 55-day high", "level": r(a["breakout_levels"]["55D high"]),
                      "why": "History: " + record(by_label["Close above the prior 55-day high"])})
    if sp and sp.get("breakout"):
        watch.append({"when": "Any daily close", "trigger": "Close above the swing breakout level", "level": r(sp["breakout"]["trigger"]),
                      "why": "The swing plan's alternative entry"})
    if sp:
        watch.append({"when": "Any daily close", "trigger": "Close below the swing stop", "level": r(sp["stop"]),
                      "why": "The swing idea is wrong; the position ladder takes over"})
    o_ = der.get("options") if isinstance(der.get("options"), dict) else {}
    for m in o_.get("max_pain", []):
        watch.append({"when": f"{m['expiry'][:2]} {m['expiry'][2:5].title()}, Deribit expiry", "trigger": "Max pain",
                      "level": m["max_pain"], "why": f"Put/call open interest {m['put_call_oi']:.2f} for that expiry"})
    write(out, "watch.csv", watch)

    g = []
    if backdrop:
        if backdrop.get("fear_greed"):
            g.append({"group": "Backdrop", "gauge": "Fear & Greed", "value": backdrop["fear_greed"]["now"], "unit": "index",
                      "read": backdrop["fear_greed"]["label"]})
        if backdrop.get("mvrv"):
            g.append({"group": "Backdrop", "gauge": "MVRV Z-score", "value": r(backdrop["mvrv"]["z_score"], 2), "unit": "z",
                      "read": "Under 0 marked cycle lows; over 7 marked tops"})
        if backdrop.get("dominance"):
            g.append({"group": "Backdrop", "gauge": "BTC dominance", "value": r(backdrop["dominance"]["btc"], 1), "unit": "pct",
                      "read": "Share of total crypto market cap"})
        if backdrop.get("stablecoins"):
            g.append({"group": "Backdrop", "gauge": "Stablecoin supply, 30-day change", "value": r(backdrop["stablecoins"]["30d"] * 100, 2),
                      "unit": "pct", "read": "On-chain liquidity"})
        for key, label in (("dxy", "Dollar index vs its 50-day"), ("spx", "S&P 500 vs its 50-day")):
            if backdrop.get(key):
                g.append({"group": "Backdrop", "gauge": label, "value": r(backdrop[key]["vs_50d"] * 100, 2), "unit": "pct", "read": ""})
    cy = next((o["cycle"] for o in plans if o.get("cycle")), None)
    if cy:
        for gauge, val, read in (("Mayer multiple", cy["mayer_multiple"], "Under 0.8 is deep value"),
                                 ("Price over the 2-year MA", cy["price_vs_2y_ma"], "Under 1 is the accumulation zone"),
                                 ("Price over the 200-week SMA", cy["price_vs_200w"], "Near 1 marked bear-market floors"),
                                 ("Pi cycle ratio", cy["pi_cycle_ratio"], "1.0 or above marked cycle tops"),
                                 ("Power-law z-score", cy["power_law"]["z"], "Below -1: cheap against the long-run trend")):
            if val is not None:
                g.append({"group": "Cycle", "gauge": gauge, "value": r(val, 2), "unit": "ratio" if "z" not in gauge else "z", "read": read})
    write(out, "gauges.csv", g)

    alerts = []
    last_close = d["c"][-1]
    prev_close = d["c"][-2]
    for o in plans:
        p = o["plans"]["long"]
        if not p:
            continue
        for name, z in (("entry 1", p["entry_1"]), ("entry 2", p["entry_2"])):
            if z and z["low"] * (1 - near) <= live <= z["high"] * (1 + near):
                inside = z["low"] <= live <= z["high"]
                alerts.append(f"{o['horizon']} {name}: price {TA.f(live)} is {'inside' if inside else 'within ' + format(near, '.0%') + ' of'} "
                              f"{TA.f(z['low'])}-{TA.f(z['high'])} ({labels(z, 3)}); grade {p['grade']}, stop {TA.f(p['stop'])}")
        if prev_close >= p["stop"] > last_close:
            alerts.append(f"{o['horizon']} stop: the daily close {TA.f(last_close)} fell below the stop {TA.f(p['stop'])}")
        b = p.get("breakout")
        if b and prev_close <= b["trigger"] < last_close:
            alerts.append(f"{o['horizon']} breakout: the daily close {TA.f(last_close)} cleared {TA.f(b['trigger'])}; "
                          "check volume (1.5x the 20-day average)")
    wc = a["_series"]["wc"]
    e21 = T.ema(wc["c"], 21)
    if len(wc["c"]) > 2 and e21[-1] and e21[-2] and wc["c"][-2] >= e21[-2] and wc["c"][-1] < e21[-1] \
            and time.time() - (wc["t"][-1] + 7 * 86400) < 2 * 86400:
        alerts.append(f"weekly close {TA.f(wc['c'][-1])} fell below the 21-week EMA {TA.f(e21[-1])}: "
                      + record(by_label["Weekly close below the 21-week EMA"]))
    seen, uniq = set(), []
    for x in alerts:  # both horizons can share a zone
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    manifest = {"symbol": a["symbol"], "live": live, "as_of": a["as_of"], "source": a["source"], "alerts": uniq,
                "files": sorted(fn for fn in os.listdir(out) if fn.endswith(".csv"))}
    with open(os.path.join(out, "alerts.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbol")
    p.add_argument("--out", required=True, help="folder for the CSVs and alerts.json")
    p.add_argument("--near", type=float, default=0.01, help="alert when price is within this share of an entry zone (default 0.01)")
    args = p.parse_args()
    try:
        m = export(args.symbol.upper(), args.out, args.near)
    except FetchError as e:
        sys.exit(f"{args.symbol.upper()}: {e}")
    print(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
