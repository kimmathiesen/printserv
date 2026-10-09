"""Simpel mock af Moonraker til lokal test af PrintHub.

Ekstra endpoints kun i mocken (bruges af tests/screenshots.py):
  POST /mock/reset            tilbage til standby, tom kø, kamera tændt
  POST /mock/camera?on=0|1    slå det falske kamera fra/til
"""
import asyncio
import copy
import json
import math
import struct
import time
import zlib

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI()
st = {"print_stats": {"state": "standby", "filename": "", "print_duration": 0, "total_duration": 0,
                      "filament_used": 0, "message": "", "info": {}},
      "extruder": {"temperature": 24.1, "target": 0, "power": 0},
      "heater_bed": {"temperature": 23.0, "target": 0, "power": 0},
      "display_status": {"progress": 0, "message": None}, "virtual_sdcard": {"progress": 0},
      "gcode_move": {"speed_factor": 1, "extrude_factor": 1, "homing_origin": [0, 0, 0, 0]},
      "fan": {"speed": 0},
      "webhooks": {"state": "ready", "state_message": "Printer is ready"}}
queue = {"queued_jobs": [], "queue_state": "paused"}
camera = {"on": True}
database = {}
files = {
    "benchy.gcode": {"size": 2_400_000, "estimated_time": 4380, "filament_total": 5200, "layer_height": 0.2,
                     "slicer": "OrcaSlicer", "modified_ago": 3600,
                     "thumbnails": [{"width": 300, "height": 300, "relative_path": ".thumbs/benchy-300x300.png"}]},
    "clip.gcode": {"size": 300_000, "estimated_time": 900, "filament_total": 800, "layer_height": 0.2,
                   "slicer": "PrusaSlicer", "modified_ago": 7200},
    "Kabelholder_v3_PETG_0.2mm_til_skrivebordet.gcode": {
        "size": 1_150_000, "estimated_time": 2700, "filament_total": 2900, "layer_height": 0.28,
        "slicer": "OrcaSlicer", "modified_ago": 86400 * 3,
        "thumbnails": [{"width": 300, "height": 300, "relative_path": ".thumbs/kabel-300x300.png"}]},
}
_initial = copy.deepcopy((st, queue, files))



# ---------------------------------------------------------------- små PNG-billeder uden afhængigheder
def png(w, h, px) -> bytes:
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw.extend(px(x, y))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def sphere(x, y, cx, cy, r, color):
    dx, dy = (x - cx) / r, (y - cy) / r
    d = dx * dx + dy * dy
    if d > 1:
        return None
    nz = math.sqrt(1 - d)
    light = max(0.0, -0.45 * dx - 0.55 * dy + 0.7 * nz)
    k = 0.45 + 0.6 * light
    return tuple(min(255, int(c * k)) for c in color) + (255,)


def thumb_px(color):
    def px(x, y):
        return sphere(x, y, 150, 140, 105, color) or (0, 0, 0, 0)
    return px


def cam_px(x, y):
    p = sphere(x, y, 320, 250, 62, (255, 120, 50))
    if p and y > 215:                       # halvt printet objekt
        return p
    if 120 < x < 520 and 40 < y < 70:       # x-gantry
        return (70, 72, 78, 255)
    if 300 < x < 340 and 70 < y < 120:      # dysehoved
        return (40, 42, 46, 255)
    if y > 230:                             # pladen
        g = 58 + (6 if (x // 40 + y // 40) % 2 else 0)
        return (g, g, g + 4, 255)
    v = 22 + y // 12
    return (v, v + 2, v + 6, 255)


THUMBS = {"benchy": png(300, 300, thumb_px((255, 110, 45))), "kabel": png(300, 300, thumb_px((60, 140, 255)))}
CAM = png(640, 360, cam_px)

# ---------------------------------------------------------------- websocket
clients = []


async def push(d, method="notify_status_update"):
    for c in list(clients):
        try:
            params = [d, time.time()] if method == "notify_status_update" else []
            await c.send_text(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))
        except Exception:
            clients.remove(c)


@app.websocket("/websocket")
async def ws(w: WebSocket):
    await w.accept()
    clients.append(w)
    while True:
        m = json.loads(await w.receive_text())
        res = {"status": st} if m["method"] == "printer.objects.subscribe" else {"connection_id": 1}
        await w.send_text(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": res}))


# ---------------------------------------------------------------- filer
@app.get("/server/files/directory")
def directory(path: str = "gcodes", extended: str = ""):
    now = time.time()
    out = [{"filename": k, "modified": now - v.get("modified_ago", 60),
            **{a: b for a, b in v.items() if a != "modified_ago"}} for k, v in files.items()]
    return {"result": {"dirs": [], "files": out, "disk_usage": {"free": 20e9, "total": 29e9, "used": 9e9}}}


@app.get("/server/files/gcodes/{p:path}")
def get_file(p: str):
    for key, data in THUMBS.items():
        if key in p:
            return Response(data, media_type="image/png")
    return Response(status_code=404)


@app.delete("/server/files/gcodes/{p:path}")
async def delete_file(p: str):
    files.pop(p, None)
    await push({}, "notify_filelist_changed")
    return {"result": {"item": {"path": p, "root": "gcodes"}, "action": "delete_file"}}


@app.post("/server/files/upload")
async def upload(req: Request):
    f = (await req.form())["file"]
    files[f.filename] = {"size": len(await f.read()), "modified_ago": 0}
    await push({}, "notify_filelist_changed")
    return {"result": {"item": {"path": f.filename, "root": "gcodes"}, "action": "create_file"}}


# ---------------------------------------------------------------- print
@app.post("/printer/print/start")
async def start(filename: str):
    st["print_stats"].update(state="printing", filename=filename, print_duration=1500, total_duration=1560,
                             filament_used=1250, info={"current_layer": 18, "total_layer": 80})
    st["display_status"]["progress"] = 0.34
    st["extruder"].update(temperature=204.6, target=205, power=0.42)
    st["heater_bed"].update(temperature=60.1, target=60, power=0.2)
    st["fan"]["speed"] = 1.0
    await push({k: st[k] for k in ("print_stats", "display_status", "extruder", "heater_bed", "fan")})
    return {"result": "ok"}


@app.post("/printer/print/{a}")
async def act(a: str):
    st["print_stats"]["state"] = {"pause": "paused", "resume": "printing", "cancel": "cancelled"}[a]
    await push({"print_stats": {"state": st["print_stats"]["state"]}})
    return {"result": "ok"}


@app.post("/printer/emergency_stop")
async def estop():
    st["webhooks"].update(state="shutdown", state_message="Shutdown due to M112 command")
    if st["print_stats"]["state"] in ("printing", "paused"):
        st["print_stats"].update(state="error", message="Shutdown due to M112 command")
    st["extruder"]["target"] = st["heater_bed"]["target"] = 0
    await push({k: st[k] for k in ("webhooks", "print_stats", "extruder", "heater_bed")})
    await push({}, "notify_klippy_shutdown")
    return {"result": "ok"}


@app.post("/printer/firmware_restart")
async def firmware_restart():
    st["webhooks"].update(state="ready", state_message="Printer is ready")
    st["print_stats"].update(state="standby", message="")
    await push({k: st[k] for k in ("webhooks", "print_stats")})
    await push({}, "notify_klippy_ready")
    return {"result": "ok"}


@app.post("/printer/gcode/script")
async def gcode(script: str):
    for line in script.splitlines():
        words = line.split()
        cmd, args = words[0].upper(), dict(w.split("=", 1) if "=" in w else (w[0], w[1:]) for w in words[1:])
        if cmd == "SET_HEATER_TEMPERATURE":
            st[args["HEATER"]]["target"] = float(args["TARGET"])
            await push({args["HEATER"]: {"target": float(args["TARGET"])}})
        elif cmd in ("M220", "M221"):
            key = "speed_factor" if cmd == "M220" else "extrude_factor"
            st["gcode_move"][key] = float(args["S"]) / 100
            await push({"gcode_move": st["gcode_move"]})
        elif cmd in ("M106", "M107"):
            st["fan"]["speed"] = float(args.get("S", 0)) / 255 if cmd == "M106" else 0
            await push({"fan": st["fan"]})
        elif cmd == "SET_GCODE_OFFSET":
            st["gcode_move"]["homing_origin"][2] = round(st["gcode_move"]["homing_origin"][2] + float(args["Z_ADJUST"]), 3)
            await push({"gcode_move": st["gcode_move"]})
        GCODE_LOG.append({"message": line, "type": "command", "time": time.time()})
    GCODE_LOG.append({"message": "ok", "type": "response", "time": time.time()})
    return {"result": "ok"}


@app.get("/server/temperature_store")
def temperature_store(include_monitors: str = ""):
    """1200 målinger (20 min). Med mål > 0 varmes der op ca. 12 min. før nu."""
    out = {}
    for heater, room, rate in (("extruder", 24.0, 0.012), ("heater_bed", 23.0, 0.006)):
        tgt, temps, tgts = st[heater]["target"], [], []
        for i in range(1200):
            t0 = 480                                      # opvarmning starter her
            if tgt and i >= t0:
                v = tgt - (tgt - room) * math.exp(-rate * (i - t0)) + math.sin(i / 7) * 0.4
                temps.append(round(v, 2)); tgts.append(tgt)
            else:
                temps.append(round(room + math.sin(i / 13) * 0.2, 2)); tgts.append(0)
        out[heater] = {"temperatures": temps, "targets": tgts, "powers": [0] * 1200}
    return {"result": out}


@app.get("/server/database/item")
def db_get(namespace: str, key: str):
    if (namespace, key) not in database:
        return JSONResponse({"error": {"code": 404, "message": f"Key '{key}' in namespace '{namespace}' not found"}}, 404)
    return {"result": {"namespace": namespace, "key": key, "value": database[(namespace, key)]}}


@app.post("/server/database/item")
async def db_post(req: Request):
    b = await req.json()
    database[(b["namespace"], b["key"])] = b["value"]
    return {"result": {"namespace": b["namespace"], "key": b["key"], "value": b["value"]}}


GCODE_LOG = [{"message": m, "type": t, "time": i} for i, (t, m) in enumerate([
    ("response", "// Klipper state: Ready"), ("command", "G28"), ("response", "ok"),
    ("command", "BED_MESH_CALIBRATE"), ("response", "// probe at 30.000,30.000 is z=0.112500"),
    ("response", "// Mesh Bed Leveling Complete"), ("response", "ok"),
    ("command", "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=205"), ("response", "ok"),
])]


@app.get("/server/gcode_store")
def gcode_store(count: int = 50):
    return {"result": {"gcode_store": GCODE_LOG[-count:]}}


# ---------------------------------------------------------------- kø og historik
@app.get("/server/job_queue/status")
def queue_status():
    return {"result": queue}


@app.post("/server/job_queue/job")
async def queue_add(req: Request):
    b = await req.json()
    for f in b["filenames"]:
        queue["queued_jobs"].append({"filename": f, "job_id": f"{time.time_ns() % 10**15:016X}",
                                     "time_added": time.time(), "time_in_queue": 12})
    await push({}, "notify_job_queue_changed")
    return {"result": queue}


@app.delete("/server/job_queue/job")
async def queue_delete(job_ids: str = "", all: str = ""):
    queue["queued_jobs"] = [] if all else [j for j in queue["queued_jobs"] if j["job_id"] not in job_ids.split(",")]
    await push({}, "notify_job_queue_changed")
    return {"result": queue}


@app.post("/server/job_queue/jump")
async def queue_jump(job_id: str):
    jobs = queue["queued_jobs"]
    queue["queued_jobs"] = [j for j in jobs if j["job_id"] == job_id] + [j for j in jobs if j["job_id"] != job_id]
    await push({}, "notify_job_queue_changed")
    return {"result": queue}


@app.post("/server/job_queue/{a}")
async def queue_state(a: str):
    queue["queue_state"] = "ready" if a == "start" else "paused"
    await push({}, "notify_job_queue_changed")
    return {"result": queue}


@app.get("/server/history/list")
def history(limit: int = 10, order: str = "desc"):
    now = time.time()
    jobs = [{"filename": "benchy.gcode", "status": "completed", "print_duration": 4300, "start_time": now - 86400},
            {"filename": "clip.gcode", "status": "cancelled", "print_duration": 310, "start_time": now - 86400 * 2},
            {"filename": "Kabelholder_v3_PETG_0.2mm_til_skrivebordet.gcode", "status": "completed",
             "print_duration": 2650, "start_time": now - 86400 * 4},
            {"filename": "clip.gcode", "status": "klippy_shutdown", "print_duration": 95, "start_time": now - 86400 * 6}]
    return {"result": {"count": len(jobs), "jobs": jobs[:limit]}}


# ---------------------------------------------------------------- kamera (crowsnest)
@app.get("/webcam/")
async def cam(action: str = "snapshot"):
    if not camera["on"]:
        return Response(status_code=503)
    if action == "snapshot":
        return Response(CAM, media_type="image/png")

    async def frames():
        while True:
            yield (b"--frame\r\nContent-Type: image/png\r\nContent-Length: " + str(len(CAM)).encode()
                   + b"\r\n\r\n" + CAM + b"\r\n")
            await asyncio.sleep(1)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


# ---------------------------------------------------------------- kun mock
@app.post("/mock/reset")
async def reset():
    s0, q0, f0 = copy.deepcopy(_initial)
    st.clear(); st.update(s0)
    files.clear(); files.update(f0)
    queue.clear(); queue.update(q0)
    camera["on"] = True
    database.clear()
    await push(st)
    await push({}, "notify_job_queue_changed")
    return {"result": "ok"}


@app.post("/mock/camera")
def set_camera(on: int = 1):
    camera["on"] = bool(on)
    return {"result": camera}
