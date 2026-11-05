"""FastAPI host agent: serves the PWA, exposes control REST + WebSocket.

Run with either:
    python -m app                 (reads REMPREZ_* env vars)
    uvicorn app.main:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import net, pairing
from .config import Config
from .controller import PresentationController
from .mock import MockController
from .models import PresentationState

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE.parent.parent / "web"

cfg = Config()
CACHE_DIR = Path(tempfile.mkdtemp(prefix="remprez-thumbs-"))


def _build_controller() -> PresentationController:
    if cfg.use_powerpoint:
        from .powerpoint_com import PowerPointController

        return PowerPointController(CACHE_DIR, cfg.thumb_width)
    return MockController(CACHE_DIR, cfg.thumb_width)


controller = _build_controller()


class Hub:
    """Tracks connected controllers and broadcasts state to all of them."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, state: PresentationState) -> None:
        payload = {"type": "state", "state": state.model_dump()}
        for ws in list(self._clients):
            try:
                await ws.send_json(payload)
            except Exception:
                self._clients.discard(ws)

    def broadcast_threadsafe(self, state: PresentationState) -> None:
        """Called from the controller's worker thread; hops onto the loop."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.broadcast(state), self._loop)


hub = Hub()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.bind_loop(asyncio.get_running_loop())
    controller.set_on_change(hub.broadcast_threadsafe)
    await controller.start()
    ip = net.get_lan_ip()
    print(pairing.banner(ip, cfg.port, cfg.token), flush=True)
    try:
        yield
    finally:
        await controller.stop()
        shutil.rmtree(CACHE_DIR, ignore_errors=True)


app = FastAPI(title="Remote Presentation Host", lifespan=lifespan)


def require_token(t: str = Query(default="")) -> None:
    if t != cfg.token:
        raise HTTPException(status_code=403, detail="Invalid or missing pairing token")


async def _apply(action: str, index: int = 0) -> PresentationState:
    if action == "next":
        state = await controller.go_next()
    elif action == "prev":
        state = await controller.go_previous()
    elif action == "goto":
        state = await controller.goto(index)
    else:
        state = await controller.refresh()
    await hub.broadcast(state)
    return state


# ------------------------------------------------------------------ REST API


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/state", dependencies=[Depends(require_token)])
async def api_state() -> dict:
    return (await controller.get_state()).model_dump()


@app.post("/api/refresh", dependencies=[Depends(require_token)])
async def api_refresh() -> dict:
    return (await _apply("refresh")).model_dump()


@app.post("/api/next", dependencies=[Depends(require_token)])
async def api_next() -> dict:
    return (await _apply("next")).model_dump()


@app.post("/api/prev", dependencies=[Depends(require_token)])
async def api_prev() -> dict:
    return (await _apply("prev")).model_dump()


@app.post("/api/goto/{index}", dependencies=[Depends(require_token)])
async def api_goto(index: int) -> dict:
    return (await _apply("goto", index)).model_dump()


@app.get("/thumbs/{filename}", dependencies=[Depends(require_token)])
async def thumb(filename: str) -> FileResponse:
    # Guard against path traversal; only serve flat PNG files from the cache.
    safe = Path(filename).name
    path = CACHE_DIR / safe
    if path.suffix.lower() != ".png" or not path.is_file():
        raise HTTPException(status_code=404, detail="thumbnail not found")
    return FileResponse(path, media_type="image/png")


# -------------------------------------------------------------------- WebSocket


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, t: str = Query(default="")) -> None:
    if t != cfg.token:
        await ws.close(code=4403)
        return
    await hub.connect(ws)
    try:
        initial = await controller.get_state()
        await ws.send_json({"type": "state", "state": initial.model_dump()})
        while True:
            msg = await ws.receive_json()
            action = str(msg.get("action", ""))
            if action in ("next", "prev", "refresh"):
                await _apply(action)
            elif action == "goto":
                await _apply("goto", int(msg.get("index", 0)))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.disconnect(ws)


# ------------------------------------------------- static PWA (declared last)

if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
