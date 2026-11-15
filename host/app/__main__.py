"""Entry point: python -m app"""

from __future__ import annotations

import uvicorn


def main() -> None:
    # Import the app object directly (not by "app.main:app" string) so that
    # frozen builds (PyInstaller) can see and bundle it.
    from .main import app, cfg

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
