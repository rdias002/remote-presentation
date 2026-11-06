"""Runtime configuration, driven by environment variables.

Env vars (all optional):
  REMPREZ_PORT   TCP port to serve on                 (default 8765)
  REMPREZ_TOKEN  fixed pairing token                  (default: random per run)
  REMPREZ_MOCK   "1" to force the mock controller      (default: auto)
  REMPREZ_THUMB_WIDTH  thumbnail width in px           (default 480)
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no")


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = field(default_factory=lambda: int(os.environ.get("REMPREZ_PORT", "8765")))
    token: str = field(
        default_factory=lambda: os.environ.get("REMPREZ_TOKEN") or secrets.token_urlsafe(9)
    )
    thumb_width: int = field(
        default_factory=lambda: int(os.environ.get("REMPREZ_THUMB_WIDTH", "1280"))
    )
    force_mock: bool = field(default_factory=lambda: _truthy(os.environ.get("REMPREZ_MOCK")))

    @property
    def use_powerpoint(self) -> bool:
        """Real COM backend only on Windows and only when mock isn't forced."""
        return sys.platform == "win32" and not self.force_mock
