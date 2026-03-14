# HabitMeme Mobile: A Local Android Auto Trading Agent Built On Bitget Wallet Skill

HabitMeme Mobile packages a Solana meme trading agent into an Android-native workflow:

- the original Bitget Wallet Skill remains the capability layer
- a new FastAPI service runs locally in Termux
- an Android WebView shell turns that local agent into an app-like control surface
- the system supports paper, semi-auto, and full auto live modes
- all trade logs and PnL snapshots are stored locally in SQLite

The differentiator is not a mock UI. The goal is to run a local agent on Android that can survive mobile-network instability, rate limits, and partial failures while still tracking real outcomes.

