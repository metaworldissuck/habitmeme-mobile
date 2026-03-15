# Runtime Boundaries

## Android-Local Shape

The project runs in this form:

- backend in Termux / Ubuntu proot
- frontend as Android WebView shell
- local communication over `127.0.0.1`

## Python Runtime Reality

Recent native Termux Python is often `3.13`, which can create `pydantic-core` installation issues on Android/Termux.

For that reason, the documented reliable path is:

- use Ubuntu `proot-distro`
- create `.venv312`
- run backend from Python `3.12`

## Background Persistence

`termux-wake-lock` can help reduce interruption risk, but it is not the same as a guaranteed Android foreground service.

So the current project should be described as:

- mobile-local
- demo-ready
- suitable for short-to-medium auto sessions

not as a guaranteed 24/7 background daemon.

## Rate-Limit Reality

The strategy uses official Bitget Wallet APIs and can still encounter rate limits.

Current mitigations include:

- reduced discovery fan-out
- short TTL caching on read-heavy token analysis endpoints
- cooldown and breaker handling
- quote throttling

These protections improve survivability, but they do not remove upstream API limits.
