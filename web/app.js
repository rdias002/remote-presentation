// Presentation Remote — PWA client.
// Talks to the host over a WebSocket (with REST fallback), renders live state,
// and sends navigation commands. All assets are self-hosted; no CDN.

const $ = (id) => document.getElementById(id);

// ---- token ----------------------------------------------------------------
// Prefer the token from the pairing URL (?t=...); otherwise reuse the last one
// we stored, so an installed PWA keeps working when launched from the home
// screen (set a fixed REMPREZ_TOKEN on the host for this to stay stable).
function resolveToken() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("t");
  if (fromUrl) {
    try { localStorage.setItem("remprez_token", fromUrl); } catch (_) {}
    return fromUrl;
  }
  try { return localStorage.getItem("remprez_token") || ""; } catch (_) { return ""; }
}
const TOKEN = resolveToken();
const withToken = (path) => path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);

// ---- state ----------------------------------------------------------------
let ws = null;
let reconnectDelay = 500;
let lastState = null;

const els = {
  title: $("deck-title"),
  status: $("status-msg"),
  position: $("position"),
  dot: $("conn-dot"),
  currentThumb: $("current-thumb"),
  currentFrame: $("current-frame"),
  nextThumb: $("next-thumb"),
  nextFrame: $("next-frame"),
  notesCard: $("notes-card"),
  notes: $("notes"),
  prev: $("btn-prev"),
  next: $("btn-next"),
  grid: $("grid"),
  gridOverlay: $("grid-overlay"),
};

// Point one preview frame at a thumbnail URL, or show its empty state.
function setPreview(frame, img, url) {
  if (url) {
    frame.classList.remove("empty");
    img.src = withToken(url);
  } else {
    frame.classList.add("empty");
    img.removeAttribute("src");
  }
}

// ---- notes font size (persisted) ------------------------------------------
function applyFont(px) {
  document.documentElement.style.setProperty("--notes-size", px + "px");
  try { localStorage.setItem("remprez_notes_px", String(px)); } catch (_) {}
}
let notesPx = 20;
try { notesPx = parseInt(localStorage.getItem("remprez_notes_px") || "20", 10) || 20; } catch (_) {}
applyFont(notesPx);
$("font-up").onclick = () => applyFont((notesPx = Math.min(40, notesPx + 2)));
$("font-down").onclick = () => applyFont((notesPx = Math.max(12, notesPx - 2)));

// ---- notes collapse (persisted) -------------------------------------------
let notesCollapsed = false;
try { notesCollapsed = localStorage.getItem("remprez_notes_collapsed") === "1"; } catch (_) {}
function applyNotesCollapsed() {
  els.notesCard.classList.toggle("collapsed", notesCollapsed);
  $("notes-toggle").setAttribute("aria-expanded", String(!notesCollapsed));
  try { localStorage.setItem("remprez_notes_collapsed", notesCollapsed ? "1" : "0"); } catch (_) {}
}
applyNotesCollapsed();
$("notes-toggle").onclick = () => { notesCollapsed = !notesCollapsed; applyNotesCollapsed(); };

// ---- commands -------------------------------------------------------------
function send(action, index) {
  const msg = index == null ? { action } : { action, index };
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  } else {
    // REST fallback keeps controls working during a WS reconnect.
    const path = action === "goto" ? `/api/goto/${index}` : `/api/${action}`;
    fetch(withToken(path), { method: "POST" })
      .then((r) => r.ok && r.json())
      .then((s) => s && render(s))
      .catch(() => {});
  }
}

els.prev.onclick = () => send("prev");
els.next.onclick = () => send("next");
$("btn-grid").onclick = () => toggleGrid(true);
$("grid-close").onclick = () => toggleGrid(false);

function toggleGrid(open) {
  els.gridOverlay.hidden = !open;
  if (open && lastState) {
    renderGrid(lastState);
    // Bring the current slide into view (list can be long).
    const cur = els.grid.querySelector(".cell.current");
    if (cur) cur.scrollIntoView({ block: "center" });
  }
}

// Hardware / keyboard niceties (bluetooth clickers, paired keyboards).
document.addEventListener("keydown", (e) => {
  if (["ArrowRight", "PageDown", " "].includes(e.key)) { e.preventDefault(); send("next"); }
  if (["ArrowLeft", "PageUp"].includes(e.key)) { e.preventDefault(); send("prev"); }
});

// ---- rendering ------------------------------------------------------------
function render(s) {
  lastState = s;
  els.title.textContent = s.title || "Presentation Remote";
  els.status.textContent = s.message || "";
  els.position.textContent = s.total ? `${s.index || "–"} / ${s.total}` : "—";

  // slide aspect ratio (width / height) → drives every preview frame + grid cell
  if (s.aspect && s.aspect > 0) {
    document.documentElement.style.setProperty("--slide-aspect", String(s.aspect));
  }

  // current + next slide previews
  setPreview(els.currentFrame, els.currentThumb, s.current_thumb_url);
  setPreview(els.nextFrame, els.nextThumb, s.next_thumb_url);

  // notes: hide the whole panel if the deck has none anywhere; else show text
  els.notesCard.classList.toggle("gone", !s.has_notes);
  els.notes.textContent = (s.current_notes && s.current_notes.trim()) ? s.current_notes : "No notes.";

  // controls availability
  els.prev.disabled = !s.is_running || s.index <= 1;
  els.next.disabled = !s.is_running || (s.total > 0 && s.index >= s.total);

  if (!els.gridOverlay.hidden) renderGrid(s);
}

function renderGrid(s) {
  const frag = document.createDocumentFragment();
  (s.slides || []).forEach((sl) => {
    const cell = document.createElement("div");
    cell.className = "cell" + (sl.index === s.index ? " current" : "") + (sl.hidden ? " hidden-slide" : "");
    cell.onclick = () => { send("goto", sl.index); toggleGrid(false); };

    // Same frame-div + image pattern as the Current/Next previews, so the
    // thumbnail keeps the slide aspect reliably (aspect-ratio on a bare <img>
    // collapses on some mobile browsers).
    const thumb = document.createElement("div");
    thumb.className = "thumb";
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = withToken(sl.thumb_url);
    thumb.append(img);

    const cap = document.createElement("div");
    cap.className = "cap";
    cap.innerHTML = `<span class="n">${sl.index}</span><span class="t">${escapeHtml(sl.title)}</span>`;

    cell.append(thumb, cap);
    frag.append(cell);
  });
  els.grid.replaceChildren(frag);
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---- websocket ------------------------------------------------------------
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}${withToken("/ws")}`);

  ws.onopen = () => {
    reconnectDelay = 500;
    els.dot.classList.add("ok");
  };
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === "state") render(data.state);
    } catch (_) {}
  };
  ws.onclose = () => {
    els.dot.classList.remove("ok");
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(5000, reconnectDelay * 1.6);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

// Keep the screen awake while presenting (best-effort).
async function keepAwake() {
  try {
    if ("wakeLock" in navigator) {
      let lock = await navigator.wakeLock.request("screen");
      document.addEventListener("visibilitychange", async () => {
        if (document.visibilityState === "visible") {
          try { lock = await navigator.wakeLock.request("screen"); } catch (_) {}
        }
      });
    }
  } catch (_) {}
}

// ---- service worker (offline app shell) -----------------------------------
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

connect();
keepAwake();
