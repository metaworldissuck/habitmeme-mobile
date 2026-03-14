# Termux Setup

## Recommended Path

Use this project directly in Termux only if your Python environment can install `pydantic-core`.

In practice, recent Termux often ships native `python3.13`, and this project may fail to install dependencies there because `pydantic-core` needs a compatible wheel/build environment. A reliable workaround is:

1. Keep Termux as the host shell.
2. Install `proot-distro`.
3. Run the project inside Ubuntu.
4. Create a dedicated Python 3.12 virtualenv there.

## Termux Host Packages

```bash
pkg update
pkg install git python uv proot-distro
```

## Environment

Create `.env` in the `habitmeme-mobile` directory:

```bash
HMS_SOL_ADDRESS=<sol_address>
HMS_SOL_PRIVATE_KEY=<sol_private_key>
HMS_DEFAULT_BUDGET_SOL=0.02
HMS_MODE_DEFAULT=paper
HMS_API_TOKEN=local-dev-token
```

Optional overrides:

```bash
BGW_API_KEY=<your_key>
BGW_API_SECRET=<your_secret>
```

## Termux Native Python 3.13 Issue

On recent Termux builds, `pkg install python3` may install `python3.13`. In that case `uv sync` can fail while building `pydantic-core`, for example with errors around:

- `ANDROID_API_LEVEL`
- incompatible built wheel tags for `android_*_arm64_v8a`

This is an environment/toolchain issue, not an application-code issue.

## Working Ubuntu `proot-distro` Flow

Install and enter Ubuntu:

```bash
proot-distro install ubuntu
proot-distro login ubuntu
```

Inside Ubuntu, create or use Python 3.12 and a dedicated venv. If your Ubuntu image already has access to Python 3.12, a working flow is:

```bash
cd /path/to/habitmeme-mobile
python3.12 -m venv .venv312
. .venv312/bin/activate
python -V
uv sync --python .venv312/bin/python
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

If you already activated `.venv312`, the startup command is:

```bash
cd /path/to/habitmeme-mobile
. .venv312/bin/activate
python -V
uv sync --python .venv312/bin/python
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787
```

## Reduce Background Suspension

Before starting the backend from Termux, you can enable:

```bash
termux-wake-lock
```

This helps reduce device sleep and can improve longer foreground sessions. It is still **not** a full Android background-service guarantee.

## Notes

- Prefer the dedicated `.venv312` instead of reusing a Termux-native `.venv`.
- If `python3` inside Ubuntu still resolves to the Termux host interpreter, call the venv explicitly:

```bash
/path/to/habitmeme-mobile/.venv312/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787
```
