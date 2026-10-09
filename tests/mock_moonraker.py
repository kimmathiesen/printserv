"""Simpel mock af Moonraker til lokal test af PrintHub."""
import asyncio, json, time
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response
app = FastAPI()
st = {"print_stats":{"state":"standby","filename":"","print_duration":0,"total_duration":0,"filament_used":0,"info":{}},
      "extruder":{"temperature":24.1,"target":0},"heater_bed":{"temperature":23.0,"target":0},
      "display_status":{"progress":0},"virtual_sdcard":{"progress":0},"gcode_move":{"speed_factor":1}}
queue = {"queued_jobs":[],"queue_state":"paused"}
files = {"benchy.gcode":{"size":2_400_000,"estimated_time":4380,"filament_total":5200,"thumbnails":[{"width":300,"height":300,"relative_path":".thumbs/benchy-300x300.png"}]},
         "clip.gcode":{"size":300_000,"estimated_time":900,"filament_total":800}}
clients=[]
async def push(d):
    for c in list(clients):
        try: await c.send_text(json.dumps({"jsonrpc":"2.0","method":"notify_status_update","params":[d,time.time()]}))
        except Exception: clients.remove(c)
@app.websocket("/websocket")
async def ws(w: WebSocket):
    await w.accept(); clients.append(w)
    while True:
        m=json.loads(await w.receive_text())
        res = {"status":st} if m["method"]=="printer.objects.subscribe" else {"connection_id":1}
        await w.send_text(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":res}))
@app.get("/server/files/directory")
def d(path:str="gcodes", extended:str=""):
    return {"result":{"dirs":[],"files":[{"filename":k,"modified":time.time()-3600,**v} for k,v in files.items()],"disk_usage":{"free":20e9}}}
@app.get("/server/files/gcodes/{p:path}")
def thumb(p:str):
    import base64
    return Response(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="),media_type="image/png")
@app.post("/printer/print/start")
async def start(filename:str):
    st["print_stats"].update(state="printing",filename=filename,print_duration=600,total_duration=620)
    st["display_status"]["progress"]=0.23; st["extruder"].update(temperature=205,target=205); st["heater_bed"].update(temperature=60,target=60)
    await push({"print_stats":st["print_stats"],"display_status":st["display_status"],"extruder":st["extruder"],"heater_bed":st["heater_bed"]}); return {"result":"ok"}
@app.post("/printer/print/{a}")
async def act(a:str):
    st["print_stats"]["state"]={"pause":"paused","resume":"printing","cancel":"cancelled"}[a]
    await push({"print_stats":{"state":st["print_stats"]["state"]}}); return {"result":"ok"}
@app.post("/printer/gcode/script")
async def g(script:str):
    if script.startswith("SET_HEATER"):
        parts=dict(x.split("=") for x in script.split()[1:]); st[parts["HEATER"]]["target"]=float(parts["TARGET"])
        await push({parts["HEATER"]:{"target":float(parts["TARGET"])}})
    return {"result":"ok"}
@app.get("/server/job_queue/status")
def qs(): return {"result":queue}
@app.post("/server/job_queue/job")
async def qa(req:Request):
    b=await req.json()
    for f in b["filenames"]: queue["queued_jobs"].append({"filename":f,"job_id":f"{len(queue['queued_jobs']):016X}","time_added":time.time(),"time_in_queue":12})
    return {"result":queue}
@app.delete("/server/job_queue/job")
def qd(job_ids:str="",all:str=""):
    queue["queued_jobs"]=[] if all else [j for j in queue["queued_jobs"] if j["job_id"] not in job_ids.split(",")]; return {"result":queue}
@app.post("/server/job_queue/{a}")
def qx(a:str): queue["queue_state"]="ready" if a=="start" else "paused"; return {"result":queue}
@app.post("/server/files/upload")
async def up(req:Request):
    f=(await req.form())["file"]; files[f.filename]={"size":len(await f.read())}
    return {"result":{"item":{"path":f.filename,"root":"gcodes"},"action":"create_file"}}
@app.get("/server/history/list")
def h(limit:int=10,order:str="desc"): return {"result":{"count":1,"jobs":[{"filename":"benchy.gcode","status":"completed","print_duration":4300,"start_time":time.time()-86400}]}}
@app.get("/server/gcode_store")
def gs(count:int=50): return {"result":{"gcode_store":[{"message":"G28","type":"command","time":1},{"message":"// Klipper state: Ready","type":"response","time":2}]}}
@app.get("/webcam/")
def cam(action:str="snapshot"): return thumb("")
