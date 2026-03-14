from __future__ import annotations

import logging

import uvicorn

from .config import load_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    settings = load_settings()
    uvicorn.run("backend.main:create_app", host=settings.host, port=settings.port, factory=True, reload=True)


if __name__ == "__main__":
    main()
