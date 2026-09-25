# Report Template

Use this structure for market scans. For due-diligence requests on named assets, keep the same finalist sections with a shorter market overview. Replace every `<placeholder>`. Where data is missing, write "n/a (not disclosed)" rather than a guess.

---

```markdown
# Crypto Opportunity Scan — <YYYY-MM-DD>

*Data as of <YYYY-MM-DD HH:MM UTC>. Research, not financial advice.*

## 1. Market overview

**Regime:** <risk-on / risk-off / range-bound; BTC-led vs. alt rotation> — <1–2 sentences of evidence>

- **BTC / ETH:** <trend, key levels vs. moving averages, dominance, ETH/BTC>
- **Liquidity:** <stablecoin supply trend, ETF flows, exchange flows>
- **Positioning:** <funding, open interest, leverage: crowded or clean?>
- **Macro & regulation:** <what matters right now>
- **Dominant narratives:** <gaining momentum> / <losing momentum>
- **Crowded trades:** <sectors or assets, and why>
- **Overlooked sectors:** <sectors, and the evidence fundamentals are improving>

## 2. Finalists at a glance

| Asset | Ticker | Price | Market Cap | FDV | Sector | Core Thesis | Key Catalyst | Biggest Risk | Key Metric | Research Score |
|---|---|---|---|---|---|---|---|---|---|---|
| <name> | <TICKER> | $<x> | $<x> | $<x> | <sector> | <≤12 words> | <event, date> | <≤10 words> | <metric: value> | <n>/10 |

Ranked by Research Score. <N> candidates screened → <M> deep-dived → <K> finalists.

## 3. Finalist deep dives

### <Rank>. <Asset> (<TICKER>) — Research Score <n>/10

**Core thesis.** <2–3 sentences: what is mispriced and why it should re-rate>

**Why the market may be mispricing it.** <e.g. stigma from a past event, misunderstood tokenomics change, low coverage, sector-wide selling, category misclassification>

**Fundamental evidence.**
- <metric, value, trend, source, date>
- Organic vs. incentive-driven: <mostly organic / mixed / mostly incentive-driven> — <evidence>

**On-chain evidence.**
- <active users new vs. returning, flows, whale/smart-money behavior, with source and date>

**Tokenomics.**
- Circulating / max supply: <x / y> (float <z%>)
- Unlocks in the next 12 months: <amount, % of circulating supply, key dates, recipients>
- Emissions / net inflation: <x%>
- Value accrual: <mechanism, and whether it is live, approved or proposed>
- Insider/investor allocation and concentration: <details>

**Relative valuation.**

| Metric | <Asset> | <Peer 1> | <Peer 2> | Peer median |
|---|---|---|---|---|
| <MC / annualized revenue> | | | | |
| <second relevant metric> | | | | |

<1–2 sentences: why these metrics fit this business model, and what the gap implies>

**Competitive positioning.** <main competitors; the advantage and whether it can be defended; where competitors are stronger; what would cause share loss>

**Catalysts.**

| Window | Catalyst | Date | Status | Priced in? |
|---|---|---|---|---|
| Next 30 days | <event> | <date> | Confirmed / Speculative | <view> |
| 1–3 months | | | | |
| 3–6 months | | | | |
| 6–12 months | | | | |
| Negative | <unlock / competitor launch / regulatory deadline> | | | |

**Scenarios.** *(Est. The valuations are illustrative and follow from the stated assumptions.)*

| Case | What has to happen | Illustrative valuation | Rough odds (subjective) |
|---|---|---|---|
| Bull | | | |
| Base | | | |
| Bear | | | |

**Thesis invalidation conditions.**
- <specific trigger anyone can observe, with a threshold and a timeframe>
- <...>

**Key metrics to monitor.** <metric: current value → level that matters>

**Research Score breakdown.** Traction <n> · Value accrual <n> · Valuation <n> · Catalysts <n> · Moat <n> · Evidence <n> → <weighted>/10 <(capped: reason)>

**Sources.** 
- <title>, <URL>, accessed <date>

## 4. Eliminated candidates

| Asset | Reason cut |
|---|---|
| <name> | <one line> |

## 5. WHAT COULD I BE WRONG ABOUT?

- **Weakest assumptions:** <per finalist, or across the list>
- **Contradicting evidence:** <data that cuts against the theses>
- **Already priced in?** <which catalysts the market likely already expects>
- **Dilution risk:** <where insiders or unlocks could overwhelm demand>
- **Regime risk:** <how a change in the market backdrop would hit these names>
- **What would change my mind:** <specific evidence>
```

---

## Research Score rubric (1–10)

Score each dimension from 1 to 10, take the weighted average, then apply any caps. Show the breakdown in each finalist section so the user can reweight it if they disagree.

| Dimension | Weight | 9–10 looks like | 1–3 looks like |
|---|---|---|---|
| **Organic traction** | 25% | Revenue and users growing, positive earnings, strong retention | Flat or declining usage, or growth mostly paid for by incentives |
| **Value accrual & tokenomics** | 20% | Live mechanism passing meaningful revenue to holders; low overhang | Governance-only; heavy unlocks; high net inflation |
| **Relative valuation** | 20% | Clearly cheap against real peers on the right metric, with improving fundamentals | Premium to peers with no growth to justify it |
| **Catalysts** | 15% | Confirmed, dated, material and not obviously priced in | Only speculative or already widely expected |
| **Competitive position** | 10% | Defensible advantage and share rising | Commoditized, losing share to stronger rivals |
| **Evidence quality** | 10% | Key metrics verifiable on-chain and cross-checked | Mostly self-reported, missing or contradictory |

**Caps** (apply the lowest one that applies):
- Active regulatory enforcement against the project, or an unresolved exploit or solvency issue → max 5
- More than 25% of circulating supply unlocking within 6 months with no credible absorption → max 6
- Critical contracts upgradeable by a small multisig with no timelock → max 7
- Key thesis metric not independently verifiable → max 7

**Reading the score**
- **8–10**: strong, well-evidenced asymmetric case
- **6–7**: promising, but with material gaps or risks. Name them.
- **Below 6**: normally not a finalist. Include one only in due-diligence mode, when the user asked about it by name.
