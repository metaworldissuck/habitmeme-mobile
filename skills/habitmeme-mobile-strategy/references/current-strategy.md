# Current Strategy

## Trading Modes

### `paper`

- Uses real discovery, analysis, and quote data.
- Simulates fills locally.
- Writes trades, positions, and PnL into the local ledger as paper data.

### `semi_auto_live`

- User chooses the token.
- Backend prepares the order.
- User signs or confirms externally.
- Backend tracks order status and updates positions / PnL.
- It is execution-assisted, not a fully automatic strategy loop.

### `auto_live`

- Runs continuously while the local backend process is alive.
- Uses official ranking sources as the top of the funnel.
- Discovers, filters, enters, monitors, and exits automatically.
- Only manual `Stop Auto` should fully stop auto runtime.
- Guards, cooldowns, and rate limits may block new buys, but should not be described as a user stop.

## Candidate Discovery

Current discovery is intentionally rate-limit-aware:

1. Use official rankings as the initial funnel.
2. Coarse filter with `rankings + token-info`.
3. Deep-check only the strongest candidate with:
   - `security`
   - `liquidity`
   - `tx-info`

This is the current behavior and should be described that way instead of calling it a generic full-market scan.

## Candidate Selection Logic

The project currently emphasizes:

- official source strength
- rank position
- liquidity quality
- holder and concentration quality
- flow quality from transaction info
- hard security blocking before entry

`riskMode` changes real thresholds. It is not display-only.

## Position Model

- Current authoritative history is based on `position_records`.
- Open positions and history are separate concepts.
- Repeated trades on the same token should not be described as a single merged position.

## Exit Logic

Auto exit behavior includes:

- hard stop loss
- recover-cost-basis tranche
- half take-profit tranche
- moonbag reduction
- time exit

When describing the strategy, talk about staged exits and rule-based discipline, not discretionary trading.

## Portfolio and Budget Rules

- Maximum concurrent positions: `2`
- Reserve-aware budget allocation is active
- If slots are full, auto waits
- If buy guards are active, auto waits instead of claiming success

## Failure and Recovery Behavior

Current strategy includes runtime protections:

- stale-order recovery
- prepared-order resubmission where supported
- rate-limit cooldown
- breaker protections
- quote throttling

These are part of the project skill because they directly affect whether the strategy can keep operating on mobile.
