"""Abstract presentation controller.

This is the seam that keeps the host agent app-agnostic. The PowerPoint COM
implementation, the dev-time mock, and any future adapter (reveal.js, Keynote)
all implement this same async interface, so the FastAPI layer and the PWA never
change when a new backend is added.

All methods are async. Implementations that talk to a single-threaded-apartment
COM object marshal onto their own worker thread internally; callers just await.
"""

from __future__ import annotations

import abc
from typing import Callable, Optional

from .models import PresentationState

# Called by a controller (possibly from a non-async worker thread) whenever the
# live state changes — e.g. the presenter advanced a slide at the laptop.
StateListener = Callable[[PresentationState], None]


class PresentationController(abc.ABC):
    def __init__(self) -> None:
        self._on_change: Optional[StateListener] = None

    def set_on_change(self, callback: Optional[StateListener]) -> None:
        """Register the sink that broadcasts state to connected clients."""
        self._on_change = callback

    def _emit(self, state: PresentationState) -> None:
        """Notify the registered listener (safe no-op if none)."""
        callback = self._on_change
        if callback is not None:
            callback(state)

    @abc.abstractmethod
    async def start(self) -> None:
        """Acquire resources (attach to the app, start the worker/poller)."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Release resources and stop background work."""

    @abc.abstractmethod
    async def get_state(self) -> PresentationState:
        """Return the current cached state without forcing a re-read."""

    @abc.abstractmethod
    async def refresh(self) -> PresentationState:
        """Re-read everything from the backing app and return fresh state."""

    @abc.abstractmethod
    async def go_next(self) -> PresentationState:
        """Advance one step (slide or build)."""

    @abc.abstractmethod
    async def go_previous(self) -> PresentationState:
        """Step back one step."""

    @abc.abstractmethod
    async def goto(self, index: int) -> PresentationState:
        """Jump to a 1-based slide index."""
