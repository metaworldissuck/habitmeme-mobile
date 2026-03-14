# Run On Android

## Main Path

1. Install `Termux` inside the Android emulator.
2. Copy or clone this repo into the emulator.
3. If native Termux `python3` is 3.13 and dependency install fails on `pydantic-core`, switch to the Ubuntu `proot-distro` flow from [TERMUX_SETUP.md](/Users/imtoken-ljm/Desktop/test1/AI_PRJ/hackathon/habitmeme-mobile/docs/TERMUX_SETUP.md).
4. Start the backend from the working Python environment:
   ```bash
   cd ~/storage/shared/hackathon/habitmeme-mobile
   cp .env.example .env
   termux-wake-lock
   . .venv312/bin/activate
   uv sync --python .venv312/bin/python
   python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787
   ```
5. Install the Android shell APK and open it.
6. The shell checks `http://127.0.0.1:8787/health`, then loads `/app`.

`termux-wake-lock` helps reduce sleep-related interruption, but it does not make the backend a guaranteed always-on Android background service.

## Dev Fallback Path

1. On macOS:
   ```bash
   cd /Users/imtoken-ljm/Desktop/test1/AI_PRJ/hackathon/habitmeme-mobile
   uv run uvicorn backend.main:app --host 0.0.0.0 --port 8787
   ```
2. In the Android emulator shell app, tap `Use Dev Host (10.0.2.2)`.
3. The WebView will load `http://10.0.2.2:8787/app`.
