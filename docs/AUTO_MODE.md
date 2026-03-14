# Auto Mode

`auto_live` runs completely on the local Termux backend.

## Requirements

- `HMS_SOL_ADDRESS` configured
- `HMS_SOL_PRIVATE_KEY` configured
- wallet funded with a small amount of SOL

## Safety Controls

- single active position
- repeated `429` pauses auto
- circuit breaker blocks new orders on unstable network
- daily loss limit pauses auto
- consecutive losing snapshots pause auto
- duplicate active orders are blocked in SQLite

## UI Signals

The `Auto` page must show:

- running state
- pause reason
- breaker state via recent API events
- latest order and tx
- latest PnL snapshot

