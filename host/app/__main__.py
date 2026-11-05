"""Entry point: python -m app"""

from __future__ import annotations

import uvicorn

from .config import Config


def main() -> None:
    cfg = Config()
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
