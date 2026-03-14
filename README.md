# HabitMeme Mobile

Final submission implementation for the Android-local auto trading agent.

## Structure

- `backend/`: FastAPI local backend for Termux
- `web/`: folded into `backend/templates` and `backend/static`
- `android-shell/`: Kotlin WebView wrapper
- `docs/`: Android, Termux, demo, and submission docs
- `tests/`: smoke and unit-oriented checks

## Local Run

```bash
cd habitmeme-mobile
cp .env.example .env
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

If you are running inside Android Termux and hit the native Python 3.13 / `pydantic-core` build issue, use the Ubuntu `proot-distro` workaround documented in [docs/TERMUX_SETUP.md](/Users/imtoken-ljm/Desktop/test1/AI_PRJ/hackathon/habitmeme-mobile/docs/TERMUX_SETUP.md). The working startup command there becomes:

```bash
cd /path/to/habitmeme-mobile
. .venv312/bin/activate
python -V
uv sync --python .venv312/bin/python
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

On Android, you can reduce the chance of Termux being suspended during a longer run by enabling:

```bash
termux-wake-lock
```

Use it before starting the backend. This helps keep the device awake, but it does **not** guarantee OS-level background persistence.

## Notes

- The original `bitget-wallet-skill` directory is intentionally preserved as-is.
- The new implementation lives only inside this directory.
- For local verification in this environment, prefer `uv run python -m ...` so Python 3.11 is used.
