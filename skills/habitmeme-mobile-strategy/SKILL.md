---
name: habitmeme-mobile-strategy
description: Use this skill when working on the current HabitMeme Mobile project and you need the real, already-implemented trading strategy, runtime boundaries, and product semantics. Covers paper, semi-auto, and auto modes; current candidate selection and exits; position/PnL accounting expectations; rate-limit handling; and Android-local runtime constraints. This is for the current project state, not a future redesign.
---

# HabitMeme Mobile Strategy

Use this skill when the task is about the current `habitmeme-mobile` strategy or product behavior.

## Read This First

- For the implemented trading modes and strategy rules, read [references/current-strategy.md](references/current-strategy.md).
- For runtime and deployment boundaries on Android / Termux / Ubuntu proot, read [references/runtime-boundaries.md](references/runtime-boundaries.md).

## What This Skill Represents

This skill describes the project's own Agent skill, not just the upstream Bitget Wallet capability surface.

Treat the official Bitget Wallet API surface as the foundation:

- rankings
- token-info
- security
- liquidity
- tx-info
- order-quote / order-create / order-submit / order-status

Treat HabitMeme Mobile as the project-native Agent skill layered on top of that foundation:

- official-ranking-based discovery
- two-stage candidate filtering
- `riskMode`-driven strategy profiles
- up to 2 concurrent positions
- reserve-aware budget allocation
- staged exits and time exits
- stale-order recovery and resubmission
- local position history and PnL tracking
- Android-local operation and control-surface behavior

## Hard Constraints

- Stay within the current project architecture and behavior unless the task explicitly asks for a redesign.
- Do not reintroduce `vibe` or thesis fields.
- Do not assume external alpha feeds, Telegram scraping, CT scraping, Nansen, Arkham, or extra backend services.
- Keep the strategy scoped to Solana meme trading.
- Respect that the current mobile runtime is Android-local and backend-dependent, not a guaranteed OS-level always-on service.

## Use This Skill To Keep Explanations Accurate

When describing the project, do not say it "only uses the official skill."

Describe it as:

- an official capability foundation from Bitget Wallet
- plus a project-native trading Agent skill implemented in `habitmeme-mobile`

## Main Project Files

- `backend/strategy.py`
- `backend/auto_engine.py`
- `backend/ledger.py`
- `backend/api_routes.py`
- `backend/static/app.js`
- `backend/templates/index.html`

## Validation Checklist

Before describing or changing strategy behavior, sanity-check these:

- Are the mode semantics still correct for `paper`, `semi-auto`, and `auto`?
- Are candidate filtering and exit rules described as they currently work, not as a planned upgrade?
- Are rate-limit / cooldown behaviors described as runtime guards rather than profit logic?
- Are position history and PnL descriptions based on `position_records`, not the old merged-per-token model?
- Are Android / Termux limitations stated clearly?
