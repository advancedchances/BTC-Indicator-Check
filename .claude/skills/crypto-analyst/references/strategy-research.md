# Strategy Research: What Tested Out

What the skill's backtests say about which rules have an edge, how they were tested, and what that means for a bot. Read this before answering "does this indicator/strategy work?", before designing or reviewing a trading bot, and before quoting any backtest number. Results are from the 31 Oct 2026 session; re-run the commands before quoting them, because they move as history grows.

## Contents
1. Where the methods come from
2. How every test is run
3. Results: single-coin rules
4. Results: cross-coin rules
5. Results: the trade plans as a bot
6. Results: signal records after the multiple-testing fix
7. What this means for a bot
8. Pitfalls

---

## 1. Where the methods come from

quants.md (the QuantJourney backtester's library of 50 strategies) was reviewed in October 2026 for ideas worth keeping. Most of its strategies are equity-ETF examples with Sharpe ratios of 0.6 or less; the strategies were not the useful part. Its *method* was:

- **One scorecard for every rule:** total return, CAGR, Sharpe, max drawdown, win rate and profit factor.
- **Costs in every test.**
- **Losing strategies are published, not hidden.**
- **Walk-forward folds treated as diagnostics, not independent evidence,** with a purge gap before each out-of-sample window.
- **The deflated Sharpe ratio** for parameter searches.
- **Position-sizing tools:** risk per trade, volatility target and Kelly.

Of its strategy families, three have real evidence behind them and fit crypto bots: time-series trend following, volatility targeting, and cross-sectional (relative) momentum. Those were tested here. Mean-reversion rules (RSI dips, Bollinger reversion), pairs ratios, intraday scalps and risk parity were not built: either they have no reliable edge in crypto (the ETH/BTC ratio trended for years, so a z-score bet on it fails), or they manage risk without adding return.

## 2. How every test is run

| Tool | What it tests | Command |
|---|---|---|
| `strategy_lab.py` | Rules that set a position each day (trend, volatility target, momentum, the notebook's verdict) against buy-and-hold | `python scripts/strategy_lab.py BTC ETH SOL --split 2023-01-01 --grid` |
| `plan_backtest.py` | `trade_plan.py`'s plans replayed weekly since 2017, and bots that take them | `python scripts/plan_backtest.py BTC ETH SOL --holdout 2023-01-01` |
| `technicals.py backtest` | Each of ~47 chart signals on one coin | `python scripts/technicals.py backtest BTC` |

Rules shared by all three:
- **No look-ahead.** Signals use closed bars. `strategy_lab.py` sets the position on the daily close and trades at the next open (a unit test checks that a jump on the signal bar can't be captured).
- **Costs.** 10 bps fee plus 5 bps slippage by default. Use `--fee-bps 40` or more for Coinbase Advanced retail fees; Hyperliquid's taker fee is about 4.5 bps.
- **Benchmark, not zero.** Any long crypto book has had a Sharpe above zero, so "Sharpe above zero" proves nothing. `strategy_lab.py` reports **Beats hold**: the share of block-bootstrap resamples in which the rule's Sharpe beat buy-and-hold's. It resamples 21-day blocks with the strategy and benchmark together, which keeps fat tails and volatility clustering.
- **Picking the best of a grid costs confidence.** With `--grid`, the best setting's Beats-hold figure is raised to the power of the number of settings tried. This is conservative because neighbouring settings are correlated. The deflated Sharpe (against zero) is in the JSON.
- **Overlap.** Weekly plan snapshots and closely spaced signals share price paths. Win-rate floors use only trades or firings that don't overlap. The floor on average R comes from a bootstrap over whole months of trades.
- **Later-period check.** `--split` / `--holdout 2023-01-01`. The rules' design saw all of history, so this checks stability, not true out-of-sample performance. Only results logged from now on test that.

## 3. Results: single-coin rules

Coinbase daily bars: BTC from Nov 2016, ETH from May 2017, SOL from Jun 2022 (each needs 370 days of warm-up). 15 bps costs. Long or flat, never leveraged.

| Rule | BTC Sharpe | ETH | SOL | Beats hold (BTC / ETH / SOL) | Max DD (BTC / ETH / SOL) |
|---|---|---|---|---|---|
| Buy and hold | 1.03 | 0.74 | 0.70 | benchmark | −84% / −94% / −79% |
| Notebook verdict (RSI/MACD/Bollinger vote) | 0.44 | 0.31 | 0.48 | 1% / 7% / 28% | −76% / −90% / −66% |
| Long above the 200-day SMA | 1.10 | 0.93 | 0.71 | 61% / 79% / 55% | −71% / −81% / −59% |
| Trend: share of 28/56/112/224-day returns up | 1.28 | 0.90 | 0.94 | 93% / 79% / 77% | −63% / −70% / −58% |
| **Trend scaled to 40% volatility** | **1.38** | **1.03** | **0.97** | **96% / 90% / 82%** | **−35% / −34% / −33%** |

- **The notebook's verdict loses to holding on every coin.** It trailed hold in 7 of 11 calendar years on BTC. It turns its capital over 19 times a year (about 10 round trips) and is out of the market 58% of the time, much of it during rallies. Keep it as a dashboard reading, never as a rule.
- **Volatility-targeted trend is the one rule that holds up everywhere.** All five volatility targets in its grid (20–80%) beat hold on all three coins: a plateau, not a spike. After allowing for five tries, the chance the best setting really beats hold is 91% on BTC, 61% on ETH and 36% on SOL (SOL has only 4.4 years of data). Its CAGR is lower than holding (BTC +45% vs +59%), because its average exposure is 44%.
- **Where the edge comes from: crashes.** Before 2023 its BTC Sharpe was 1.57 against 1.04 for holding. From 2023 to Oct 2026 it was 1.02 against 1.10 on BTC, 0.57 against 0.57 on ETH and 1.12 against 1.11 on SOL. Over that same stretch it cut max drawdown about in half: −26% vs −53% on BTC, −34% vs −68% on ETH, −33% vs −76% on SOL. In 2018 BTC fell 73% and the rule lost 26%; in 2022 BTC fell 64% and it lost 20%. Read it as "the same risk-adjusted return as holding in good years, and far smaller losses in crashes", not as a return booster.
- **Single moving averages and single lookbacks are fragile.** The best SMA length was 50 on BTC and 20 on ETH, and the best single lookback was 14, 7 and 28 days on the three coins. After the grid correction, the best SMA's chance of beating hold was 65% on BTC, 42% on ETH and 2% on SOL. Averaging four lookbacks (tsmom) is steadier than any one of them.

**Stocks are different.** On SPY and QQQ (Yahoo daily bars from mid-2017, `--asset stock`), the trend rules did not beat holding:
- Volatility-targeted trend beat hold in only 8% and 26% of resamples.
- The 200-day SMA filter cut max drawdown (−24% against −32% and −37%) at a similar or lower Sharpe (0.65 vs 0.78 on SPY, 0.93 vs 0.87 on QQQ).

quants.md's own equity-ETF strategies, all with Sharpe ratios of 0.62 or less, point the same way. On US index ETFs, timing rules mostly buy a smoother ride, not more return. Don't carry the crypto result over to stocks.

**A circuit breaker is not an edge.** A 25% drawdown breaker with a 30-day pause (`--breaker 25`):
- On buy-and-hold BTC, it cut the Sharpe from 1.03 to 0.62 and the CAGR from +59% to +21%, while barely improving the max drawdown (−77% vs −84%). It sold after drops and missed the rebounds.
- On volatility-targeted trend, it changed almost nothing (Sharpe 1.37 vs 1.38), because the volatility scaling had already cut exposure.

## 4. Results: cross-coin rules

Basket: BTC, ETH, LTC, BCH, XRP, ADA, LINK, DOGE, SOL, XLM and ETC, Aug 2017 to Oct 2026, rebalanced weekly. **Survivorship bias:** these are coins still trading today (no LUNA, FTT), so compare the rules with the equal-weight basket, which shares the bias, and not with BTC.

| Rule | Sharpe | CAGR | Max DD | Beats basket | Sharpe before / from 2023 |
|---|---|---|---|---|---|
| Equal-weight basket | 0.81 | +38% | −89% | benchmark | 0.85 / 0.77 |
| BTC buy and hold | 0.81 | +37% | −84% | 53% | 0.73 / 1.10 |
| Top 3 by 28-day return, while positive (xsmom) | 0.85 | +43% | −78% | 54% | 1.00 / 0.58 |
| Each coin's trend at 40% volatility, 1/N each | 1.01 | +24% | −29% | 89% | 1.26 / 0.63 |

- **Cross-sectional momentum showed no reliable edge.** 15 of 20 settings beat the basket more often than not, but none clearly. After allowing for 20 tries, the best setting's chance of really beating the basket is 2%, and the rule weakened after 2023. Rotating into last month's winners is not worth a bot's complexity on this evidence.
- **The trend basket cut the drawdown from −89% to −29%,** but its Sharpe trailed the basket from 2023 (0.63 vs 0.77). Since 2023, holding BTC alone beat every alt basket (Sharpe 1.10). That fits SKILL.md's "alts at all?" rule.

## 5. Results: the trade plans as a bot

`plan_backtest.py` replayed 4,578 chart-only plans, weekly on BTC, ETH and SOL from 2017, after 10 bps fees and 5 bps slippage. Costs took 0.02–0.05R per trade.

- **Overall, the plans lost money after costs.** Most filled trades ended at the stop (1,003) or the time stop (609). Only 220 reached every exit.
- **Shorts lose.** Swing shorts averaged −0.23R (90% floor −0.34R), and position shorts −0.25R.
- **Counter-trend plans lose.** Swing plans against the trend averaged −0.20R (floor −0.31R). Swing plans with the trend broke even (+0.00R).
- **One kind of plan was positive in both halves:** swing, long, with the trend, grade A. It won 53% of 120 filled trades at +0.26R after costs (+0.29R before), with +0.29R before 2023 and +0.25R from 2023. But its 90% floor is −0.00R, so the data can't rule out no edge.
- **A++ did worse than A** (37% of 19, −0.05R). Position plans showed no edge at any grade.
- **As a bot, the plans trail simple trend following.** Taking one plan at a time per coin at 1% risk, the best filter (swing, long, with the trend, grade A or A++) made a Sharpe of 0.61 with a −8.6% max drawdown, in the market 26% of the time. Taking every primary swing plan made 0.28. That filter was picked after seeing an earlier replay, so treat 0.61 as optimistic. For comparison, equal-weight holding of BTC, ETH and SOL made 1.16, and volatility-targeted trend made 1.38 on BTC.

`trade_plan.py` quotes the matching record under every plan from `references/plan_calibration.json`: R after costs, its 90% floor, and the record from 2023 on alone. Rebuild it after changing the plan logic:

`python scripts/plan_backtest.py BTC ETH SOL --holdout 2023-01-01 --csv trades.csv --calibration references/plan_calibration.json`

## 6. Results: signal records after the multiple-testing fix

`technicals.py` tests about 47 signals per coin. Until October 2026, "proven" meant a 90% win-rate floor above 50% on firings that could overlap. On BTC that made a 55-day-high breakout "proven" (64% of 58). But BTC was higher 21 days later from 56% of all days, and only 48 of those firings were far enough apart to count separately, of which 60% won (p = 0.31).

"Proven" now needs three things:
- 10 or more firings at least one horizon apart;
- positive edge;
- a binomial p-value against the any-day win rate that survives a 10% false-discovery rate (Benjamini–Hochberg) across every signal tested on the coin.

On BTC (31 Oct 2026) nothing is proven. The strongest signals are bearish weekly ones:
- a weekly RSI cross below 50: 77% of 13 separate firings were lower 8 weeks later, against 42% from any week (p = 0.011);
- a weekly close below the 21-week EMA: 71% of 14 (p = 0.025).

With 47 tests, neither clears the correction, but both are worth respecting as exit or de-risking cues. Oversold buys did worst: daily RSI reclaiming 30 won 32% of 25 separate firings against 56% for any day.

## 7. What this means for a bot

1. **Exposure layer: trend with a volatility target.** Decide how much to hold from the tsmom signal scaled to a volatility target (`strategy_lab.py`'s voltrend). Take a 20–40% target for a smoother ride, or 60–80% to keep more upside. It is the only tested rule that beat holding on all three coins, and its benefit is avoiding crashes.
2. **Entries: the plan's levels, long side, with the trend.** Use `trade_plan.py` for where to add within the exposure the trend layer allows. Its swing-long-with-trend-A record is the only positive one.
3. **No shorts and no counter-trend fades** on this evidence. Use short-side levels to cut or exit.
4. **Sizing:**
   - `--vol-target` caps each position's volatility.
   - The Kelly line under the plan size says how much risk the tested record supports. It gives zero when the record's 90% floor is at or below zero.
   - The portfolio check shows that plans on BTC, ETH and SOL behave like about one bet (correlation about 0.86). Size them as a group.
5. **Circuit breaker:** `journal_stats.py --breaker 10` replays a drawdown pause on a real journal, and `strategy_lab.py --breaker 25` shows its effect on a rule. On BTC it hurt buy-and-hold badly and did nothing for the trend rule. Keep a kill switch for operational failures (a broken feed, runaway orders), but don't expect a drawdown pause to add return. Check whether one would have helped your own record before adopting it.
6. **Paper trade first, and log the plans.** None of these tests is truly out of sample. A bot's first months of logged trades are the first real test: grade them with `call_scorecard.py` or `journal_stats.py`.

## 8. Pitfalls

- **CAGR vs Sharpe.** A rule that beat holding on Sharpe or drawdown but not on CAGR traded return for a smoother ride. Say which one it bought.
- **Daily bars.** Intraday stops, fills and liquidations are not modelled. Leverage turns a −35% drawdown into a margin call.
- **Costs scale with turnover.** voltrend trades about 12 times its capital a year: at 50 bps a side, that costs about 6% a year. Re-run with your venue's fees.
- **Short histories.** SOL's test is 4.4 years. Newer coins can't support a verdict.
- **Funding and taxes** are not modelled. On perps, longs pay about 11% a year at the common default funding rate (0.01% per 8 hours), and more in bull markets.
