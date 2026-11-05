"""PowerPoint controller via COM automation (Windows only).

Design notes
------------
PowerPoint's COM object is single-threaded-apartment, so *all* COM access
happens on one dedicated worker thread that owns the Application object. The
async methods just enqueue a callable and await its result via a Future — they
never touch COM directly. This is the #1 correctness issue with pywin32 under
an async server, and keeping every call on one apartment thread avoids it.

Change detection is poll-first (compare CurrentShowPosition on a timer), which
is robust regardless of event-binding quirks across PowerPoint/pywin32 versions.
COM application events are also wired up as a best-effort latency improvement;
if they don't bind, polling still catches every change.

pywin32 is imported lazily inside the worker thread so this module imports
cleanly on non-Windows machines (where only the mock controller is used).
"""

from __future__ import annotations

import asyncio
import concurrent.futures as futures
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .controller import PresentationController
from .models import PresentationState, SlideMeta
from . import thumbnails

# PowerPoint placeholder type for the notes body (ppPlaceholderBody).
_PP_PLACEHOLDER_BODY = 2

# Set by the active controller so COM event callbacks can reach it. Only one
# PowerPoint controller exists per process.
_ACTIVE: "PowerPointController | None" = None


def _make_event_handler():
    """Event sink class for DispatchWithEvents.

    Defines both the plain and ``On``-prefixed spellings of the slide-change
    events so we catch the callback whichever name the installed pywin32 maps
    to. Each just nudges the worker to re-poll immediately.
    """

    def _nudge(*_args):
        if _ACTIVE is not None:
            _ACTIVE._request_poll()

    class _PPTEvents:
        pass

    for name in (
        "SlideShowNextSlide",
        "SlideShowBegin",
        "SlideShowEnd",
        "OnSlideShowNextSlide",
        "OnSlideShowBegin",
        "OnSlideShowEnd",
    ):
        setattr(_PPTEvents, name, lambda self, *a, **k: _nudge())
    return _PPTEvents


class _Command:
    __slots__ = ("fn", "future")

    def __init__(self, fn: Callable[[], PresentationState]) -> None:
        self.fn = fn
        self.future: futures.Future = futures.Future()


class PowerPointController(PresentationController):
    def __init__(self, cache_dir: Path, thumb_width: int = 480, poll_interval: float = 0.25) -> None:
        super().__init__()
        self._cache_dir = cache_dir
        self._thumb_width = thumb_width
        self._poll_interval = poll_interval

        self._cmd_q: "queue.Queue[_Command]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._poll_now = threading.Event()

        self._app = None  # COM Application (lives on worker thread)
        self._state = PresentationState(message="Connecting to PowerPoint…")
        self._thumb_sig: tuple | None = None
        self._last_index = -1

    # ------------------------------------------------------------------ public

    async def start(self) -> None:
        global _ACTIVE
        _ACTIVE = self
        self._thread = threading.Thread(target=self._run, name="ppt-com", daemon=True)
        self._thread.start()
        # Wait (off the event loop) until the worker has attached or failed.
        await asyncio.get_running_loop().run_in_executor(None, self._ready.wait, 15.0)

    async def stop(self) -> None:
        global _ACTIVE
        self._stop.set()
        if self._thread is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._thread.join, 5.0)
        _ACTIVE = None

    async def get_state(self) -> PresentationState:
        return self._state

    async def refresh(self) -> PresentationState:
        return await self._submit(self._read_state)

    async def go_next(self) -> PresentationState:
        return await self._submit(lambda: self._nav("next"))

    async def go_previous(self) -> PresentationState:
        return await self._submit(lambda: self._nav("prev"))

    async def goto(self, index: int) -> PresentationState:
        return await self._submit(lambda: self._nav("goto", index))

    # ----------------------------------------------------------------- worker

    def _request_poll(self) -> None:
        self._poll_now.set()

    def _submit(self, fn: Callable[[], PresentationState]) -> "asyncio.Future":
        cmd = _Command(fn)
        self._cmd_q.put(cmd)
        return asyncio.wrap_future(cmd.future)

    def _run(self) -> None:
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:  # pragma: no cover - non-Windows
            self._state = PresentationState(message=f"pywin32 not available: {exc}")
            self._ready.set()
            return

        pythoncom.CoInitialize()
        try:
            self._connect(win32com, pythoncom)
            self._safe_update(self._read_state)
            self._ready.set()

            last_poll = 0.0
            while not self._stop.is_set():
                try:
                    cmd = self._cmd_q.get(timeout=0.02)
                except queue.Empty:
                    cmd = None
                if cmd is not None:
                    try:
                        cmd.future.set_result(cmd.fn())
                    except Exception as exc:  # surface to the awaiting caller
                        cmd.future.set_exception(exc)

                pythoncom.PumpWaitingMessages()

                now = time.monotonic()
                if self._poll_now.is_set() or (now - last_poll) >= self._poll_interval:
                    self._poll_now.clear()
                    last_poll = now
                    self._poll()
        finally:
            pythoncom.CoUninitialize()

    def _connect(self, win32com, pythoncom) -> None:
        try:
            self._app = win32com.client.DispatchWithEvents(
                "PowerPoint.Application", _make_event_handler()
            )
        except Exception:
            # Events couldn't bind; plain dispatch + polling still works fully.
            self._app = win32com.client.Dispatch("PowerPoint.Application")

    # ------------------------------------------------------------- COM reads

    def _poll(self) -> None:
        """Cheap change check: re-read only when the live position moved."""
        try:
            index = self._current_position()
        except Exception:
            index = 0
        if index != self._last_index:
            self._safe_update(self._read_state, emit=True)

    def _safe_update(self, reader, emit: bool = False) -> None:
        try:
            state = reader()
        except Exception as exc:
            state = PresentationState(message=f"PowerPoint read error: {exc}")
        self._state = state
        self._last_index = state.index
        if emit:
            self._emit(state)

    def _current_position(self) -> int:
        app = self._app
        if app is None or app.SlideShowWindows.Count == 0:
            return 0
        return int(app.SlideShowWindows.Item(1).View.CurrentShowPosition)

    def _show_view(self):
        app = self._app
        if app is None or app.SlideShowWindows.Count == 0:
            return None
        return app.SlideShowWindows.Item(1).View

    def _read_state(self) -> PresentationState:
        app = self._app
        try:
            pres = app.ActivePresentation
        except Exception:
            return PresentationState(
                has_presentation=False, message="Open a presentation in PowerPoint"
            )

        slides = pres.Slides
        total = int(slides.Count)
        self._ensure_thumbs(pres, total)

        metas: list[SlideMeta] = []
        for i in range(1, total + 1):
            s = slides.Item(i)
            metas.append(
                SlideMeta(
                    index=i,
                    title=self._slide_title(s, i),
                    thumb_url=thumbnails.thumb_url(i),
                    hidden=self._is_hidden(s),
                )
            )

        running = False
        index = 0
        try:
            if app.SlideShowWindows.Count > 0:
                index = int(app.SlideShowWindows.Item(1).View.CurrentShowPosition)
                running = True
        except Exception:
            running = False

        notes = self._notes_for(slides, index) if index else ""
        has_next = 0 < index < total
        msg = "" if running else "Start the slideshow (F5) to control it from your phone"

        return PresentationState(
            has_presentation=True,
            is_running=running,
            index=index,
            total=total,
            title=self._safe(lambda: pres.Name, "Presentation"),
            slides=metas,
            current_notes=notes,
            current_thumb_url=thumbnails.thumb_url(index) if index else None,
            next_thumb_url=thumbnails.thumb_url(index + 1) if has_next else None,
            message=msg,
        )

    def _ensure_thumbs(self, pres, total: int) -> None:
        sig = (self._safe(lambda: pres.Name, ""), total)
        if sig == self._thumb_sig:
            return
        width = self._thumb_width
        height = thumbnails.thumb_height(width)
        for i in range(1, total + 1):
            dest = self._cache_dir / thumbnails.thumb_filename(i)
            try:
                pres.Slides.Item(i).Export(str(dest), "PNG", width, height)
            except Exception:
                pass
        self._thumb_sig = sig

    def _nav(self, action: str, index: int = 0) -> PresentationState:
        view = self._show_view()
        if view is not None:
            try:
                if action == "next":
                    view.Next()
                elif action == "prev":
                    view.Previous()
                elif action == "goto":
                    view.GotoSlide(index)
            except Exception:
                pass
        state = self._read_state()
        self._state = state
        self._last_index = state.index
        return state

    # ------------------------------------------------------------- COM helpers

    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    def _slide_title(self, slide, i: int) -> str:
        try:
            if slide.Shapes.HasTitle:
                text = slide.Shapes.Title.TextFrame.TextRange.Text.strip()
                if text:
                    return text
        except Exception:
            pass
        return f"Slide {i}"

    def _is_hidden(self, slide) -> bool:
        try:
            return bool(slide.SlideShowTransition.Hidden)
        except Exception:
            return False

    def _notes_for(self, slides, index: int) -> str:
        try:
            notes_page = slides.Item(index).NotesPage
        except Exception:
            return ""
        for shape in self._iter_shapes(notes_page):
            try:
                if not shape.HasTextFrame:
                    continue
                if shape.PlaceholderFormat.Type != _PP_PLACEHOLDER_BODY:
                    continue
                frame = shape.TextFrame
                if frame.HasText:
                    return frame.TextRange.Text
            except Exception:
                continue
        return ""

    @staticmethod
    def _iter_shapes(notes_page):
        try:
            shapes = notes_page.Shapes
            for i in range(1, int(shapes.Count) + 1):
                yield shapes.Item(i)
        except Exception:
            return
