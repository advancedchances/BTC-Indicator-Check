# Data Sources

Where to get each metric, and how far to trust it. Prefer the highest tier available, and cross-check anything important against a second source. Tool availability varies by session. Use whatever is connected (web search and fetch, exchange or market-data tools, explorers) and say when a source couldn't be reached.

## Trust tiers

| Tier | What | Use for |
|---|---|---|
| **1 — Primary** | Blockchain data and explorers, protocol dashboards, official docs and tokenomics pages, governance proposals and votes, regulatory filings, direct project announcements, audit reports | Facts: supply, unlocks, mechanisms, dates, votes, filings |
| **2 — Reputable aggregators** | DefiLlama, Token Terminal, Artemis, CoinGecko, CoinMarketCap, Tokenomist, L2BEAT, Dune dashboards (check the query author and logic), Glassnode, CryptoQuant, Nansen, Arkham | Comparable metrics across protocols; cross-checking Tier 1 |
| **3 — Research and media** | Exchange and fund research, Messari, The Block, Blockworks, CoinDesk, established analysts' reports | Context, and leads to verify at Tier 1 or 2 |
| **4 — Social** | X/Twitter, Telegram, Discord, KOLs, sentiment and mindshare tools (e.g. LunarCrush, Kaito), Google Trends | Measuring attention and crowding only. Never proof of value. |

A project's own claims (a blog or a tweet) are primary for *what the project says* but not for independently verified performance. Confirm usage claims with on-chain data or an aggregator.

## By data type

**Prices, market cap, FDV, supply**
- CoinGecko, CoinMarketCap (spot aggregates); exchange APIs such as Coinbase or Kraken for venue prices
- The project's docs or tokenomics page and the token contract on an explorer for supply; reconcile these with the aggregator numbers

**Fees, revenue, incentives, TVL**
- DefiLlama (fees, revenue, holders revenue, incentives, TVL, per-chain breakdowns)
- Token Terminal (standardized fees, revenue, earnings, active users)
- Artemis (chain and app fundamentals, stablecoins, developer activity)
- Protocol-run dashboards and Dune dashboards for protocol-specific detail

**Token unlocks and allocations**
- Tokenomist (formerly Token Unlocks), CryptoRank, DefiLlama unlocks
- Primary: the project's tokenomics docs, vesting contracts on-chain, investor announcements (for rounds and valuations)

**On-chain flows and holders**
- Block explorers (Etherscan, Solscan, Arbiscan and so on) for holder lists and contract labels
- Nansen and Arkham (labeled wallets, smart money, fund and team wallet movements)
- Glassnode and CryptoQuant (exchange flows, BTC/ETH holder cohorts, MVRV, realized cap)
- Dune and Allium (custom queries: new vs. returning users, retention, sybil patterns)

**Derivatives and positioning**
- Coinglass (funding, open interest, liquidations, long/short ratios), Velo, Laevitas; exchange data pages

**Stablecoin liquidity**
- DefiLlama stablecoins (supply by chain and by issuer), Artemis
- Issuer transparency pages and attestations (e.g. Circle, Tether)

**Institutional flows**
- Spot ETF flows: Farside Investors, SoSoValue, issuer websites
- SEC EDGAR (S-1, 8-K, 10-Q/10-K and 13F filings) for corporate treasuries and fund holdings
- Official announcements from banks, asset managers and tokenization platforms

**L2 and bridge risk**
- L2BEAT (stages, proof systems, sequencer and upgrade risks, TVS)
- Bridge dashboards on DefiLlama and Artemis

**Developer activity**
- Electric Capital developer report, GitHub (commits, contributors), Artemis developer data. This is a slow signal and easy to game, so weight it lightly.

**Governance and catalysts**
- Snapshot, Tally, on-chain governance portals, official forums (proposals, vote dates, results)
- Official blogs, docs changelogs, release notes and audit announcements for upgrade timelines

**Security**
- Audit reports (read the scope and the date), bug bounty pages (Immunefi), DefiLlama hacks database, Rekt News

**Macro and regulation**
- Central bank statements and calendars (FOMC), CME FedWatch, the dollar index and Treasury yields
- Regulators' official releases: SEC, CFTC, US Treasury/OFAC, ESMA and national regulators under MiCA, and others by jurisdiction

**Retail attention (Tier 4)**
- App-store rankings of major exchange apps, Google Trends, social mindshare tools. Use these to judge whether a trade is crowded or overlooked, never to judge value.

## Recording sources

For each important figure, record the value, the source, the URL and the as-of date. When two sources disagree by more than about 10%, report both and explain the difference, which is usually a definition (fees vs. revenue), timing, or coverage (which chains or deployments are included).
