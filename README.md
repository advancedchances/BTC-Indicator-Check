# BTC Indicator Check

A simple Bitcoin indicator scanner built in Python. Fetches live market data from Coinbase and CoinGecko, calculates technical indicators, and displays current market signals. This tool does not execute any trades.

## Features

- Live BTC spot, buy, and sell prices from Coinbase
- RSI (14) with overbought/oversold detection
- MACD (12/26/9) with crossover signals
- Bollinger Bands (20) with upper/lower band alerts
- 200-Week SMA with alert when price drops below (historically rare bear market signal)
- Overall signal verdict (Bullish / Bearish / Neutral)
- The verdict's own track record: how it has traded since 2016, after costs, against buy-and-hold

Indicators are computed on closed daily Coinbase candles with the skill's TradingView-matching formulas (`ta_core.py`), so the notebook and the skill always agree.

## Setup

No API keys and nothing to install: the notebook and every script use only the Python standard library (Python 3.12 or later, as CI runs) and public endpoints.

Open `BTC Indicator Check.ipynb` in Jupyter from the repo root and run all cells (Cell > Run All). To refresh, re-run from the price cell down.

## Example Output

```
==========================================================
  BTC TRADING SIGNALS  |  2026-10-31 14:53 UTC
==========================================================
  Current Price :  $   77,398.23
  200W SMA      :  $   67,157.27

  [OK] Price is $10,240.96 ABOVE the 200W SMA

----------------------------------------------------------
  INDICATOR SIGNALS (last daily close):
----------------------------------------------------------
  [NEUTRAL]  RSI neutral (34.3)
  [NEUTRAL]  MACD no crossover
  [ BUY  ]  Price at/below lower Bollinger Band
----------------------------------------------------------
  OVERALL: BULLISH  (1 buy vs 0 sell signals)
==========================================================
```

**Read the verdict as a dashboard, not a trading rule.** Tested as a long/flat rule on daily bars, with 15 bps of costs per trade, it lost to buy-and-hold on BTC (Sharpe 0.44 vs 1.03), ETH and SOL. A trend rule scaled to a volatility target beat holding on all three, with about a third of the drawdown (see *Strategy lab* below).

## Crypto Analyst Skill (Claude Code)

`.claude/skills/crypto-analyst/` is a Claude Code skill for evidence-first crypto research. Claude Code loads it automatically when you work in this repo. Ask things like *"find the most mispriced crypto assets right now"* or *"do a deep dive on ONDO — is it undervalued?"*.

It follows a fixed workflow:

1. Market regime (BTC/ETH trend, dominance, stablecoin liquidity, flows, funding/OI, macro, narratives)
2. A broad candidate set, then a quick elimination screen
3. Deep research: tokenomics, unlocks, revenue/fees, organic vs. incentive-driven growth, on-chain flows, value accrual
4. Relative valuation on metrics that fit each business model
5. Catalysts (30d / 1–3m / 3–6m / 6–12m, confirmed vs. speculative) and competition
6. An aggressive bear case with thesis-invalidation conditions
7. A scored comparison table, a deep dive per finalist, and a closing **"WHAT COULD I BE WRONG ABOUT?"** section

Reference files cover the research checklist, the valuation metrics for each business model, data sources and the report template. `scripts/valuation_calc.py` computes valuation ratios, sector-peer comparisons and screening flags from a CSV of researched inputs:

```bash
python .claude/skills/crypto-analyst/scripts/valuation_calc.py --template > candidates.csv
python .claude/skills/crypto-analyst/scripts/valuation_calc.py candidates.csv
```

Besides valuation multiples, the calculator reports holder yield (what buybacks and fee shares pay holders each year) and buyback cover (that payout against the next year's unlocks), and flags when unlocks trigger the score cap. Two companion scripts keep scoring and follow-ups consistent:

```bash
python .claude/skills/crypto-analyst/scripts/research_score.py scores.csv   # Research Score with full breakdown and caps
python .claude/skills/crypto-analyst/scripts/call_scorecard.py calls.csv    # how earlier calls did against BTC, by group
```

`scripts/market_data.py` gathers the inputs without API keys and checks, at the start of each session, which data sources the session can reach:

```bash
python .claude/skills/crypto-analyst/scripts/market_data.py check                    # which sources are reachable right now
python .claude/skills/crypto-analyst/scripts/market_data.py prices BTC ETH SKY       # Coinbase spot prices and 24h volume
python .claude/skills/crypto-analyst/scripts/market_data.py revenue aave sky         # DefiLlama fees, revenue, holders revenue
python .claude/skills/crypto-analyst/scripts/market_data.py mcaps aave sky           # CoinGecko market caps via DefiLlama
```

Ask *"update the scan"* or *"how did your picks do?"* after a report and the skill runs in follow-up mode: it re-prices the earlier calls, scores them against BTC, sorts new evidence into new / closed / open / contradicting / corrections, and rescores with the full breakdown.

### Entry and exit plans for any ticker

Give it a ticker and `trade_plan.py` returns entry zones, a stop, three exits with reward/risk, an alternative breakout entry, and a setup grade (A++/A/B/C). It also prints a row in trade-journal columns.

```bash
python .claude/skills/crypto-analyst/scripts/trade_plan.py BTC --horizon both --side long   # swing and position entries for BTC
python .claude/skills/crypto-analyst/scripts/trade_plan.py HYPE                             # long and short plans with all evidence
python .claude/skills/crypto-analyst/scripts/trade_plan.py ETH SOL ZEC --brief              # plan blocks only
python .claude/skills/crypto-analyst/scripts/trade_plan.py SOL --account 160000 --risk 1    # add a position size for 1% risk
```

Every level in a plan traces to a reading printed beside it. Two engines supply them, both standard library only and free of API keys:

- `technicals.py` is the chart engine. It uses TradingView's formulas and computes:
  - RSI, Stoch RSI and divergences on 4H, daily, 3D, weekly and monthly bars, and MACD on the same timeframes except 3D;
  - yearly, quarterly, monthly and weekly opens, and the prior year's close, high and low;
  - classic, Fibonacci and Camarilla pivots for each period;
  - the 20/50/100/200-day, 20/21/50/200-week and 2-year averages;
  - Ichimoku (default and crypto presets), Bollinger squeeze, anchored VWAPs and volume profile;
  - a confluence map of levels that agree, and a backtest of every signal firing now on that coin's own history.

  It also prints BTC cycle gauges (Mayer, Pi cycle, 200W, power law) and returns by calendar month.
- `derivatives.py` covers positioning and the backdrop:
  - funding on eight venues;
  - open interest history and long/short ratios;
  - live positions and liquidation prices of the top Hyperliquid traders;
  - a modeled liquidation map;
  - Deribit options (max pain, open-interest walls, skew, DVOL, basis);
  - order-book walls and the Coinbase premium;
  - the market backdrop: Fear & Greed, MVRV Z-score, dominance, stablecoins, the dollar, the S&P 500 and the 10-year yield.

```bash
python .claude/skills/crypto-analyst/scripts/technicals.py analyze BTC      # full technical dossier
python .claude/skills/crypto-analyst/scripts/technicals.py backtest ETH     # which signals have worked on ETH
python .claude/skills/crypto-analyst/scripts/derivatives.py liqmap BTC      # liquidation map and whale liquidation prices
python .claude/skills/crypto-analyst/scripts/derivatives.py sentiment       # market backdrop and risk gate
```

`references/technical-analysis.md` explains how the plan is built, how to read each input, what the BTC backtests show, and a breakout checklist. Plans are conditional research: where a trade makes sense and where it is wrong. They are not instructions to trade.

More tools built on the same engine:

```bash
python .claude/skills/crypto-analyst/scripts/trade_plan.py MSTR IBIT --asset stock         # stocks and ETFs (Yahoo Finance, SPY benchmark)
python .claude/skills/crypto-analyst/scripts/trade_plan.py BTC --pine btc.pine             # TradingView Pine v6 indicator: levels + alert conditions
python .claude/skills/crypto-analyst/scripts/trade_plan.py BTC --out reports/              # plans as .md and .json files for a digest to read
python .claude/skills/crypto-analyst/scripts/trade_plan.py BTC ETH SOL --account 100000 --risk 1 --vol-target 20   # sizes, Kelly line, correlation check
python .claude/skills/crypto-analyst/scripts/plan_backtest.py BTC ETH SOL --holdout 2023-01-01   # walk-forward test of the plans since 2017, after costs, with bots
python .claude/skills/crypto-analyst/scripts/export_dashboard.py BTC --out /tmp/btc_map    # dashboard data files + alerts.json
python .claude/skills/crypto-analyst/scripts/journal_stats.py trades.csv --breaker 10      # grade a trade journal; replay a drawdown circuit breaker
```

Every plan prints its **tested record**: how plans of the same kind (horizon, side, with or against the trend, grade) traded in the walk-forward test, after 10 bps fees and 5 bps slippage. The test replayed 4,578 chart-only plans on BTC, ETH and SOL from 2017 to 2026:

- Overall, the plans lost money after costs.
- Short plans and counter-trend plans lost about 0.2R a trade.
- Swing longs with the trend at grade A were the one kind positive in both halves of the sample: +0.29R before 2023 and +0.25R after. Their 90% floor is about zero.
- As a bot taking one plan at a time per coin at 1% risk, the best filter made a Sharpe of 0.61, against 1.16 for simply holding.

### Strategy lab: which rules beat holding

`strategy_lab.py` tests rules that set a position each day against buy-and-hold. Every rule gets the same scorecard: CAGR, Sharpe, Sortino, max drawdown, Calmar, time in market and turnover. Costs are included, positions are set on the close and traded at the next open, and every result carries a check on whether it beat holding by more than luck:

```bash
python .claude/skills/crypto-analyst/scripts/strategy_lab.py BTC ETH SOL --split 2023-01-01   # each rule on each coin, before/after 2023
python .claude/skills/crypto-analyst/scripts/strategy_lab.py BTC --grid                       # parameter grids: plateau or spike?
python .claude/skills/crypto-analyst/scripts/strategy_lab.py --basket BTC ETH SOL XRP DOGE ADA LTC LINK BCH   # rotation and basket rules
```

Results on 31 Oct 2026, with 15 bps costs:

| Rule | BTC Sharpe | ETH | SOL | Beats hold (BTC / ETH / SOL) | BTC max DD |
|---|---|---|---|---|---|
| Buy and hold | 1.03 | 0.74 | 0.70 | benchmark | −84% |
| Notebook verdict | 0.44 | 0.31 | 0.48 | 1% / 7% / 28% | −76% |
| Long above the 200-day SMA | 1.10 | 0.93 | 0.71 | 61% / 79% / 55% | −71% |
| Trend (share of 28/56/112/224-day returns up) | 1.28 | 0.90 | 0.94 | 93% / 79% / 77% | −63% |
| Trend scaled to 40% volatility | 1.38 | 1.03 | 0.97 | 96% / 90% / 82% | −35% |

**Beats hold** is the share of block-bootstrap resamples in which the rule's Sharpe beat buy-and-hold's.

- The volatility-targeted trend rule's edge came from the crashes: it lost 26% in 2018 and 20% in 2022, while BTC lost 73% and 64%.
- Since 2023 it has matched holding's Sharpe on all three coins with about half the drawdown.
- Cross-coin momentum (rotating into last month's winners) did not reliably beat an equal-weight basket.

`references/strategy-research.md` has the full results, the methods (taken from the review of quants.md's 50-strategy library) and what they mean for a bot.

### Tests

```bash
python -m unittest discover -s .claude/skills/crypto-analyst/tests -v   # offline, standard library only; CI runs it on every push
```

## Disclaimer

This is a random project for educational purposes only. Not financial advice. Always do your own research before making any investment decisions.
