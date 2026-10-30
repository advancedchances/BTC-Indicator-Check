# BTC Indicator Check

A simple Bitcoin indicator scanner built in Python. Fetches live market data from Coinbase and CoinGecko, calculates technical indicators, and displays current market signals. This tool does not execute any trades.

## Features

- Live BTC spot, buy, and sell prices from Coinbase
- RSI (14) with overbought/oversold detection
- MACD (12/26/9) with crossover signals
- Bollinger Bands (20) with upper/lower band alerts
- 200-Week SMA with alert when price drops below (historically rare bear market signal)
- Overall signal verdict (Bullish / Bearish / Neutral)

## Setup

### 1. Install Dependencies

```bash
pip install anthropic requests ta
```

### 2. Set Your API Key

This project uses the Anthropic API. Set your key as an environment variable so it stays out of your code.

**Windows (Anaconda Prompt):**
```bash
setx ANTHROPIC_API_KEY "your-api-key-here"
```

**Mac/Linux:**
```bash
export ANTHROPIC_API_KEY="your-api-key-here"
```

Then restart your terminal and Jupyter.

### 3. Run the Notebook

Open the notebook in Jupyter Notebook and run all cells (Cell > Run All).

To refresh signals, just re-run the last two cells.

## Example Output

```
==========================================================
  BTC TRADING SIGNALS  |  2026-03-08 08:46:51
==========================================================
  Current Price :  $   67,541.00
  200W SMA      :  $   58,672.67

  [OK] Price is $8,868.33 ABOVE the 200W SMA

----------------------------------------------------------
  INDICATOR SIGNALS:
----------------------------------------------------------
  [NEUTRAL]  RSI neutral (41.9)
  [NEUTRAL]  MACD no crossover
  [NEUTRAL]  Price inside Bollinger Bands
----------------------------------------------------------
  OVERALL: NEUTRAL  (0 buy vs 0 sell signals)
==========================================================
```

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

## Disclaimer

This is a random project for educational purposes only. Not financial advice. Always do your own research before making any investment decisions.
