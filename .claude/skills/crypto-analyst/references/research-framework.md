# Research Framework — Per-Candidate Checklist

Work through this for every survivor of the elimination screen. You don't need every line for every asset. Skip items that don't apply to the business model and say why. But write down any item you couldn't find data for: "n/a (not disclosed)" is a finding, not a failure.

## Contents
1. Market data
2. Supply, unlocks and allocations
3. Token utility and value accrual
4. Protocol fundamentals
5. Organic vs. incentive-driven growth
6. On-chain evidence
7. Ecosystem, partnerships and adoption
8. Security, centralization and regulatory posture
9. Summary questions

---

## 1. Market data

Take these from a single source at a single timestamp so they're consistent with one another.

- Price, market cap and FDV
- Circulating, total and maximum supply. **Float** = circulating ÷ max (or ÷ total if there's no cap)
- 24h and 30d spot volume, and where the volume happens (CEX vs. DEX, and whether one venue dominates)
- Liquidity: ±2% order-book depth on the main venues, DEX pool depth. Thin liquidity means your "price" may not hold for size.
- Perp listings, funding and open interest relative to market cap. High OI/MC plus positive funding means crowded longs.
- Drawdown from the all-time high, and performance over 30 days, 90 days and 1 year relative to BTC and to sector peers

## 2. Supply, unlocks and allocations

- **Unlock schedule**: amount and USD value unlocking in the next 30 days, 3 months, 6 months and 12 months, plus the dates of cliffs. Express each as a % of circulating supply and as days of average volume.
- **Recipients**: who receives those tokens (team, investors, ecosystem fund, community)? Investor unlocks at a cost basis far below the current price are the classic source of sell pressure.
- **Allocation table**: team %, investors % (and their entry valuations if disclosed), treasury/foundation %, community/airdrop %, ecosystem %.
- **Emissions and inflation**: annual new supply %, where it goes (staking rewards, liquidity mining, validators) and whether the schedule is fixed or set by governance.
- **Net issuance**: emissions minus burns and buybacks. Positive net issuance must be absorbed by real demand.
- **Staking**: % of supply staked, nominal yield, and **real yield** (yield paid from fees/revenue, not from inflation). Staking yield funded by inflation is dilution with extra steps.
- **Treasury**: size, composition (native token vs. stablecoins/ETH/BTC) and runway at the current burn rate. A treasury mostly held in the native token is weaker than it looks.
- **Holder concentration**: share held by the top 10 and top 100 wallets, **excluding** exchange, bridge and staking contracts, which you should identify and label. Look for recent large movements from team or investor wallets to exchanges.

## 3. Token utility and value accrual

This is where many theses fall apart. Answer concretely:

- What is the token *required* for? Gas, collateral, staking for security, access, governance, or fee discounts?
- How do protocol revenues reach token holders?
  - Direct fee share or staking distribution
  - Buyback-and-burn or buyback-and-distribute (how large compared with revenue, and how steady?)
  - Burn from usage (e.g. EIP-1559-style base-fee burns)
  - None: governance only
- Is the mechanism **live**, **approved but not live**, or only **proposed**? A fee switch that hasn't been turned on is a catalyst, not current value capture.
- Structural leakage: does a labs company or foundation with equity holders capture the economics instead of the token (front-end fees, off-chain revenue, sequencer profit)?
- Any legal or regulatory reason the value-accrual mechanism might be switched off or blocked?

## 4. Protocol fundamentals

Define terms before comparing, because aggregators differ:
- **Fees**: everything users pay
- **Revenue**: the share the protocol keeps (fees minus the share paid to LPs, suppliers or other supply-side participants)
- **Holders revenue / earnings**: the share that reaches token holders, or revenue minus token incentives

Collect over the last 30 and 90 days, with the trend (month over month, quarter over quarter, year over year):
- Fees, revenue, holders revenue
- Token incentives paid out (USD) → **earnings = revenue − incentives**
- TVL or AUM (split by source; beware double-counting through re-staked or looped assets)
- Transaction count and transaction or trading volume
- Active users or addresses (daily, weekly or monthly as relevant) and fee per user
- Market share within the category, and its trend over 3–12 months
- Developer activity: full-time developers, commits, contributors (Electric Capital-style counts). Use it as a slow-moving signal only, because commit counts are easy to game.
- Stablecoin supply on the chain or protocol and its trend (for L1s, L2s and DeFi venues)

## 5. Organic vs. incentive-driven growth

Growth that disappears when the rewards stop is not a fundamental trend. Test for it:

- **Earnings test**: is revenue minus incentives positive? Is it improving?
- **Retention**: share of this period's active users who were also active 30 or 90 days ago; returning vs. new users over time
- **Farming signals**: points programs, pending airdrops, sybil clusters (many fresh wallets doing identical, minimal actions)
- **Wash trading**: volume far above what the liquidity or user count can explain, self-matching, fee-free venues with huge volume
- **Mercenary TVL**: TVL concentrated in a few wallets, or TVL that tracks the emissions rate
- **Post-incentive behavior**: what happened after past incentive programs ended?
- **Unit economics**: revenue per user, and cost to acquire that user through incentives

Conclude with one of: *mostly organic*, *mixed*, or *mostly incentive-driven*, and give the evidence.

## 6. On-chain evidence

Use these where reliable data exists. Note the data provider and whether addresses are labeled.

- Active addresses: new vs. returning, trend
- DEX volume on or of the asset
- Bridge flows into and out of the chain or ecosystem
- Exchange inflows (possible selling) and outflows (possible accumulation or self-custody)
- Whale behavior: are large holders accumulating or distributing, and are known fund, team or market-maker wallets moving?
- Smart-money activity: labeled funds and historically profitable wallets (useful but noisy; don't overweight it)
- Staking flows: net staking vs. unstaking, and the length of the unbonding queue
- Stablecoin flows into the ecosystem
- Network fees and blockspace demand

**Look for divergence**: fundamentals and on-chain activity trending up while the price or the valuation multiple trends down is the core of a mispricing thesis. The reverse (price up while usage is flat or down) is a warning.

## 7. Ecosystem, partnerships and adoption

- Ecosystem growth: number and quality of apps or integrations, TVL or users of the top ecosystem projects
- Partnerships and integrations: **verify from the partner's side** (their docs, announcement or code). Many "partnerships" are marketing or small pilots.
- Institutional adoption: ETPs, custody support, regulated entities building on it, tokenized funds, enterprise usage. Separate real volume from announcements.
- Distribution: wallet or exchange integrations, fiat on-ramps, listings on major venues

## 8. Security, centralization and regulatory posture

- Audits (by whom, when, and the scope compared with the code that is live now), bug bounty size, exploit history and how each exploit was handled
- Upgradeability: who controls admin keys and multisigs (signer count and identity), timelocks
- For L2s: stage of decentralization, fraud or validity proofs live?, sequencer control, escape hatches (L2BEAT is the standard reference)
- Oracle and bridge dependencies
- Validator or node concentration
- Regulatory exposure: jurisdiction of the team or foundation, securities-law risk around the token, open enforcement actions, sanctions or KYC issues

## 9. Summary questions

Answer these in writing for each candidate before moving on:
1. Is the growth organic? (mostly / mixed / mostly incentive-driven)
2. Does protocol success create economic value for *the token*? Through what mechanism, and is it live?
3. How much supply overhang is there over the next 12 months relative to demand?
4. What is the cleanest metric showing a gap between price and fundamentals?
5. What single data point, if it changed, would break the thesis?
