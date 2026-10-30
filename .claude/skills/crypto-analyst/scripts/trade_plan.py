#!/usr/bin/env python3
"""
Entry and exit plan for a ticker, built from every data family the skill
can reach. Standard library only.

  python trade_plan.py HYPE                  # full plan: bias, entries, stop, targets, exit rules, evidence
  python trade_plan.py BTC ETH SOL --brief   # just the plan block for each coin
  python trade_plan.py ZEC --side short      # force the short-side plan
  python trade_plan.py SOL --account 160000 --risk 1   # add position size for 1% account risk
  python trade_plan.py HYPE --no-derivs      # chart-only (faster; no funding, whales or liquidation map)
  python trade_plan.py HYPE --full           # plan + the full technical dossier underneath
  python trade_plan.py BTC --horizon both    # swing plan (days-weeks) and position plan (weeks-months)
  add --json for machine-readable output

How the plan is built
  1. technicals.py reads the chart (multi-timeframe RSI and MACD, period
     opens, pivots, moving averages, Ichimoku, VWAPs, volume profile,
     swings) and backtests every signal firing now on this coin.
  2. derivatives.py adds funding across venues, open interest, long/short
     ratios, Hyperliquid whale positions, a modeled liquidation map, options
     walls (BTC and ETH) and order-book walls.
  3. Every level from both is clustered into zones. Intraday levels (daily
     pivots, prior-day high/low) are left out: this is a swing plan.
  4. Ten evidence families vote (see EVIDENCE below). The sum is the bias.
  5. Entries sit in the strongest support zones below price (resistance for
     shorts), the stop goes beyond the deeper zone and beyond any nearby
     liquidation pocket (stops parked inside liquidation clusters get run),
     and exits sit just in front of the next strong zones.
  6. The setup grade (A++, A, B, C, as in a trade journal) is a points
     count: +1 per evidence family voting for the side and -1 per family
     against; +2 for graded reward/risk of 3 or more (+1 for 2-3, -1 under
     1.5); +1 for an entry zone of strength 7+; +1 with the trend, -1
     against it; +1 if a proven signal is firing; -1 if the overall bias
     leans the other way (-1 or beyond). A++ is 7+, A 5-6, B 3-4, C under 3.

Every number traces to a level or reading printed alongside it. The plan is
conditional research, not an instruction: it says where a trade would make
sense and where it is wrong, and leaves the decision and sizing to you.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also works under python -I
import technicals as TA  # noqa: E402
from market_data import FetchError  # noqa: E402

INTRADAY = ("D pivot", "D Camarilla", "prior-day", "daily open")
# Position plans (weeks to months) also drop short-term levels and positioning that turns over within days.
SHORT_TERM = INTRADAY + ("W pivot", "W Camarilla", "prior-week", "weekly open", "20D", "55D", "21D EMA", "20D SMA",
                         "daily kijun", "daily cloud", "long liquidation", "short liquidation", "whale", "call OI",
                         "put OI", "max pain", "bid wall", "ask wall")
CYCLE_W = {"realized price": 3.0, "power-law fair value": 2.0, "power-law -1 sd": 2.0, "2-year MA": 2.0,
           "Mayer 0.8 (0.8x 200D)": 1.5}
ZONE_MIN = 3.0          # weight a zone needs to count as an entry or target
NEAR_ATR, FAR_ATR = 0.3, 6.0
EVIDENCE = ("trend", "momentum", "stretch", "signal record", "positioning", "liquidity",
            "relative strength", "seasonality", "backdrop", "cycle")


def horizon_atr(a, horizon):
    return a["tf"]["1W"].get("atr") if horizon == "position" else a["tf"]["1D"].get("atr")


def swing_zones(a, horizon="swing"):
    drop = SHORT_TERM if horizon == "position" else INTRADAY
    levels = [x for x in TA.collect_levels(a) if not x[1].startswith(drop)]
    zones, tol = TA.confluence(levels, a["live"], horizon_atr(a, horizon), max_dist=0.30 if horizon == "swing" else 0.45)
    return zones, tol


def cycle_levels(a, cycle, backdrop):
    """BTC cycle floors as confluence levels: realized price, power law, 2-year MA, Mayer 0.8."""
    out = []
    mv = (backdrop or {}).get("mvrv")
    if mv:
        dc = a["_series"]["dc"]
        day = {TA.T.utc(t).strftime("%Y-%m-%d"): c for t, c in zip(dc["t"], dc["c"])}
        if day.get(mv["as_of"]):
            out.append({"price": day[mv["as_of"]] / mv["mvrv"], "label": "realized price", "weight": CYCLE_W["realized price"]})
    pl = cycle["power_law"]
    out.append({"price": pl["fair_value"], "label": "power-law fair value", "weight": CYCLE_W["power-law fair value"]})
    out.append({"price": pl["band_minus_1sd"], "label": "power-law -1 sd", "weight": CYCLE_W["power-law -1 sd"]})
    if cycle.get("two_year_ma"):
        out.append({"price": cycle["two_year_ma"], "label": "2-year MA", "weight": CYCLE_W["2-year MA"]})
    s200 = a["moving_averages"].get("200D SMA", {}).get("value")
    if s200:
        out.append({"price": 0.8 * s200, "label": "Mayer 0.8 (0.8x 200D)", "weight": CYCLE_W["Mayer 0.8 (0.8x 200D)"]})
    return out


def pick(zones, side, live, atr, near, far, min_w=ZONE_MIN, exclude_below=None):
    """Zones on one side of price between `near` and `far` ATRs away, strongest first."""
    out = []
    for z in zones:
        if z["weight"] < min_w:
            continue
        d = (live - z["high"]) / atr if side == "support" else (z["low"] - live) / atr
        if near <= d <= far and z["side"] in (side, "at price"):
            if exclude_below is not None and z["high"] >= exclude_below:
                continue
            out.append(dict(z, atr_away=d))
    return out


# ---------------------------------------------------------------- evidence

HORIZON_WEIGHTS = {"position": {"positioning": 0.5, "liquidity": 0.5, "signal record": 0.5, "cycle": 2.0}}


def evidence(a, backdrop, btc_ctx, cycle, horizon="swing"):
    ev = _evidence(a, backdrop, btc_ctx, cycle)
    w = HORIZON_WEIGHTS.get(horizon, {})
    return [(k, round(v * w.get(k, 1.0), 2), r) for k, v, r in ev]


def _evidence(a, backdrop, btc_ctx, cycle):
    ev = []
    sc = a["score"]
    d, w = a["tf"]["1D"], a["tf"]["1W"]
    ev.append(("trend", round(sc["trend"] / 2.5, 2), f"{sc['stance']}: " + ", ".join(sc["trend_reasons"])))
    ev.append(("momentum", round(sc["momentum"] / 2.5, 2), ", ".join(sc["momentum_reasons"])))
    st, why = 0.0, []
    if d.get("rsi") is not None:
        if d["rsi"] < 25:
            st, why = st + 1.5, why + [f"daily RSI {d['rsi']:.0f} (capitulation zone)"]
        elif d["rsi"] < 30:
            st, why = st + 1.0, why + [f"daily RSI {d['rsi']:.0f} oversold"]
        elif d["rsi"] > 80:
            st, why = st - 1.5, why + [f"daily RSI {d['rsi']:.0f} (blow-off zone)"]
        elif d["rsi"] > 70:
            st, why = st - 1.0, why + [f"daily RSI {d['rsi']:.0f} overbought"]
    if w.get("rsi") is not None and w["rsi"] < 30:
        st, why = st + 1.0, why + [f"weekly RSI {w['rsi']:.0f} oversold"]
    if w.get("rsi") is not None and w["rsi"] > 75:
        st, why = st - 1.0, why + [f"weekly RSI {w['rsi']:.0f} stretched"]
    if d.get("pct_b") is not None and d["pct_b"] < 0:
        st, why = st + 0.5, why + ["closed below the lower Bollinger Band"]
    if d.get("pct_b") is not None and d["pct_b"] > 1:
        st, why = st - 0.5, why + ["closed above the upper Bollinger Band"]
    e21 = a["moving_averages"].get("21D EMA", {}).get("value")
    if e21 and d.get("atr"):
        gap = (a["live"] - e21) / d["atr"]
        if gap <= -3:
            st, why = st + 0.5, why + [f"{-gap:.1f} ATR below the 21D EMA"]
        elif gap >= 3:
            st, why = st - 0.5, why + [f"{gap:.1f} ATR above the 21D EMA"]
    ev.append(("stretch", max(-2, min(2, st)), "; ".join(why) or "not stretched"))
    sv, sw = 0.0, []
    for s in a["signals_now"]:
        sign = 1 if s["direction"] == "bullish" else -1
        pr = s[s["primary"]]
        if s["grade"] == "proven":
            sv += sign * 1.0
        elif s["grade"] == "positive, unproven":
            sv += sign * 0.5
        else:
            continue
        sw.append(f"{s['label']} ({s['grade']}, {TA.rate(pr['win_rate'])} of {pr['n']}, median {TA.pc(pr['median'])})")
    ev.append(("signal record", max(-2, min(2, sv)), "; ".join(sw) or "no signal firing now has a positive record on this coin"))
    der = a.get("derivatives") or {}
    votes = der.get("votes") or []
    pos = [v for v in votes if v[0] in ("funding", "open interest", "whales", "options")]
    liq = [v for v in votes if v[0] == "liquidation map"]
    ev.append(("positioning", max(-2, min(2, sum(v[1] for v in pos))),
               "; ".join(f"{v[0]}: {v[2]}" for v in pos) or "no derivatives data (spot only or --no-derivs)"))
    ev.append(("liquidity", sum(v[1] for v in liq), "; ".join(v[2] for v in liq) or "no liquidation map"))
    if btc_ctx and a["symbol"] != "BTC":
        rel = (a["change"]["30d"] or 0) - (btc_ctx["30d"] or 0)
        v = 0.5 if rel > 0.10 else -0.5 if rel < -0.10 else 0
        txt = f"30-day return {TA.pc(a['change']['30d'])} vs BTC {TA.pc(btc_ctx['30d'])}"
        if btc_ctx["stance"].startswith(("Strong downtrend", "Downtrend")):
            v -= 0.5
            txt += f"; BTC itself: {btc_ctx['stance'].lower()}"
        ev.append(("relative strength", v, txt))
    else:
        ev.append(("relative strength", 0, "BTC is the benchmark"))
    se = a["seasonality"]
    cur = next(x for x in se["table"] if x["month"] == se["current_month"])
    v = 0
    if cur["n"] >= 6 and cur["positive"] is not None:
        v = 0.25 if cur["positive"] >= 0.7 else -0.25 if cur["positive"] <= 0.3 else 0
    ev.append(("seasonality", v, f"{se['current_month']} up {TA.rate(cur['positive'])} of {cur['n']} years, median {TA.pc(cur['median'])}"))
    if backdrop:
        g = backdrop["risk_gate"]
        ev.append(("backdrop", 0.5 if g["score"] >= 2 else -0.5 if g["score"] <= -2 else 0, f"{g['label']}: {'; '.join(g['why'])}"))
    else:
        ev.append(("backdrop", 0, "not checked"))
    if cycle:
        v, why = 0.0, []
        if cycle["mayer_multiple"] and cycle["mayer_multiple"] < 0.8:
            v, why = v + 0.5, why + [f"Mayer {cycle['mayer_multiple']:.2f}"]
        if cycle["price_vs_200w"] and cycle["price_vs_200w"] < 1.1:
            v, why = v + 0.5, why + [f"{cycle['price_vs_200w']:.2f}x the 200W SMA"]
        if cycle.get("price_vs_2y_ma") and cycle["price_vs_2y_ma"] < 1:
            v, why = v + 0.5, why + [f"under the 2-year MA ({cycle['price_vs_2y_ma']:.2f}x), the accumulation zone"]
        if cycle["mayer_multiple"] and cycle["mayer_multiple"] > 2.4:
            v, why = v - 1, why + [f"Mayer {cycle['mayer_multiple']:.2f} overheated"]
        if cycle["pi_cycle_ratio"] and cycle["pi_cycle_ratio"] > 0.95:
            v, why = v - 1, why + ["Pi cycle near a top cross"]
        pl = cycle["power_law"]["z"]
        if pl <= -1:
            v, why = v + 0.5, why + [f"below the power-law -1 sd band (z {pl:+.2f})"]
        elif pl >= 2:
            v, why = v - 1, why + [f"above the power-law +2 sd band (z {pl:+.2f})"]
        mv = (backdrop or {}).get("mvrv")
        if mv and mv["mvrv"] < 1:
            v, why = v + 1, why + [f"MVRV {mv['mvrv']:.2f}: price under the realized price"]
        elif mv and mv["z_score"] > 6:
            v, why = v - 1, why + [f"MVRV Z {mv['z_score']:.1f}: overheated"]
        why.append(f"Mayer {cycle['mayer_multiple']:.2f}, {cycle['price_vs_200w']:.2f}x 200W"
                   + (f", MVRV {mv['mvrv']:.2f} (Z {mv['z_score']:.2f})" if mv else ""))
        ev.append(("cycle", v, "; ".join(why)))
    else:
        ev.append(("cycle", 0, "BTC only"))
    return ev


# ---------------------------------------------------------------- plan construction

def build_side(a, zones, side, ev_total, ev, horizon="swing"):
    live, atr = a["live"], horizon_atr(a, horizon)
    long = side == "long"
    entry_side, target_side = ("support", "resistance") if long else ("resistance", "support")
    first = pick(zones, entry_side, live, atr, NEAR_ATR, 2.5)
    first = max(first, key=lambda z: z["weight"] - 0.6 * z["atr_away"]) if first else None
    if first is None:
        cand = pick(zones, entry_side, live, atr, NEAR_ATR, FAR_ATR)
        first = min(cand, key=lambda z: z["atr_away"]) if cand else None
    if first is None:
        return None
    deeper = [z for z in pick(zones, entry_side, live, atr, first["atr_away"] + 0.6, FAR_ATR, ZONE_MIN + 1)]
    second = max(deeper, key=lambda z: z["weight"] - 0.25 * z["atr_away"]) if deeper else None
    e1 = first["mid"]
    e2 = second["mid"] if second else None
    avg = (e1 + e2) / 2 if e2 else e1
    edge = (second or first)["low"] if long else (second or first)["high"]
    buf = max(0.5 * atr, 0.01 * live)
    stop = edge - buf if long else edge + buf
    der_levels = [lv for lv in a.get("extra_levels", []) if "liquidation" in lv["label"]]
    moved = None
    for lv in der_levels:
        p = lv["price"]
        if long and stop - atr <= p <= stop + 0.25 * atr:
            stop, moved = min(stop, p - 0.3 * atr), lv["label"]
        if not long and stop - 0.25 * atr <= p <= stop + atr:
            stop, moved = max(stop, p + 0.3 * atr), lv["label"]
    risk = (avg - stop) if long else (stop - avg)
    tz = pick(zones, target_side, live, atr, 0.8, 12, ZONE_MIN)
    tz.sort(key=lambda z: z["atr_away"])
    targets = []
    for z in tz:
        price = z["low"] - 0.1 * atr if long else z["high"] + 0.1 * atr
        if targets and abs(price / targets[-1]["price"] - 1) < 0.02:
            continue
        rr = ((price - avg) if long else (avg - price)) / risk if risk > 0 else None
        if rr is not None and rr <= 0:
            continue
        targets.append({"price": price, "zone": z, "rr": rr})
        if len(targets) == 3:
            break
    if len(targets) < 3:
        sw = a["fib"]["swing"]
        rng = sw["swing_high"] - sw["swing_low"]
        for ext in (1.272, 1.618):
            price = sw["swing_low"] + ext * rng if long else sw["swing_high"] - ext * rng
            if abs(price / live - 1) > 0.35:  # too far to plan around; leave the exit list short instead
                continue
            if (long and price > (targets[-1]["price"] if targets else avg)) or (not long and price < (targets[-1]["price"] if targets else avg) and price > 0):
                targets.append({"price": price, "zone": {"levels": [f"fib extension {ext}"], "weight": 0}, "rr":
                                ((price - avg) if long else (avg - price)) / risk if risk > 0 else None})
            if len(targets) == 3:
                break
    brk = None
    over = pick(zones, target_side, live, atr, 0.0, 3.0)
    if over:
        z = min(over, key=lambda z: z["atr_away"])
        trig = z["high"] if long else z["low"]
        bstop = (z["low"] - 0.5 * atr) if long else (z["high"] + 0.5 * atr)
        far_enough = [t for t in targets if 1.5 * atr <= (t["price"] - trig if long else trig - t["price"]) <= 0.30 * trig]
        bt = far_enough[0]["price"] if far_enough else (trig + 2 * (trig - bstop) if long else trig - 2 * (bstop - trig))
        brk = {"trigger": trig, "stop": bstop, "target": bt,
               "rr": abs(bt - trig) / abs(trig - bstop) if trig != bstop else None,
               "zone": z}
    sign = 1 if long else -1
    agree = sum(1 for _, v, _ in ev if v * sign >= 0.5)
    against = sum(1 for _, v, _ in ev if v * sign <= -0.5)
    # Grade on a conservative reward/risk: target 2, but no more than twice target 1 and never above 4,
    # so a far-off Fibonacci extension can't carry the grade on its own.
    rr1 = targets[0]["rr"] if targets else None
    rr2 = targets[1]["rr"] if len(targets) > 1 else rr1
    rr_g = min(rr2, 2 * rr1, 4.0) if rr1 is not None and rr2 is not None else None
    pts, why = 0, []
    pts += agree - against
    why.append(f"{agree} evidence {'family agrees' if agree == 1 else 'families agree'}, "
               f"{against} {'disagrees' if against == 1 else 'disagree'}")
    if rr_g is not None:
        pts += 2 if rr_g >= 3 else 1 if rr_g >= 2 else -1 if rr_g < 1.5 else 0
        why.append(f"reward/risk {rr_g:.1f} (graded)")
    if ev_total * sign <= -1:
        pts -= 1
        why.append("overall bias leans the other way")
    if first["weight"] >= 7:
        pts += 1
        why.append(f"entry zone strength {first['weight']:.1f}")
    trend_v = next(v for k, v, _ in ev if k == "trend")
    if trend_v * sign > 0:
        pts += 1
        why.append("with the trend")
    elif trend_v * sign < 0:
        pts -= 1
        why.append("against the trend: needs a reversal trigger")
    proven = [s for s in a["signals_now"] if s["grade"] == "proven" and (s["direction"] == "bullish") == long]
    if proven:
        pts += 1
        why.append("a proven signal is firing")
    grade = "A++" if pts >= 7 else "A" if pts >= 5 else "B" if pts >= 3 else "C"
    hold_atr = abs((targets[1] if len(targets) > 1 else targets[0])["price"] - avg) / atr if targets else 0
    hold_days = max(2, round(hold_atr * 1.6 * (7 if horizon == "position" else 1)))
    hold = "Days" if hold_days <= 7 else "Weeks" if hold_days <= 45 else "Months"
    thesis = pick(zones, entry_side, live, atr, (abs(stop - live) / atr), FAR_ATR * 2, 5.0)
    thesis_line = min(thesis, key=lambda z: z["atr_away"]) if thesis else None
    counter = trend_v * sign < 0
    kind = "Mean reversion" if counter else "Trend pullback"
    return {"side": side, "entry_1": first, "entry_2": second, "avg_entry": avg, "stop": stop, "stop_moved_for": moved,
            "risk_pct": risk / avg, "targets": targets, "breakout": brk, "grade": grade, "grade_points": pts,
            "grade_why": why, "expected_hold": hold, "expected_hold_days": hold_days,
            "thesis_line": thesis_line, "counter_trend": counter, "type": kind, "horizon": horizon}


def plan(sym, derivs=True, side=None, backdrop=None, btc=None, account=None, risk_pct=None, horizon="swing"):
    a = TA.analyze(sym, with_derivs=derivs, bench=btc["bench"] if btc else None)
    cycle = None
    if a["symbol"] == "BTC":
        cycle = TA.cycle_gauges(a["_series"]["dc"], a["_series"]["wc"], a["live"])
        a["extra_levels"] = a.get("extra_levels", []) + cycle_levels(a, cycle, backdrop)
    zones, tol = swing_zones(a, horizon)
    ev = evidence(a, backdrop, btc["ctx"] if btc else None, cycle, horizon)
    total = round(sum(v for _, v, _ in ev), 2)
    bias = ("LONG BIAS" if total >= 2.5 else "LEAN LONG" if total >= 1 else "SHORT BIAS" if total <= -2.5
            else "LEAN SHORT" if total <= -1 else "NEUTRAL")
    sides = {}
    for s in ("long", "short"):
        sides[s] = build_side(a, zones, s, total, ev, horizon)
    primary = side or ("long" if total > 0.5 else "short" if total < -0.5 else
                       max(("long", "short"), key=lambda s: (sides[s] or {}).get("grade_points", -99)))
    out = {"symbol": a["symbol"], "as_of": a["as_of"], "live": a["live"], "source": a["source"], "bias": bias,
           "bias_score": total, "evidence": [{"family": k, "vote": v, "reading": r} for k, v, r in ev],
           "primary": primary, "plans": sides, "zones": zones, "zone_tolerance": tol, "analysis": a,
           "atr": horizon_atr(a, horizon), "horizon": horizon, "cycle": cycle}
    p = sides.get(primary)
    if p and account and risk_pct:
        risk_usd = account * risk_pct / 100
        units = risk_usd / abs(p["avg_entry"] - p["stop"])
        out["sizing"] = {"account": account, "risk_pct": risk_pct, "risk_usd": risk_usd, "units": units,
                         "notional": units * p["avg_entry"], "pct_of_account": units * p["avg_entry"] / account}
    return out


# ---------------------------------------------------------------- rendering

f, pc = TA.f, TA.pc


def zone_text(z, n=4):
    return "; ".join(z["levels"][:n]) + (" …" if len(z["levels"]) > n else "")


def render_side(p, live, title):
    L = [f"**{title}** · setup grade **{p['grade']}** ({'; '.join(p['grade_why'])})",
         "", "| Step | Price | From live | Reward/risk | Why this level |", "|---|---|---|---|---|"]
    e1 = p["entry_1"]
    share = "50%" if p["entry_2"] else "100%"
    L.append(f"| Entry 1 ({share}) | {f(e1['low'])}-{f(e1['high'])} | {pc(e1['mid'] / live - 1)} | | {zone_text(e1)} (strength {e1['weight']:.1f}) |")
    if p["entry_2"]:
        e2 = p["entry_2"]
        L.append(f"| Entry 2 (50%) | {f(e2['low'])}-{f(e2['high'])} | {pc(e2['mid'] / live - 1)} | | {zone_text(e2)} (strength {e2['weight']:.1f}) |")
    moved = f"; moved past {p['stop_moved_for']}" if p["stop_moved_for"] else ""
    L.append(f"| Stop | {f(p['stop'])} | {pc(p['stop'] / live - 1)} | risk {pc(p['risk_pct']).lstrip('+')} from avg entry {f(p['avg_entry'])} "
             f"| beyond the {'deeper ' if p['entry_2'] else ''}entry zone plus 0.5 ATR{moved} |")
    for i, t in enumerate(p["targets"], 1):
        L.append(f"| Exit {i} | {f(t['price'])} | {pc(t['price'] / live - 1)} | {t['rr']:.1f} | "
                 f"just {'below' if p['side'] == 'long' else 'above'} {zone_text(t['zone'], 3)} |")
    b = p.get("breakout")
    if b:
        L.append(f"\nAlternative entry, {'breakout' if p['side'] == 'long' else 'breakdown'}: a daily close "
                 f"{'above' if p['side'] == 'long' else 'below'} {f(b['trigger'])} with volume at least 1.5x the 20-day average "
                 f"(and a weekly close {'above' if p['side'] == 'long' else 'below'} it for confirmation). Stop {f(b['stop'])}, "
                 f"first target {f(b['target'])}" + (f", reward/risk {b['rr']:.1f}" if b.get("rr") else "") +
                 ". A close back inside the zone means it failed: cut it rather than wait for the stop.")
    tl = p.get("thesis_line")
    trail = ("the weekly kijun or a weekly close " + ("below" if p["side"] == "long" else "above") + " the 21-week EMA"
             if p.get("horizon") == "position" else
             "the daily kijun or a daily close " + ("below" if p["side"] == "long" else "above") + " the 21-day EMA")
    L.append(f"\nExit rules: take a third at each exit; after exit 1, move the stop to the average entry; "
             f"trail the rest under {trail}. "
             f"Time stop: if exit 1 hasn't hit within {2 * p['expected_hold_days']} days, the move isn't coming; close it."
             + (f" The bigger idea is wrong on a weekly close {'below' if p['side'] == 'long' else 'above'} {f(tl['low'] if p['side'] == 'long' else tl['high'])} ({zone_text(tl, 2)})." if tl else ""))
    if p["counter_trend"]:
        L.append("\nCounter-trend: wait for a trigger in the zone (daily MACD crossing up, RSI back above 30, or a reclaimed "
                 "level) and use smaller size." if p["side"] == "long" else
                 "\nCounter-trend: wait for rejection in the zone (daily MACD crossing down or a lost level) and use smaller size.")
    return L


def journal_row(o):
    p = o["plans"].get(o["primary"])
    if not p or not p["targets"]:
        return None
    tgt = p["targets"][1] if len(p["targets"]) > 1 else p["targets"][0]
    sl_pct = abs(p["stop"] / p["avg_entry"] - 1)
    def names(z, n):
        return ", ".join(x.rsplit(" ", 1)[0] for x in z["levels"][:n])

    rat = names(p["entry_1"], 3) + (f"; add at {names(p['entry_2'], 2)}" if p["entry_2"] else "")
    return {"Ticker": o["symbol"], "Side": p["side"], "Entry / Cost Basis": f(p["avg_entry"]),
            "Exit": f(tgt["price"]), "Stop Loss": f(p["stop"]), "SL %": f"{sl_pct * 100:.2f}%",
            "R:R": round(tgt["rr"], 2), "Rationale": rat, "Type": p["type"],
            "Setup Confidence": p["grade"], "Expected Hold Time": p["expected_hold"]}


def render(o, brief=False, full=False):
    hz = "position (weeks to months; weekly ATR, monthly and longer levels)" if o["horizon"] == "position" else \
        "swing (days to weeks; daily ATR, weekly and longer levels plus positioning)"
    L = [f"# {o['symbol']} — entry and exit plan, {o['horizon']} horizon",
         f"*{o['as_of']} · live {f(o['live'])} · {'weekly' if o['horizon'] == 'position' else 'daily'} ATR {f(o['atr'])} "
         f"({pc(o['atr'] / o['live']).lstrip('+')}) · candles {o['source']} · {hz}*\n",
         f"## {o['bias']} ({o['bias_score']:+.1f}) · leading with the {o['primary']} plan\n"]
    p = o["plans"].get(o["primary"])
    if p:
        L += render_side(p, o["live"], f"{o['primary'].capitalize()} plan")
    else:
        L.append("No mapped zones on that side within 6 ATR; no plan.")
    other = "short" if o["primary"] == "long" else "long"
    q = o["plans"].get(other)
    if q and (not brief or o["bias"] == "NEUTRAL"):
        L.append("")
        L += render_side(q, o["live"], f"{other.capitalize()} plan (the other side, for reference)")
    jr = journal_row(o)
    if jr:
        L.append("\nJournal row: " + " | ".join(f"{k}: {v}" for k, v in jr.items()))
    if o.get("sizing"):
        s = o["sizing"]
        L.append(f"Size for {s['risk_pct']}% risk on {f(s['account'])}: {s['units']:,.4g} units, {f(s['notional'])} notional "
                 f"({pc(s['pct_of_account']).lstrip('+')} of the account; above 100% means leverage).")
    L.append("\n### Evidence\n| Family | Vote | Reading |\n|---|---|---|")
    for e in o["evidence"]:
        L.append(f"| {e['family']} | {e['vote']:+.2f} | {e['reading']} |")
    L.append(f"\nBias = sum of votes ({o['bias_score']:+.1f}): +2.5 or more long bias, +1 lean long, under +1 and over -1 neutral"
             + (" (position horizon: positioning, liquidity and signal record count half, cycle double)." if o["horizon"] == "position" else "."))
    if brief:
        return "\n".join(L)
    a = o["analysis"]
    L.append("\n### Levels used (swing timeframe)")
    L.append("| Side | Zone | From live | Strength | What's there |\n|---|---|---|---|---|")
    zs = [z for z in o["zones"] if z["weight"] >= ZONE_MIN and abs(z["distance"]) <= 0.20]
    for z in sorted(zs, key=lambda z: -z["mid"]):
        L.append(f"| {z['side']} | {f(z['low'])}-{f(z['high'])} | {pc(z['distance'])} | {z['weight']:.1f} | {zone_text(z, 6)} |")
    if a.get("signals_now"):
        L.append(f"\n### Signals firing now, with their record on {o['symbol']}")
        L += TA.render_signal_rows(a["signals_now"])
    if a.get("derivatives") and "error" not in a["derivatives"]:
        import derivatives as D
        L.append("")
        L += D.render_snapshot(a["derivatives"])
    if full:
        L.append("\n---\n")
        L.append(TA.render(a))
    return "\n".join(L)


def strip(o):
    o = dict(o)
    a = dict(o.pop("analysis"))
    a.pop("_series", None)
    a.pop("backtest", None)
    if isinstance(a.get("derivatives"), dict):
        a["derivatives"] = dict(a["derivatives"])
        lm = a["derivatives"].get("liquidation_map")
        if isinstance(lm, dict):
            a["derivatives"]["liquidation_map"] = {k: v for k, v in lm.items() if k != "bins"}
        fu = a["derivatives"].get("funding")
        if isinstance(fu, dict) and fu.get("hl_history"):
            fu["hl_history"] = {k: v for k, v in fu["hl_history"].items() if k != "aprs"}
    o["analysis"] = a
    o["journal_row"] = journal_row(o)
    return o


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--side", choices=["long", "short"])
    p.add_argument("--horizon", choices=["swing", "position", "both"], default="swing",
                   help="swing: days to weeks (default); position: weeks to months; both: one plan each")
    p.add_argument("--brief", action="store_true", help="plan block only")
    p.add_argument("--full", action="store_true", help="append the full technical dossier")
    p.add_argument("--no-derivs", action="store_true", help="skip funding, whales and the liquidation map")
    p.add_argument("--no-backdrop", action="store_true", help="skip the macro backdrop check")
    p.add_argument("--account", type=float, help="account size in USD, for position sizing")
    p.add_argument("--risk", type=float, help="percent of the account to risk per trade, e.g. 1")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    backdrop = None
    if not args.no_backdrop:
        try:
            import derivatives as D
            backdrop = D.cached("backdrop.json", 1800, D.sentiment)
        except Exception:
            backdrop = None
    btc = None
    if any(s.upper() != "BTC" for s in args.symbols):
        try:
            bench = TA.bench_series()
            b = TA.analyze("BTC", with_derivs=False)
            btc = {"bench": bench, "ctx": {"30d": b["change"]["30d"], "stance": b["score"]["stance"]}}
        except FetchError:
            btc = None
    outs = []
    horizons = ("swing", "position") if args.horizon == "both" else (args.horizon,)
    for sym in args.symbols:
        for hz in horizons:
            try:
                outs.append(plan(sym, derivs=not args.no_derivs, side=args.side, backdrop=backdrop, btc=btc,
                                 account=args.account, risk_pct=args.risk, horizon=hz))
            except FetchError as e:
                print(f"skipped {sym.upper()}: {e}", file=sys.stderr)
    if args.json:
        print(json.dumps([strip(o) for o in outs], indent=2, default=str))
        return
    for o in outs:
        print(render(o, brief=args.brief, full=args.full))
        print()
    print("Conditional research, not instructions. Levels are where the evidence clusters; the stop is where the idea is "
          "wrong. Size so the stop costs what you planned to lose.")


if __name__ == "__main__":
    main()
