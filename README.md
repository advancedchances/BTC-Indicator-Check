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

## Disclaimer

This is a student project for educational purposes only. Not financial advice. Always do your own research before making any investment decisions.
