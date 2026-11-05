"""Typed state shared over REST + WebSocket.

These models are the contract between the host agent and the PWA. Any
PresentationController implementation (PowerPoint, mock, future reveal.js /
Keynote) produces a PresentationState; the PWA only ever sees this shape.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class SlideMeta(BaseModel):
    """One entry in the slide grid."""

    index: int  # 1-based position in the deck
    title: str
    thumb_url: str
    hidden: bool = False


class PresentationState(BaseModel):
    """Full snapshot pushed to every connected controller."""

    has_presentation: bool = False  # a deck is open
    is_running: bool = False  # slideshow is live
    index: int = 0  # current 1-based position (0 when nothing is live)
    total: int = 0
    title: str = ""  # deck name / filename
    slides: List[SlideMeta] = []
    current_notes: str = ""
    current_thumb_url: Optional[str] = None
    next_thumb_url: Optional[str] = None
    message: str = ""  # human-readable status, e.g. "PowerPoint not running"
