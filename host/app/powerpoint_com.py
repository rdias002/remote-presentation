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
        # Lightweight signal the poller compares to detect a change. It includes
        # the active deck's identity (not just the slide number) so switching to
        # another running slideshow is detected even at the same slide index.
        self._last_poll_sig: tuple | None = None
        # Per-deck thumbnail cache, keyed by full path. Each deck gets its own
        # id so its thumbnails live under a distinct filename namespace
        # (d{id}-slide-N.png) and can never be confused with another deck's —
        # the cause of the grid "mixing up" when two shows run at once.
        self._decks: dict[str, dict] = {}
        self._next_deck_id = 0
        self._cur: dict | None = None  # the tracked deck's cache entry

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
        """Cheap change check: re-read only when the live signature moved.

        The signature is (active deck, slide position), so advancing a slide,
        switching to another running show, or opening a different deck all
        trigger a re-read — the old code compared only the slide number and
        missed a switch between two shows sitting on the same slide index.
        """
        try:
            sig = self._live_signature()
        except Exception:
            sig = None
        if sig != self._last_poll_sig:
            self._last_poll_sig = sig
            self._safe_update(self._read_state, emit=True)

    def _safe_update(self, reader, emit: bool = False) -> None:
        try:
            state = reader()
        except Exception as exc:
            state = PresentationState(message=f"PowerPoint read error: {exc}")
        self._state = state
        if emit:
            self._emit(state)

    def _live_signature(self) -> tuple:
        """(tracked deck identity, current slide position) — cheap to compute.

        Identity comes from the tracked SHOW's deck (not ActivePresentation,
        which PowerPoint does NOT move to a newly started show), so switching to
        another running show is detected and followed.
        """
        app = self._app
        if app is None:
            return (None, 0)
        show = self._active_show_window()
        if show is None:
            # No show running: fall back to the active edit deck so that merely
            # opening/switching a deck still refreshes the grid.
            try:
                return (app.ActivePresentation.FullName, 0)
            except Exception:
                return (None, 0)
        try:
            name = show.Presentation.FullName
        except Exception:
            name = None
        try:
            pos = int(show.View.CurrentShowPosition)
        except Exception:
            pos = 0
        return (name, pos)

    def _active_show_window(self):
        """The slideshow window to control/track when shows are running.

        PowerPoint prepends a newly started show at index 1, so Item(1) is the
        most recently started (current) show — the one the presenter just
        launched and wants the remote to drive. ActivePresentation is NOT
        reliable here: it stays on the previously active deck when a second show
        starts. Returns None when no show is running.
        """
        app = self._app
        if app is None:
            return None
        try:
            if int(app.SlideShowWindows.Count) == 0:
                return None
            return app.SlideShowWindows.Item(1)
        except Exception:
            return None

    def _show_view(self):
        show = self._active_show_window()
        try:
            return show.View if show is not None else None
        except Exception:
            return None

    def _read_state(self) -> PresentationState:
        app = self._app
        # Track the active running show; fall back to the active (edit) deck.
        # Deriving the deck FROM the chosen show keeps slides, notes and the
        # current/next preview all pointing at the same presentation when more
        # than one slideshow is open.
        show = self._active_show_window()
        try:
            pres = show.Presentation if show is not None else app.ActivePresentation
        except Exception:
            return PresentationState(
                has_presentation=False, message="Open a presentation in PowerPoint"
            )

        slides = pres.Slides
        total = int(slides.Count)
        self._ensure_deck_cache(pres, slides, total)
        cur = self._cur or {"id": 0, "ver": 0, "aspect": thumbnails.DEFAULT_ASPECT, "has_notes": False}
        did, ver = cur["id"], cur["ver"]

        metas: list[SlideMeta] = []
        for i in range(1, total + 1):
            s = slides.Item(i)
            metas.append(
                SlideMeta(
                    index=i,
                    title=self._slide_title(s, i),
                    thumb_url=thumbnails.thumb_url(did, i, ver),
                    hidden=self._is_hidden(s),
                )
            )

        running = False
        index = 0
        if show is not None:
            try:
                index = int(show.View.CurrentShowPosition)
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
            current_thumb_url=thumbnails.thumb_url(did, index, ver) if index else None,
            next_thumb_url=thumbnails.thumb_url(did, index + 1, ver) if has_next else None,
            aspect=cur["aspect"],
            has_notes=cur["has_notes"],
            message=msg,
        )

    def _ensure_deck_cache(self, pres, slides, total: int) -> None:
        """Ensure the tracked deck's thumbnails exist in their own filename
        namespace, and record its aspect / notes flag. Keyed by the deck's full
        path so two distinct decks (e.g. two running shows) never share cache
        files — this is what stops the grid mixing decks up. Does real export
        work only the first time a deck is seen (or if its slide count / size
        changed); otherwise it just re-selects the cached entry.
        """
        key = self._safe(lambda: pres.FullName, "") or self._safe(lambda: pres.Name, "")
        width = self._thumb_width
        entry = self._decks.get(key)
        if entry is not None and entry["total"] == total and entry["width"] == width:
            self._cur = entry  # already cached with the same shape
            return

        did = entry["id"] if entry is not None else self._next_deck_id
        if entry is None:
            self._next_deck_id += 1
        ver = (entry["ver"] + 1) if entry is not None else 1

        aspect = self._deck_aspect(pres)
        height = thumbnails.thumb_height(width, aspect)

        # Replace only THIS deck's own files (namespaced); other decks untouched.
        for old in self._cache_dir.glob(f"d{did}-slide-*.png"):
            try:
                old.unlink()
            except Exception:
                pass
        for i in range(1, total + 1):
            dest = self._cache_dir / thumbnails.thumb_filename(did, i)
            try:
                slides.Item(i).Export(str(dest), "PNG", width, height)
            except Exception:
                pass

        entry = {
            "id": did,
            "ver": ver,
            "total": total,
            "width": width,
            "aspect": aspect,
            "has_notes": self._deck_has_notes(slides, total),
        }
        self._decks[key] = entry
        self._cur = entry

    def _deck_aspect(self, pres) -> float:
        try:
            setup = pres.PageSetup
            w = float(setup.SlideWidth)
            h = float(setup.SlideHeight)
            if w > 0 and h > 0:
                return w / h
        except Exception:
            pass
        return thumbnails.DEFAULT_ASPECT

    def _deck_has_notes(self, slides, total: int) -> bool:
        for i in range(1, total + 1):
            if self._notes_for(slides, i).strip():
                return True
        return False

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
