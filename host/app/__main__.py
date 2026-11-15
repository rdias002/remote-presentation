"""Entry point: python -m app"""

from __future__ import annotations

import sys

import uvicorn


def _utf8_stdio() -> None:
    """The QR banner uses Unicode block characters; on Windows, redirected or
    legacy-codepage stdout (cp1252) would crash startup with UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _utf8_stdio()
    # Import the app object directly (not by "app.main:app" string) so that
    # frozen builds (PyInstaller) can see and bundle it.
    from .main import app, cfg

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
