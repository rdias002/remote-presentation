"""In-memory mock controller for development without PowerPoint.

Lets the FastAPI server + PWA run and be verified on any OS (incl. macOS).
Builds a fake deck, renders labelled placeholder thumbnails, and tracks a
current position that next/prev/goto move through. Emits state changes so the
WebSocket broadcast path can be exercised too.
"""

from __future__ import annotations

from pathlib import Path

from .controller import PresentationController
from .models import PresentationState, SlideMeta
from . import thumbnails

_SAMPLE = [
    ("Welcome", "Thanks everyone for coming. Wait for the room to settle before starting."),
    ("The Problem", "Set up the pain point. Ask: 'who here has hit this?' — show of hands."),
    ("Our Approach", "Three pillars. Don't read the slide; tell the story behind pillar two."),
    ("Live Demo", "Switch to the terminal. If the wifi is flaky, fall back to the recording."),
    ("Results", "Numbers are YoY. The 38% figure is the one to land — pause after it."),
    ("What's Next", "Roadmap is directional, not a commitment. Invite questions here."),
    ("Thank You", "Contact details on screen. Hang around for the hallway track."),
]


class MockController(PresentationController):
    def __init__(self, cache_dir: Path, thumb_width: int = 480) -> None:
        super().__init__()
        self._cache_dir = cache_dir
        self._thumb_width = thumb_width
        self._index = 1
        self._running = True
        self._titles = [t for t, _ in _SAMPLE]
        self._notes = [n for _, n in _SAMPLE]

    @property
    def _total(self) -> int:
        return len(self._titles)

    async def start(self) -> None:
        self._render_thumbs()

    async def stop(self) -> None:
        pass

    def _render_thumbs(self) -> None:
        for i in range(1, self._total + 1):
            thumbnails.make_placeholder_png(
                self._cache_dir / thumbnails.thumb_filename(i),
                i,
                self._total,
                self._titles[i - 1],
                self._thumb_width,
            )

    def _build_state(self) -> PresentationState:
        slides = [
            SlideMeta(index=i, title=self._titles[i - 1], thumb_url=thumbnails.thumb_url(i))
            for i in range(1, self._total + 1)
        ]
        has_next = self._index < self._total
        return PresentationState(
            has_presentation=True,
            is_running=self._running,
            index=self._index,
            total=self._total,
            title="Sample Deck (mock)",
            slides=slides,
            current_notes=self._notes[self._index - 1],
            current_thumb_url=thumbnails.thumb_url(self._index),
            next_thumb_url=thumbnails.thumb_url(self._index + 1) if has_next else None,
            message="Mock controller — PowerPoint not required",
        )

    async def get_state(self) -> PresentationState:
        return self._build_state()

    async def refresh(self) -> PresentationState:
        return self._build_state()

    async def go_next(self) -> PresentationState:
        if self._index < self._total:
            self._index += 1
        return self._changed()

    async def go_previous(self) -> PresentationState:
        if self._index > 1:
            self._index -= 1
        return self._changed()

    async def goto(self, index: int) -> PresentationState:
        self._index = max(1, min(self._total, index))
        return self._changed()

    def _changed(self) -> PresentationState:
        # Commanded moves are broadcast by the API layer (_apply). on_change is
        # reserved for autonomous changes (e.g. the presenter advancing at the
        # laptop), which the PowerPoint poller emits; the mock has none.
        return self._build_state()
