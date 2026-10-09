"""Skærmbilleder af alle faner (mobil + desktop, lyst + mørkt) mod en kørende PrintHub.

Brug:  python tests/screenshots.py [mappe]   (standard: shots/)
Kræver at mock-Moonraker (7125) og PrintHub (8095) kører med kameraet peget på mocken:
  CAMERA_STREAM_URL=http://127.0.0.1:7125/webcam/?action=stream
  CAMERA_SNAPSHOT_URL=http://127.0.0.1:7125/webcam/?action=snapshot
og `playwright install chromium`.

Hver fane fotograferes som den første skærm; er siden længere, også rullet helt ned (`-bund`).
Scenarier: `klar` (standby, intet kamera, tom kø) og `printer` (print i gang, kamera, job i kø).
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HUB = "http://127.0.0.1:8095"
MOCK = "http://127.0.0.1:7125"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
SIZES = {"mobil": (390, 844), "desktop": (1280, 800)}
TABS = ["printer", "files", "queue", "console"]
# (navn, fane, hvad der klikkes på) - åbner et sheet, kun i scenariet `printer`
SHEETS = [("temp", "printer", "[data-heater=extruder]"), ("fil", "files", "#fileList .item"),
          ("nodstop", "printer", "#estopBtn"), ("annuller", "printer", "#cancelBtn")]


def post(url, body=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, method="POST", data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5)


def shoot(page, name):
    page.wait_for_timeout(700)
    page.screenshot(path=OUT / f"{name}.png")
    if page.evaluate("document.documentElement.scrollHeight > innerHeight + 4"):
        page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(300)
        page.screenshot(path=OUT / f"{name}-bund.png")
        page.evaluate("scrollTo(0, 0)")


def run(scenario, errors):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, (w, h) in SIZES.items():
            for scheme in ("light", "dark"):
                ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme,
                                          device_scale_factor=2 if size == "mobil" else 1)
                page = ctx.new_page()
                page.on("console", lambda m: m.type == "error" and errors.append(f"{scenario}: {m.text}"))
                page.on("pageerror", lambda e: errors.append(f"{scenario}: {e}"))
                page.goto(HUB)
                page.wait_for_timeout(1500)
                for tab in (TABS if scenario != "stoppet" else ["printer"]):
                    page.click(f"[data-view={tab}]")
                    shoot(page, f"{scenario}-{tab}-{size}-{scheme}")
                if scenario == "printer":
                    for name, tab, sel in SHEETS:
                        page.goto(HUB)
                        page.wait_for_timeout(1200)
                        page.click(f"[data-view={tab}]")
                        page.wait_for_timeout(500)
                        try:
                            page.click(sel, timeout=2000)
                        except Exception:
                            continue
                        page.wait_for_timeout(700)
                        page.screenshot(path=OUT / f"sheet-{name}-{size}-{scheme}.png")
                        # klik aldrig på bekræft-knappen; dialogen lukkes ved genindlæsning
                    page.goto(HUB + "/#filer")
                    page.reload()
                    page.wait_for_timeout(1000)
                    page.set_input_files("#fileInput", files=[{"name": "ny_model.gcode", "mimeType": "text/plain",
                                                               "buffer": b"G28\n" * 50000}])
                    page.wait_for_timeout(700)
                    page.screenshot(path=OUT / f"sheet-upload-{size}-{scheme}.png")
                    if size == "desktop":
                        page.reload()
                        page.wait_for_timeout(800)
                        page.evaluate("document.getElementById('dropzone').classList.add('show')")
                        page.wait_for_timeout(300)
                        page.screenshot(path=OUT / f"drop-{size}-{scheme}.png")
                ctx.close()
        browser.close()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    errs = []
    post(f"{MOCK}/mock/reset")
    post(f"{MOCK}/mock/camera?on=0")
    run("klar", errs)
    post(f"{MOCK}/mock/camera?on=1")
    post(f"{MOCK}/printer/print/start?filename=benchy.gcode")
    post(f"{HUB}/api/queue/start")
    post(f"{HUB}/api/queue", {"filenames": ["clip.gcode"]})
    post(f"{HUB}/api/queue", {"filenames": ["Kabelholder_v3_PETG_0.2mm_til_skrivebordet.gcode"]})
    run("printer", errs)
    post(f"{MOCK}/printer/emergency_stop")
    run("stoppet", errs)
    post(f"{MOCK}/mock/reset")
    print(f"{len(list(OUT.glob('*.png')))} billeder i {OUT}/")
    for e in sorted(set(errs)):
        print("KONSOLFEJL:", e)
