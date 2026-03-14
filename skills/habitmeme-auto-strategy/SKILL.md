---
name: habitmeme-auto-v2-strategy
description: Upgrade HabitMeme auto trading strategy when the goal is to improve the existing Bitget-official-API-based Solana meme auto strategy without adding external services or new data providers. Use when designing or revising selection, sizing, exits, or risk rules for the current auto engine. Hard constraints: at most 2 concurrent positions, no Telegram/Nansen/Arkham/extra backend services, and only official Bitget Wallet APIs already available in the project.
---

# HabitMeme Auto V2 Strategy

Use this skill only for the `habitmeme-mobile` auto strategy.

## Hard Constraints

- Maximum concurrent open positions: `2`
- Do not add external services, bots, queues, stream processors, or new backends
- Do not require Telegram, CT scraping, Nansen, Arkham, GMGN, or any non-Bitget data source
- Stay on Solana meme trading only
- Reuse the current project architecture: rankings -> analysis -> quote -> create -> submit -> status -> positions -> pnl

## Official API Surface To Use

Keep strategy upgrades inside the current Bitget Wallet API surface already used by the repo:

- `rankings`
  - `Hotpicks`
  - `topGainers`
- `token-info`
- `security`
- `liquidity`
- `tx-info`
- `order-quote`
- `order-create`
- `order-submit`
- `order-status`

Optional if already exposed through the same official docs and easy to add without a new service:

- `historical-coins`
- `kline`

Do not design around APIs the repo does not already have wrappers for unless the change is very small and still stays inside the same official provider.

## Strategy Objective

Turn the current single-position defensive rotator into a `2-slot`, `official-API-only`, `risk-first but more expressive` meme strategy.

Priority order:

1. Avoid obvious rugs and illiquid traps
2. Improve candidate ranking quality
3. Improve exit quality
4. Improve capital allocation across up to `2` positions

## What To Keep From V1

- Security blocking rules stay first-class
- Liquidity minimums stay enforced
- Automatic exits stay enabled
- PnL and position accounting remain authoritative in SQLite
- No full rewrite of execution flow

## Required V2 Upgrades

### 1. Two-Slot Portfolio

Move from one open position to at most two.

Implementation intent:

- `slot_count_max = 2`
- `slot_budget_sol = total_budget / active_slot_count_cap`
- New buys are blocked when two open positions already exist
- If one slot is free, only one new token may be opened per discovery cycle

Do not implement 5-10 token venture baskets. That exceeds current API/rate-limit and product constraints.

### 2. Candidate Ranking Upgrade

Keep official ranking sources as the top of the funnel, but improve scoring with signals already available:

- Source strength
  - both `Hotpicks` and `topGainers` > single-source listing
- Rank position
  - earlier official ranking positions score higher
- Liquidity quality
  - more liquidity is better up to a cap
- Holder quality
  - higher holders helps, concentration still penalizes
- Flow quality
  - buy pressure > sell pressure
  - buyer count > seller count
  - avoid concentrated single-buyer spikes
- Safety quality
  - warnings penalize score even if not hard-blocked

Do not reintroduce `vibe` or thesis fields.

### 3. Exit Upgrade

Use the existing staged exit framework, but move it closer to the desired meme logic while still remaining practical for small wallets:

- `2x` move: recover cost basis
- `5x` move: sell 50%
- `10x+` move: leave moonbag
- hard stop loss remains enabled
- time exit remains enabled

If current liquidity/volatility makes those thresholds too wide for real fills, use a conservative staged variant but keep the same ladder semantics.

### 4. Risk Mode Must Actually Change Behavior

`riskMode` must not be display-only.

At minimum, bind it to:

- min liquidity threshold
- max slot budget
- stop loss width
- time exit duration
- whether single-source candidates are allowed

Recommended shape:

- `conservative`
  - only dual-source or strongest ranked names
  - higher liquidity threshold
  - smaller slot budget
  - tighter stop loss
- `normal`
  - balanced defaults
- `degen`
  - looser source and liquidity filters
  - still keep hard security blocks

### 5. Capital Preservation

Keep a reserve and apply portfolio-level brakes.

Required:

- reserve SOL floor
- max daily loss
- max consecutive losses
- no new buy while pending order exists
- no new buy if one slot is already occupied and the second candidate is blocked or weak

## Explicitly Out Of Scope

- Social scraping
- Wallet labeling / smart money services
- Kafka consumers or streaming infrastructure
- Off-chain alpha feeds
- Multi-chain support
- Hyperactive high-frequency sniping

## File Targets

When implementing this strategy in the current repo, the main files are:

- `backend/strategy.py`
- `backend/legacy_strategy.py`
- `backend/auto_engine.py`
- `backend/config.py`
- `backend/ledger.py`
- `backend/static/app.js`

## Validation Checklist

- Auto never opens more than `2` positions
- Auto never opens a new trade when an order is still pending
- Risk mode changes actual thresholds and changes which candidates pass
- Candidate ordering is stable and explainable
- Exit ladder updates positions and pnl correctly after each tranche
- No new runtime dependency or external service is introduced
