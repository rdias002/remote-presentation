# Remote Presentation

Control a live **PowerPoint** presentation from your Android phone/tablet (or any
browser) over WiFi — navigate slides, jump to any slide from a grid, see a
**next-slide preview**, and read **presenter notes**.

```
 Phone browser PWA  ──HTTP + WebSocket──▶  Host agent on the presenting PC
  prev / next · grid · notes · preview       FastAPI + pywin32 (PowerPoint COM)
```

The **host agent** runs on the computer that's presenting (it's the only thing
that can drive PowerPoint and read notes/thumbnails). The **phone is just a web
app** — no install needed, works on Android, iOS, and tablets.

## Status

- ✅ **Windows + PowerPoint** backend (COM automation via pywin32) — the primary target.
- ✅ **Mock backend** for development on any OS (macOS/Linux), no PowerPoint required.
- 🔜 Web decks (Google Slides / reveal.js) and macOS/Keynote — the controller
  interface (`app/controller.py`) is pluggable so these slot in without a rewrite.

## Run it (Windows, real PowerPoint)

1. Install **Python 3.10+** and **Microsoft PowerPoint** (desktop).
2. Install the host:
   ```powershell
   cd host
   py -m venv .venv
   .venv\Scripts\activate
   pip install -e .
   ```
3. Open your deck in PowerPoint and **start the slideshow (F5)**.
4. Start the host (same machine):
   ```powershell
   python -m app
   ```
   A pairing URL + **QR code** print in the terminal.
5. On your phone (same WiFi), **scan the QR code** (or open the printed URL).
   You can now control the deck. Advancing at the laptop also updates the phone.

> Tip: "Install" / "Add to Home Screen" from your phone browser to run it like a
> native app (fullscreen, keeps the screen awake while presenting).

## Try it without PowerPoint (any OS)

The mock backend serves a fake 7-slide deck so you can develop/preview the UI:

```bash
cd host
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
REMPREZ_MOCK=1 python -m app
```

Open the printed URL in a browser. On macOS/Linux the mock backend is used
automatically (pywin32 is Windows-only).

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `REMPREZ_PORT` | `8765` | Port to serve on |
| `REMPREZ_TOKEN` | random per run | Pairing token. **Set a fixed value** so an installed PWA keeps working across restarts |
| `REMPREZ_MOCK` | auto | `1` forces the mock backend (useful on Windows to test the UI) |
| `REMPREZ_THUMB_WIDTH` | `480` | Thumbnail width in pixels |

## How it works

- `host/app/controller.py` — abstract `PresentationController` interface (the seam
  that keeps everything app-agnostic).
- `host/app/powerpoint_com.py` — PowerPoint via COM. All COM calls run on **one
  dedicated worker thread** (COM is single-apartment); the async API marshals
  commands to it. Slide changes made at the laptop are detected by polling
  `CurrentShowPosition` (plus best-effort COM events) and pushed over WebSocket.
- `host/app/mock.py` — in-memory deck with placeholder thumbnails for dev.
- `host/app/main.py` — FastAPI: serves the PWA, REST (`/api/*`), WebSocket (`/ws`),
  token-gated thumbnails (`/thumbs/*`).
- `web/` — the PWA (vanilla JS/CSS, **no CDN, no build step** — everything loads
  from the host, so it works on isolated/offline conference WiFi).

## Notes & limitations

- **Presenter notes** are extracted as text from each slide's notes page.
- **Next-slide preview** is the next *slide's* image (not an exact per-animation
  "build" preview — that would require mirroring the native presenter view).
- **Security:** a random pairing **token** gates all control + thumbnails so others
  on the same WiFi can't hijack your deck. It's meant for trusted local networks;
  it is not hardened for the public internet.
- PowerPoint must have a slideshow running (F5) for navigation to work.

## Verify (dev)

```bash
# with the mock server running on :8765 and REMPREZ_TOKEN=testtoken
curl -s "http://127.0.0.1:8765/api/state?t=testtoken"        # full state
curl -s -X POST "http://127.0.0.1:8765/api/next?t=testtoken"  # advance
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/api/state  # 403 (no token)
```
