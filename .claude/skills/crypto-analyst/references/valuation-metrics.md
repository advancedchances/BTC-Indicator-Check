# Valuation Metrics — What Makes Sense for Which Asset

A ratio only means something if its numerator and denominator are economically connected for that business. Pick metrics by business model, compare only against genuine peers, and say when a metric doesn't apply rather than forcing one.

## General rules

- **Annualize from 30 or 90 days**, not from a single day: `annualized = revenue_30d × 365 / 30`. Mention seasonality or one-off spikes (an airdrop, a memecoin mania, a liquidation cascade).
- **MC vs. FDV**: use market cap for what the market pays today and FDV for what it pays once all supply exists. When float is low, FDV-based multiples tell you more.
- **Revenue definitions differ.** Compare fees with fees and revenue with revenue across peers, from the same source where possible. Where token value capture is the question, prefer *holders revenue* or *earnings (revenue − incentives)*.
- **Peers must share a business model.** A DEX vs. a DEX, a lending market vs. a lending market. Cross-category comparisons (an L1 vs. a DEX on P/F) are apples to oranges.
- **Small samples**: with only 2–3 peers, a "discount to the median" is a weak signal. Say so.
- **Cheap for a reason?** A low multiple on shrinking revenue, weak value accrual or heavy dilution isn't mispricing. It's the market pricing risk correctly. Always pair a multiple with its trend and with the value-accrual check.

## By business model

| Business model | Primary metrics | Secondary | Beware |
|---|---|---|---|
| **DEX / AMM** | MC / annualized revenue, FDV / revenue, market share of volume | Volume / TVL (capital efficiency), fee per trade | Volume from wash trading or incentives; fees that go to LPs, not the token |
| **Perps / derivatives DEX** | MC / revenue, FDV / revenue, OI and volume share | Revenue per user, insurance fund size | Points-driven volume, liquidity vaults funded by the protocol |
| **Lending / money markets** | MC / revenue, MC / active loans | Utilization, bad-debt history, revenue per $ borrowed | TVL inflated by looping; incentive-subsidized borrow rates |
| **Liquid staking / restaking** | MC / TVL (a take rate on AUM), MC / revenue, market share | Fee take rate, operator concentration | Restaking points; TVL counted in several layers |
| **Stablecoin issuers / RWA / tokenization** | MC / AUM (tokenized assets or supply), MC / net interest revenue | Supply growth, distribution partners, attestation quality | Revenue that depends on interest rates (it falls when rates fall) |
| **L1 smart-contract platforms** | Real economic value (fees + MEV) and its trend, stablecoin supply on chain, active addresses, developer share | MC per monthly active address, fees burned vs. issuance (net inflation) | P/F treats all fees as token cash flow; only burned or staker-paid fees accrue, and L1s carry a "monetary premium" that cash-flow multiples can't capture. Compare L1s with L1s. |
| **L2 rollups** | Sequencer profit (L2 fees − L1 data costs), MC / sequencer profit, TVS and activity share | Stage of decentralization, stablecoin supply | Many L2 tokens have no claim on sequencer revenue. Check value accrual first. |
| **DePIN** | MC / annualized *demand-side* revenue (paid by real customers) | Burn vs. emissions (burn-and-mint balance), supply growth vs. demand growth | "Revenue" that is really token rewards to the supply side; hardware sales counted as protocol revenue |
| **Oracles / middleware / infra** | MC / revenue, number of paying integrations | Total value secured (TVS), market share | TVS is not revenue; free integrations inflate adoption counts |
| **Bitcoin** | Not a cash-flow asset. Use MVRV, realized cap, price vs. 200-week SMA, ETF flows, hash rate, holder behavior (long-term vs. short-term holders) | Miner revenue, fee share | Don't apply P/F or P/E |
| **Memecoins / governance-only tokens** | Fundamental multiples don't apply | Liquidity, holder distribution, exchange coverage | Say plainly that there is no fundamental valuation anchor. These rarely belong in a fundamentals-driven finalist list. |

## Useful derived metrics

| Metric | Formula | Why it matters |
|---|---|---|
| Float | market cap ÷ FDV | Low float means future dilution is priced into FDV, not MC |
| Unlock overhang | USD unlocking in 12 months ÷ market cap | Supply the market must absorb |
| Unlock pressure vs. liquidity | USD unlocking in 30 days ÷ average daily volume | How many days of volume a cliff represents |
| Earnings | revenue − token incentives | Positive means the protocol isn't paying for its own growth |
| Revenue growth | revenue 30 days ÷ prior 30 days − 1 (also quarter over quarter) | Multiples only look cheap relative to growth |
| Growth-adjusted multiple | (MC / annualized revenue) ÷ revenue growth % (year over year) | A crude PEG. Use it only when growth is stable and positive. |
| Valuation per active user | MC ÷ monthly active users | For consumer apps and chains; compare within a category |
| Real yield | staking yield paid from revenue ÷ staked value | Separates cash-flow yield from inflation |
| Net inflation | (emissions − burns − buybacks) ÷ circulating supply, annualized | True dilution rate |

## Using `scripts/valuation_calc.py`

Put the raw inputs for each candidate into a CSV (print the header with `python scripts/valuation_calc.py --template`) and run:

```bash
python scripts/valuation_calc.py candidates.csv            # markdown table
python scripts/valuation_calc.py candidates.csv --json     # machine-readable
```

It computes float, annualized revenue, MC/revenue, FDV/revenue, MC/fees, MC/TVL, MC per user, earnings, revenue growth and unlock overhang. It also computes each asset's MC/revenue premium or discount against the median of its sector peers, and raises screening flags (LOW_FLOAT, UNLOCK_OVERHANG, SUBSIDIZED, REV_DECLINING, NO_REVENUE_DATA). Blank inputs stay blank and are never filled with zero, so any ratio that depends on missing data shows as `n/a`. The flags are heuristics that prompt a closer look, not verdicts. Decide which ratios actually apply using the table above.
