# Technical Analysis and Entry/Exit Plans

How to turn a ticker into entry and exit levels the user can check, using `scripts/trade_plan.py` and the two engines under it. Read this before presenting a plan.

## Contents
1. Commands
2. What each engine reads
3. How the plan is built
4. Reading the derivatives data
5. Signal track records
6. What the backtests say about BTC (30 Oct 2026)
7. Breakout checklist
8. Presenting a plan
9. Pitfalls

---

## 1. Commands

| Need | Command |
|---|---|
| Entry and exit plan for a ticker | `python scripts/trade_plan.py BTC` |
| Swing and position plans together (BTC, ETH, core holdings) | `python scripts/trade_plan.py BTC --horizon both` |
| Only the long side ("where do I buy?") | `python scripts/trade_plan.py BTC --side long` |
| Position size for a fixed risk | `python scripts/trade_plan.py SOL --account 160000 --risk 1` |
| Several coins, plan blocks only | `python scripts/trade_plan.py ETH SOL HYPE --brief` |
| Full chart dossier (every timeframe, pivots, MAs, Ichimoku, VWAPs) | `python scripts/technicals.py analyze BTC` |
| One line per coin | `python scripts/technicals.py scan BTC ETH SOL HYPE` |
| Every signal's record on a coin | `python scripts/technicals.py backtest ETH` |
| BTC cycle gauges (Mayer, Pi cycle, 2Y MA, 200W, power law) | `python scripts/technicals.py cycle` |
| Returns by calendar month | `python scripts/technicals.py seasonality BTC` |
| Funding across venues | `python scripts/derivatives.py funding BTC HYPE` |
| Liquidation map, whale liquidation prices, recent liquidations | `python scripts/derivatives.py liqmap BTC` |
| Options (put/call, max pain, OI walls, skew, DVOL, basis) | `python scripts/derivatives.py options BTC` |
| Market backdrop (Fear & Greed, MVRV Z, dominance, stablecoins, DXY, S&P 500, 10Y) | `python scripts/derivatives.py sentiment` |

Add `--json` to any of them for machine-readable output. All of them use closed bars for signals and print the live price separately. Set `CRYPTO_ANALYST_CACHE` to move the cache (the Hyperliquid leaderboard is 40 MB and is cached for six hours, whale positions for 15 minutes).

## 2. What each engine reads

**technicals.py** (candles from Coinbase, then Binance spot, Hyperliquid perps, Kraken; the longest history wins when the first venue listed the coin recently):
- RSI(14) on 4H, 1D, 3D, 1W and 1M bars; RSI against its 14-bar average; Stoch RSI(14,14,3,3); RSI range regime (bull markets hold 40–80, bear markets 20–60); regular and hidden divergences, using TradingView's built-in rule (pivots 5/5, 5–60 bars apart).
- MACD(12,26,9) on the same timeframes: side of signal and zero, histogram direction and streak, bars since the last cross.
- Period anchors: yearly, quarterly, monthly, weekly and daily opens; the prior year's, quarter's, month's, week's and day's high, low and close. On a 24/7 market the yearly open and the prior year's close are nearly the same price; both are reported.
- Classic, Fibonacci and Camarilla pivots from the prior day, week, month, quarter and year.
- Moving averages: 20/50/100/200-day SMA, 21-day EMA, 20-week SMA and 21-week EMA (the bull market support band), 50-week and 200-week SMA, and the last golden or death cross.
- Ichimoku on daily and weekly bars in two presets: default 9/26/52/26 and crypto 20/60/120/30. Reports position against the cloud, TK cross, future cloud colour, chikou, a STRONG BULL or STRONG BEAR verdict when all agree, and the edge-to-edge target when price is inside the cloud.
- Bollinger Bands(20,2): %B and band width as a percentile of 120 days (20th or below is a squeeze). ATR(14).
- VWAPs: year to date, 90-day rolling, and anchored at the 180-day swing high and low. Volume profile for 90 and 365 days: point of control and value area.
- Swing structure (pivots 5/5), Fibonacci retracements of the 180-day swing, 20- and 55-day highs and lows.
- Signals firing on the last closed bar, each graded on this coin's own history (section 5).

**derivatives.py**:
- Funding and open interest on Hyperliquid, OKX, Bitget, Gate, dYdX and Deribit, plus Binance and Bybit predicted funding through Hyperliquid (both refuse some regions directly). It also reports Hyperliquid's funding percentile over 30 days.
- OKX open interest history (hourly, 30 days), account and top-trader long/short ratios, and taker buy share.
- Whales: open positions of the top 100 Hyperliquid accounts by all-time PnL with over $1M, with entries and liquidation prices.
- Modeled liquidation map (section 4), recent OKX liquidations, Deribit options, order-book walls (Coinbase full book, else Hyperliquid's top 20 levels) and the Coinbase premium.

## 3. How the plan is built

1. **Levels.** Every level from both engines goes into one list with a weight. Higher-timeframe and widely watched levels weigh more: the yearly open and 200W SMA count 3, the 200D SMA 2.5, monthly pivots 1.5, weekly opens 1. The weights are in `technicals.py`.
   - Swing plans drop intraday levels (daily pivots, prior-day high and low).
   - Position plans also drop weekly levels and positioning, and for BTC they add cycle floors: realized price (from MVRV), power-law fair value and its −1 sd band, the 2-year MA and 0.8x the 200D SMA.
2. **Zones.** Levels within 0.35 ATR of each other merge into a zone, and a zone's strength is the sum of its weights. A zone of 7 or more means several independent methods agree on the price.
3. **Entries.** Entry 1 is the strongest zone 0.3–2.5 ATR from price, discounted for distance. Entry 2 is a deeper zone of strength 4 or more, used to scale in.
4. **Stop.** The stop goes beyond the deeper zone plus 0.5 ATR (at least 1%). If a modeled liquidation cluster or whale liquidation pocket sits just past it, the stop moves beyond the pocket. Stops parked inside a pocket get run when it is swept.
5. **Exits.** Exits sit just in front of the next three strong zones (above them for shorts), each with its reward/risk from the average entry. When no zone remains, Fibonacci extensions (1.272, 1.618) of the 180-day swing stand in.
6. **Alternative entry.** The breakout (or breakdown) level is the nearest strong zone on the other side, with its own stop and a target at least 1.5 ATR away.
7. **Evidence and bias.** Ten families vote: trend, momentum, stretch, signal record, positioning, liquidity, relative strength against BTC, seasonality, macro backdrop and BTC cycle. Most votes are capped at ±2. The sum is the bias: +2.5 or more is a long bias, +1 to +2.5 leans long, and between −1 and +1 is neutral. Position plans halve positioning, liquidity and signal record, and double cycle.
8. **Grade (A++/A/B/C).** The grade adds up points:
   - +1 for each family that agrees and −1 for each that disagrees.
   - Reward/risk counts +2 at 3 or more, +1 from 2 to 3, and −1 under 1.5. It is graded on the lowest of three numbers: R:R to exit 2, twice the R:R to exit 1, and 4.
   - +1 for an entry zone of strength 7 or more, +1 with the trend or −1 against it, and +1 if a proven signal is firing.
   - −1 if the bias leans the other way.

   A++ needs 7 points, A 5 or 6, B 3 or 4; anything less is C.
9. **Journal row.** The plan ends with a row in common trade-journal columns: Entry / Exit / Stop Loss / SL % / R:R / Rationale / Type / Setup Confidence / Expected Hold Time.

## 4. Reading the derivatives data

- **Funding.** 0.01% per 8 hours (about 11% a year) is many venues' default when the market is balanced, so it is not a crowding signal.
  - Over 25% a year, or the 90th percentile of 30 days together with 20% or more, means longs are crowded.
  - Under −5% a year means shorts are paying, which is squeeze fuel if price turns.
- **Open interest against price, 24h.**
  - Both up: new longs, so the trend has leverage behind it.
  - Price down, OI up: new shorts. Pressure is building, and that becomes squeeze fuel if the move reverses.
  - Price up, OI down: short covering, so the rally lacks new buyers.
  - Both down: a long flush. Leverage has cleared, often near short-term lows.
- **Whales.** These are real positions, but only for the top 100 Hyperliquid accounts. A one-sided whale book is only a squeeze or blow-off setup when funding agrees. Average entries show where whales would be underwater.
- **Liquidation map (a model).**
  - How it works: it replays 30 days of OKX open interest hour by hour. Each rise in OI opens a long and a short at that hour's average price, spread across 5x/10x/25x/50x/100x leverage (15/30/30/15/10%). A fall in OI closes positions pro rata, and any level price has since traded through is deleted. Clusters are scaled to the open interest on reachable venues; Binance and Bybit are missing, so absolute sizes are understated.
  - How to use it: read it for where clusters sit and which side is heavier, not for exact dollars. Heavy long liquidations below price act as a downside magnet, and heavy short liquidations above as an upside magnet.
- **Options (BTC, ETH).**
  - Put/call OI.
  - Max pain for the three largest expiries within 60 days, which pulls price into expiry.
  - Call walls above and put walls below, within 45 days. Large open interest at a strike tends to pin price or act as a level.
  - 25-delta skew near 30 days: puts more than 5 vol points over calls is priced fear (contrarian supportive).
  - DVOL percentile and the futures basis.
- **Coinbase premium.** Coinbase USD price against Binance USDT, converted at Coinbase's USDT rate. Positive means US spot demand.

## 5. Signal track records

`technicals.py` tests about 45 signals: RSI levels and crosses, Stoch RSI, MACD crosses and histogram turns, golden and death crosses, 200D and yearly-open crosses, 20/55-day breakouts, Bollinger squeeze breaks, washouts, divergences, Ichimoku cloud and TK crosses, BBAWE, 200W proximity, Mayer under 0.8, and weekly MACD, RSI-50, 21W EMA and cloud signals. Each one is graded on the coin's own history.

- Each signal is measured over 7/21/42 days if daily, or 4/8/13 weeks if weekly. It is graded at 21 days or 8 weeks, with a 10-bar cooldown between occurrences.
- **Win rate** is direction-adjusted: a bearish signal "wins" when price is lower.
- **90% floor** is the lower end of the Wilson interval on that win rate.
- **Edge** is the average return after the signal minus the average over all days. It answers whether the signal beats just being in the market.
- **Typical drawdown** is the median worst move against the signal before the horizon. Use it to judge whether a stop survives normal noise.
- **Grades:**
  - *proven*: 10 or more trades, a 90% floor above 50%, and positive edge;
  - *positive, unproven*: a positive win rate and edge, but a floor of 50% or below;
  - *coin-flip or worse*;
  - *too few*: under 5 trades.
- Everything is in-sample on one coin. With about 45 signals, two or three will look good by chance. Prefer signals that are proven on the coin *and* on BTC or ETH. Use a record to decide which trigger to wait for at a level, never as a forecast.

## 6. What the backtests say about BTC (30 Oct 2026, Coinbase daily since Nov 2015)

- **BTC rewards breakouts, not oversold buys.**
  - Proven: a close above the prior 55-day high (64% of 58, median +6.3% in 21 days) and a daily close above the Ichimoku cloud (63% of 54).
  - Negative record: daily RSI reclaiming 30 (41% win, median −1.6%). RSI under 25 wins 60% of the time but has negative edge.
  - Breakdowns below the 20-day low fail more often than they work (43%).
- **The weekly 21-week EMA matters.** A weekly close below it led lower 8 weeks later in 61% of 18 cases (median −7.3%). A weekly RSI cross below 50 did so in 67% of 18. Neither is proven yet, but both are worth respecting.
- **What this means for entries.** At a support zone, wait for a daily close back above a lost level, a cloud reclaim or a 55-day-high style breakout rather than buying the first oversold print. Use oversold readings only to know where to start watching.

Re-run `technicals.py backtest BTC` before quoting these; they move as history grows.

## 7. Breakout checklist

Pattern breakouts (inverse head and shoulders, triangles, falling wedges, cloud breakouts) often stall at the neckline and stop out; a trade journal full of "stalled at neckline" exits is the usual symptom. Before calling a breakout, check:

1. **Close, not wick.** A daily close beyond the level, ideally a weekly close too.
2. **Volume.** At least 1.5x the 20-day average on the breakout day (relative volume is in the dossier).
3. **Not exhausted.** Daily RSI under 75, and no regular bearish divergence on the daily or 3D.
4. **Room to run.** The next strong zone is at least 1.5 ATR away. If a strong zone sits just above the neckline, the breakout runs straight into supply.
5. **Record.** Check whether a breakout of this kind (20D or 55D high, cloud break, squeeze break) has a positive record on this coin.
6. **Failure rule.** A close back inside the old range means the breakout failed. Cut it then, rather than waiting for the stop. Add a time stop: if exit 1 hasn't hit in twice the expected hold, close it.

## 8. Presenting a plan

Lead with the plan, then the evidence.

1. One line with the bias and grade, e.g. "BTC: neutral on the swing view (C); leans long on the position view (A)".
2. The plan table: entries (zones with what's there), stop, exits with R:R, and the alternative breakout entry.
3. The three to five readings that matter most, each with its number: the strongest agreeing families and the strongest disagreeing one.
4. **What to wait for.** The trigger at the entry zone, preferring triggers with a positive record on this coin, plus any weekly or monthly close that changes the picture.
5. **What makes it wrong.** The stop, the thesis line on a weekly close, and positioning that would flip (funding crowding, whales flipping).

When the user has shared positions or exit levels, relate the plan to them in the conversation only: distance to their written exit lines, and whether a planned entry conflicts with a rule they wrote (for example, "core exits happen at the weekly review"). Keep holdings out of anything shareable.

Plans are conditional research: say where a trade makes sense and where it is wrong. Don't tell the user to buy or sell, and don't hide a C grade behind confident wording.

## 9. Pitfalls

- **Forming bars.** Signals use closed bars. A daily close above a level means the UTC daily close, and a weekly close means the Sunday 23:59 UTC close (Monday 00:00 open). Say which one a trigger needs.
- **Venue differences.** Hyperliquid perp candles (used when they give the longest history, as for HYPE) differ slightly from spot. Binance spot is quoted in USDT. The dossier header names the source.
- **3D bars** are counted from the Unix epoch, so they can differ from TradingView's 3D bars.
- **Thin coins.** For coins with little derivatives activity the positioning and liquidity families come back empty. Say so; don't treat silence as neutral evidence.
- **Levels aren't forecasts.** A strong zone says many traders watch that price. It doesn't say price will reach it or hold there.
