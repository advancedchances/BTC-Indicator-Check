#!/usr/bin/env python3
"""
Number formats shared by the skill's reports, so every script prints a price,
a percent or a ratio the same way. Standard library only.

Every function returns "n/a" for None (and for NaN), so a missing reading
never crashes a report or prints "nan".

  price(77380.4)    -> "77,380"     prices of 1,000 or more: whole units with thousands separators
  price(2.24931)    -> "2.24931"    smaller prices: six significant digits (nd)
  pct(0.0512)       -> "+5.1%"      signed percent, one decimal (nd)
  rate(0.5312)      -> "53%"        unsigned whole percent: win rates, shares, probabilities
  num(1.234)        -> "1.23"       plain number, two decimals (nd): ratios like Sharpe or profit factor
  signed(0.261)     -> "+0.26"      signed number, two decimals (nd): R multiples

Formats only one script uses (dollar amounts, valuation multiples, p-values)
stay in that script.
"""

import math


def _missing(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def price(x, nd=6):
    if _missing(x):
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:,.{nd}g}"


def pct(x, nd=1):
    return "n/a" if _missing(x) else f"{x * 100:+.{nd}f}%"


def rate(x):
    return "n/a" if _missing(x) else f"{x * 100:.0f}%"


def num(x, nd=2):
    return "n/a" if _missing(x) else f"{x:.{nd}f}"


def signed(x, nd=2):
    return "n/a" if _missing(x) else f"{x:+.{nd}f}"
