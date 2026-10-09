"""PrintHub - let frontend til Klipper/Moonraker med kø, filer, kamera og notifikationer."""
import asyncio
import base64
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

import httpx
import websockets
from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("printhub")

# ---------------------------------------------------------------- konfiguration
MOONRAKER_URL = os.getenv("MOONRAKER_URL", "http://printer.local:7125").rstrip("/")
MOONRAKER_API_KEY = os.getenv("MOONRAKER_API_KEY", "")
_host = urlparse(MOONRAKER_URL).hostname
CAMERA_STREAM_URL = os.getenv("CAMERA_STREAM_URL", f"http://{_host}/webcam/?action=stream")
CAMERA_SNAPSHOT_URL = os.getenv("CAMERA_SNAPSHOT_URL", f"http://{_host}/webcam/?action=snapshot")
OBICO_URL = os.getenv("OBICO_URL", "")
PRINTER_NAME = os.getenv("PRINTER_NAME", "CR-6 SE")
NTFY_URL = os.getenv("NTFY_URL", "").rstrip("/")          # fx https://ntfy.sh eller http://192.168.68.58:8090
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")
HA_WEBHOOK_URL = os.getenv("HA_WEBHOOK_URL", "")          # fx http://192.168.68.72:8123/api/webhook/printhub
APP_USER = os.getenv("APP_USER", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")              # tom = ingen login (kun LAN!)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

SUBSCRIBE_OBJECTS = {
    "print_stats": None,
    "virtual_sdcard": ["progress", "is_active", "file_position"],
    "display_status": ["progress", "message"],
    "extruder": ["temperature", "target", "power"],
    "heater_bed": ["temperature", "target", "power"],
    "toolhead": ["homed_axes", "position", "print_time", "estimated_print_time"],
    "gcode_move": ["speed_factor", "extrude_factor"],
    "fan": ["speed"],
    "webhooks": ["state", "state_message"],
    "idle_timeout": ["state"],
}

# ---------------------------------------------------------------- tilstand
state: dict = {"connected": False, "status": {}, "queue": None, "updated": 0}
browser_clients: set[WebSocket] = set()
http: httpx.AsyncClient | None = None


def _headers() -> dict:
    return {"X-Api-Key": MOONRAKER_API_KEY} if MOONRAKER_API_KEY else {}


def deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v


async def broadcast(msg: dict) -> None:
    dead = []
    data = json.dumps(msg)
    for ws in browser_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        browser_clients.discard(ws)


# ---------------------------------------------------------------- notifikationer
async def notify(title: str, message: str, tags: str = "printer", priority: str = "default",
                 with_snapshot: bool = False, event: str = "") -> None:
    log.info("Notifikation: %s - %s", title, message)
    tasks = []
    if NTFY_URL and NTFY_TOPIC:
        tasks.append(_notify_ntfy(title, message, tags, priority, with_snapshot))
    if HA_WEBHOOK_URL:
        tasks.append(_notify_ha(title, message, event))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _rfc2047(s: str) -> str:
    """ntfy accepterer RFC 2047-kodede headers, så æøå og emoji overlever."""
    return "=?UTF-8?B?" + base64.b64encode(s.encode("utf-8")).decode() + "?="


async def _notify_ntfy(title, message, tags, priority, with_snapshot):
    headers = {"Title": _rfc2047(title), "Tags": tags, "Priority": priority}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    url = f"{NTFY_URL}/{NTFY_TOPIC}"
    try:
        if with_snapshot:
            try:
                snap = await http.get(CAMERA_SNAPSHOT_URL, timeout=5)
                if snap.status_code == 200:
                    headers["Filename"] = "snapshot.jpg"
                    headers["Message"] = _rfc2047(message)
                    await http.put(url, content=snap.content, headers=headers, timeout=15)
                    return
            except Exception:
                pass
        await http.post(url, content=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception as e:
        log.warning("ntfy fejlede: %s", e)


async def _notify_ha(title, message, event):
    ps = state["status"].get("print_stats", {})
    try:
        await http.post(HA_WEBHOOK_URL, json={
            "title": title, "message": message, "event": event, "printer": PRINTER_NAME,
            "filename": ps.get("filename"), "state": ps.get("state"),
            "progress": round(_progress() * 100, 1),
        }, timeout=10)
    except Exception as e:
        log.warning("Home Assistant webhook fejlede: %s", e)


def _progress() -> float:
    st = state["status"]
    return float(st.get("display_status", {}).get("progress")
                 or st.get("virtual_sdcard", {}).get("progress") or 0)


def _fmt_dur(sec: float) -> str:
    sec = int(sec or 0)
    h, m = divmod(sec // 60, 60)
    return f"{h}t {m}m" if h else f"{m}m"


last_print_state: str | None = None


async def handle_state_change(new: str) -> None:
    global last_print_state
    old = last_print_state
    last_print_state = new
    if old is None or old == new:
        return
    ps = state["status"].get("print_stats", {})
    fn = ps.get("filename") or "ukendt fil"
    if new == "printing" and old in ("standby", "complete", "cancelled", "error"):
        await notify(f"{PRINTER_NAME}: Print startet", fn, "arrow_forward", event="started")
    elif new == "printing" and old == "paused":
        await notify(f"{PRINTER_NAME}: Genoptaget", fn, "arrow_forward", event="resumed")
    elif new == "paused":
        await notify(f"{PRINTER_NAME}: Pauset", fn, "pause_button", "high", True, "paused")
    elif new == "complete":
        await notify(f"{PRINTER_NAME}: Færdig ✅", f"{fn} tog {_fmt_dur(ps.get('print_duration'))}",
                     "white_check_mark", "high", True, "complete")
    elif new == "cancelled":
        await notify(f"{PRINTER_NAME}: Annulleret", fn, "x", event="cancelled")
    elif new == "error":
        await notify(f"{PRINTER_NAME}: FEJL", f"{fn}: {ps.get('message', '')}", "rotating_light",
                     "urgent", True, "error")


# ---------------------------------------------------------------- Moonraker websocket
async def moonraker_loop() -> None:
    ws_url = MOONRAKER_URL.replace("http", "ws", 1) + "/websocket"
    req_id = 0
    while True:
        try:
            extra = {}
            if MOONRAKER_API_KEY:
                extra["additional_headers"] = _headers()
            async with websockets.connect(ws_url, max_size=2**24, ping_interval=20, **extra) as ws:
                log.info("Forbundet til Moonraker %s", ws_url)
                req_id += 1
                await ws.send(json.dumps({"jsonrpc": "2.0", "method": "server.connection.identify",
                                          "params": {"client_name": "PrintHub", "version": "1.0",
                                                     "type": "web", "url": "http://localhost"},
                                          "id": req_id}))
                sub_id = req_id = req_id + 1
                await ws.send(json.dumps({"jsonrpc": "2.0", "method": "printer.objects.subscribe",
                                          "params": {"objects": SUBSCRIBE_OBJECTS}, "id": sub_id}))
                state["connected"] = True
                await refresh_queue()
                await broadcast({"type": "connection", "connected": True})
                async for raw in ws:
                    msg = json.loads(raw)
                    await handle_moonraker_msg(msg, sub_id)
        except Exception as e:
            log.warning("Moonraker-forbindelse tabt: %s - prøver igen om 5s", e)
        state["connected"] = False
        await broadcast({"type": "connection", "connected": False})
        await asyncio.sleep(5)


async def handle_moonraker_msg(msg: dict, sub_id: int) -> None:
    if msg.get("id") == sub_id and "result" in msg:
        state["status"] = msg["result"].get("status", {})
        await after_status_update()
        return
    method = msg.get("method")
    if method == "notify_status_update":
        deep_merge(state["status"], msg["params"][0])
        await after_status_update()
    elif method == "notify_job_queue_changed":
        await refresh_queue()
    elif method in ("notify_klippy_ready", "notify_klippy_shutdown", "notify_klippy_disconnected"):
        await broadcast({"type": "klippy", "event": method})
        if method == "notify_klippy_shutdown":
            await notify(f"{PRINTER_NAME}: Klipper shutdown", "Printeren er i shutdown-tilstand",
                         "warning", "high", event="shutdown")
    elif method == "notify_filelist_changed":
        await broadcast({"type": "files_changed"})


async def after_status_update() -> None:
    state["updated"] = time.time()
    ps_state = state["status"].get("print_stats", {}).get("state")
    if ps_state:
        await handle_state_change(ps_state)
    await broadcast({"type": "status", "status": state["status"]})


async def refresh_queue() -> None:
    try:
        r = await mr("GET", "/server/job_queue/status")
        state["queue"] = r
        await broadcast({"type": "queue", "queue": r})
    except Exception as e:
        log.warning("Kunne ikke hente kø: %s", e)


# ---------------------------------------------------------------- Moonraker HTTP
async def mr(method: str, path: str, **kw):
    try:
        r = await http.request(method, MOONRAKER_URL + path, headers=_headers(), **kw)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Moonraker kan ikke nås: {e}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", {}).get("message", r.text)
        except Exception:
            detail = r.text
        raise HTTPException(r.status_code, detail)
    data = r.json()
    return data.get("result", data)


# ---------------------------------------------------------------- app
@asynccontextmanager
async def lifespan(app: FastAPI):
    global http
    http = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
    task = asyncio.create_task(moonraker_loop())
    yield
    task.cancel()
    await http.aclose()


app = FastAPI(title="PrintHub", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if APP_PASSWORD and request.url.path != "/healthz":
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, APP_USER) and secrets.compare_digest(pw, APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response("Login påkrævet", 401, {"WWW-Authenticate": 'Basic realm="PrintHub"'})
    return await call_next(request)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "moonraker": state["connected"]}


@app.get("/api/config")
async def config():
    return {"printer_name": PRINTER_NAME, "obico_url": OBICO_URL,
            "notifications": {"ntfy": bool(NTFY_URL and NTFY_TOPIC), "ha": bool(HA_WEBHOOK_URL)}}


@app.get("/api/status")
async def status():
    return {"connected": state["connected"], "status": state["status"], "queue": state["queue"]}


# --- filer
@app.get("/api/files")
async def files():
    d = await mr("GET", "/server/files/directory", params={"path": "gcodes", "extended": "true"})
    out = []

    async def walk(path, data):
        for f in data.get("files", []):
            rel = f"{path}/{f['filename']}" if path else f["filename"]
            if not rel.lower().endswith((".gcode", ".g", ".gco")):
                continue
            thumbs = f.get("thumbnails") or []
            thumb = max(thumbs, key=lambda t: t.get("width", 0), default=None)
            thumb_path = None
            if thumb:
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                thumb_path = f"{parent}/{thumb['relative_path']}" if parent else thumb["relative_path"]
            out.append({"path": rel, "size": f.get("size"), "modified": f.get("modified"),
                        "estimated_time": f.get("estimated_time"), "filament_total": f.get("filament_total"),
                        "layer_height": f.get("layer_height"), "slicer": f.get("slicer"),
                        "thumbnail": thumb_path})
        for sub in data.get("dirs", []):
            name = sub["dirname"]
            if name.startswith("."):
                continue
            p = f"{path}/{name}" if path else name
            sd = await mr("GET", "/server/files/directory", params={"path": f"gcodes/{p}", "extended": "true"})
            await walk(p, sd)

    await walk("", d)
    out.sort(key=lambda x: x.get("modified") or 0, reverse=True)
    disk = d.get("disk_usage", {})
    return {"files": out, "disk": disk}


@app.get("/api/thumb/{path:path}")
async def thumb(path: str):
    try:
        r = await http.get(f"{MOONRAKER_URL}/server/files/gcodes/{quote(path)}", headers=_headers())
    except httpx.HTTPError:
        raise HTTPException(502)
    if r.status_code != 200:
        raise HTTPException(404)
    return Response(r.content, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@app.post("/api/files/upload")
async def upload(file: UploadFile, action: str = "none"):
    """action: none | print | queue"""
    content = await file.read()
    data = {"root": "gcodes"}
    if action == "print":
        data["print"] = "true"
    r = await mr("POST", "/server/files/upload", files={"file": (file.filename, content)}, data=data)
    if action == "queue":
        await mr("POST", "/server/job_queue/job", json={"filenames": [r["item"]["path"]]})
        await refresh_queue()
    return r


@app.delete("/api/files/{path:path}")
async def delete_file(path: str):
    return await mr("DELETE", f"/server/files/gcodes/{quote(path)}")


# --- print
@app.post("/api/print/start")
async def print_start(body: dict):
    return await mr("POST", "/printer/print/start", params={"filename": body["filename"]})


@app.post("/api/print/{action}")
async def print_action(action: str):
    if action not in ("pause", "resume", "cancel"):
        raise HTTPException(400)
    return await mr("POST", f"/printer/print/{action}")


@app.post("/api/estop")
async def estop():
    return await mr("POST", "/printer/emergency_stop")


@app.post("/api/firmware_restart")
async def firmware_restart():
    return await mr("POST", "/printer/firmware_restart")


@app.post("/api/gcode")
async def gcode(body: dict):
    script = str(body.get("script", "")).strip()
    if not script:
        raise HTTPException(400, "Tom kommando")
    return await mr("POST", "/printer/gcode/script", params={"script": script}, timeout=None)


@app.post("/api/temp")
async def set_temp(body: dict):
    heater = body.get("heater")
    target = float(body.get("target", 0))
    if heater not in ("extruder", "heater_bed") or not 0 <= target <= 300:
        raise HTTPException(400, "Ugyldig temperatur")
    return await mr("POST", "/printer/gcode/script",
                    params={"script": f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET={target:g}"})


@app.get("/api/gcode/log")
async def gcode_log(count: int = 50):
    return await mr("GET", "/server/gcode_store", params={"count": count})


@app.get("/api/history")
async def history(limit: int = 20):
    return await mr("GET", "/server/history/list", params={"limit": limit, "order": "desc"})


# --- kø
@app.get("/api/queue")
async def queue():
    return await mr("GET", "/server/job_queue/status")


@app.post("/api/queue")
async def queue_add(body: dict):
    r = await mr("POST", "/server/job_queue/job", json={"filenames": body["filenames"]})
    await refresh_queue()
    return r


@app.delete("/api/queue/{job_id}")
async def queue_remove(job_id: str):
    r = await mr("DELETE", "/server/job_queue/job", params={"job_ids": job_id})
    await refresh_queue()
    return r


@app.post("/api/queue/{action}")
async def queue_action(action: str, job_id: str = ""):
    if action == "jump":
        r = await mr("POST", "/server/job_queue/jump", params={"job_id": job_id})
    elif action in ("start", "pause"):
        r = await mr("POST", f"/server/job_queue/{action}")
    elif action == "clear":
        r = await mr("DELETE", "/server/job_queue/job", params={"all": "true"})
    else:
        raise HTTPException(400)
    await refresh_queue()
    return r


# --- kamera (proxies, så det også virker udefra via én adresse)
@app.get("/api/camera/snapshot")
async def snapshot():
    try:
        r = await http.get(CAMERA_SNAPSHOT_URL, timeout=8)
    except httpx.HTTPError:
        raise HTTPException(502, "Kamera kan ikke nås")
    return Response(r.content, media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "no-store"})


@app.get("/api/camera/stream")
async def stream():
    client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=None))
    try:
        req = client.build_request("GET", CAMERA_STREAM_URL)
        r = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(502, "Kamera kan ikke nås")

    async def gen():
        try:
            async for chunk in r.aiter_raw():
                yield chunk
        finally:
            await r.aclose()
            await client.aclose()

    return StreamingResponse(gen(), media_type=r.headers.get("content-type", "multipart/x-mixed-replace"),
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --- test af notifikationer
@app.post("/api/notify/test")
async def notify_test():
    await notify(f"{PRINTER_NAME}: Test", "PrintHub kan sende notifikationer 🎉", "tada", with_snapshot=True,
                 event="test")
    return {"ok": True}


# --- live opdateringer til browseren
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    browser_clients.add(ws)
    await ws.send_text(json.dumps({"type": "init", "connected": state["connected"],
                                   "status": state["status"], "queue": state["queue"]}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        browser_clients.discard(ws)


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
