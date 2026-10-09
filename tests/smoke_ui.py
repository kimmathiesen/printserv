"""Klikker UI'et igennem mod mock-Moonraker og tjekker, at handlingerne når frem.

Brug:  python tests/smoke_ui.py   (mock på 7125 og PrintHub på 8095 skal køre)
"""
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

HUB = "http://127.0.0.1:8095"
MOCK = "http://127.0.0.1:7125"


def get(path):
    return json.load(urllib.request.urlopen(HUB + path, timeout=5))


def post(url):
    urllib.request.urlopen(urllib.request.Request(url, method="POST", data=b""), timeout=5)


def status():
    return get("/api/status")["status"]


def main():
    post(f"{MOCK}/mock/reset")
    errors = []
    gcode = Path(tempfile.mkdtemp()) / "smoketest.gcode"
    gcode.write_text("G28\n")
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 390, "height": 844})
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(HUB)
        expect(page.locator("#statusText")).to_have_text("Klar")

        # temperatur: sheet, +5, sæt
        page.click("[data-heater=extruder]")
        page.click("[data-t='205']")
        page.click("[data-step='5']")
        page.click("#tSet")
        expect(page.locator("#tExtT")).to_contain_text("210")
        assert status()["extruder"]["target"] == 210
        page.click("[data-preset=petg]")
        expect(page.locator("#tBedT")).to_contain_text("75")
        assert status()["heater_bed"]["target"] == 75

        # upload og læg i kø
        page.click("[data-view=files]")
        expect(page.locator("#fileList .item")).to_have_count(3)
        page.set_input_files("#fileInput", str(gcode))
        page.click("[data-res=queue]")
        expect(page.locator("#fileList .item")).to_have_count(4)
        expect(page.locator("#queueBadge")).to_have_text("1")
        assert get("/api/queue")["queued_jobs"][-1]["filename"] == "smoketest.gcode"

        # søgning
        page.fill("#fileSearch", "kabel")
        expect(page.locator("#fileList .item")).to_have_count(1)
        page.fill("#fileSearch", "findesikke")
        expect(page.locator("#fileList")).to_contain_text("Ingen filer matcher")
        page.fill("#fileSearch", "")

        # print fra detalje-sheet
        page.locator("#fileList .item", has_text="benchy").click()
        page.click("#fPrint")
        expect(page.locator("#statusText")).to_contain_text("Printer nu")
        expect(page.locator("#jobName")).to_have_text("benchy")

        # annullér: først fortryd, så bekræft
        page.click("#cancelBtn")
        page.click("[data-res='']")
        assert status()["print_stats"]["state"] == "printing"
        page.click("#cancelBtn")
        page.click("[data-res='1']")
        expect(page.locator("#statusText")).to_have_text("Annulleret")

        # nødstop kræver bekræftelse, derefter firmware-genstart
        page.click("#estopBtn")
        page.click("[data-res='']")
        assert status()["webhooks"]["state"] == "ready"
        page.click("#estopBtn")
        page.click("[data-res='1']")
        expect(page.locator("#klippyAlert")).to_be_visible()
        expect(page.locator("#statusText")).to_have_text("Klipper er stoppet")
        page.click("#klippyRestart")
        page.click("[data-res='1']")
        expect(page.locator("#klippyAlert")).to_be_hidden()

        # kø: tilføj én til, flyt først, fjern, ryd
        post(f"{MOCK}/mock/reset")
        page.click("[data-view=files]")
        for name in ("clip", "benchy"):
            page.locator("#fileList .item", has_text=name).click()
            page.click("#fQueue")
            page.wait_for_timeout(300)
        page.click("[data-view=queue]")
        expect(page.locator("#queueList .item")).to_have_count(2)
        page.click("#queueList [data-jump]")
        expect(page.locator("#queueList .item").first).to_contain_text("benchy")
        page.locator("#queueList [data-rm]").first.click()
        expect(page.locator("#queueList .item")).to_have_count(1)
        page.click("#qToggle")
        expect(page.locator("#queuePill")).to_have_text("Aktiv")
        page.click("#qClear")
        page.click("[data-res='1']")
        expect(page.locator("#queueList")).to_contain_text("Køen er tom")
        expect(page.locator("#peekCard")).to_be_hidden()

        # konsol
        page.click("[data-view=console]")
        page.fill("#gcodeInput", "M115")
        page.press("#gcodeInput", "Enter")
        expect(page.locator("#console")).to_contain_text("M115")

        # hash-ruter
        page.goto(HUB + "/#ko")
        expect(page.locator("#v-queue")).to_be_visible()
        b.close()
    post(f"{MOCK}/mock/reset")
    if errors:
        print("KONSOLFEJL:", *sorted(set(errors)), sep="\n  ")
        sys.exit(1)
    print("Røgtest OK")


if __name__ == "__main__":
    main()
