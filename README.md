# PrintHub – selvhostet alternativ til SimplyPrint

Arkitektur:

```
CR-6 SE (BTT SKR CR6) ──USB── Raspberry Pi 3B+                    Tower (Unraid, 192.168.68.58)
                              ├─ Klipper   (printer-motor)         ├─ PrintHub :8095  (vores UI: kø, filer, kamera, styring)
                              ├─ Moonraker (API :7125)  ◄──────────┤─ Obico    :3334  (AI-fejldetektion, RTX 3060 kan bruges)
                              ├─ crowsnest (kamera /webcam)        └─ ntfy     :8090  (push)  ─► + Home Assistant webhook
                              ├─ moonraker-obico (agent) ──────────►
                              └─ Mainsail (admin/config, fallback)
```

Pi'en laver kun det, der *skal* ske ved printeren. Alt tungt kører på Tower.

---

## Del 1 – Pi'en: fra OctoPi til Klipper

> ⚠️ **Inden du starter:** Gem din nuværende Marlin `firmware.bin`, så du kan rulle tilbage. Når boardet kører Klipper, **virker CR-6 SE's touch-skærm ikke længere** – al betjening foregår via PrintHub/Mainsail (eller KlipperScreen, hvis du vil have en skærm).

### 1.1 Nyt image på SD-kortet
Det letteste er at erstatte OctoPi med **MainsailOS** (har Klipper, Moonraker, crowsnest og Mainsail forudinstalleret):

1. Raspberry Pi Imager → *Choose OS* → *Other specific-purpose OS* → *3D printing* → **MainsailOS** (vælg Pi 3-varianten).
2. Sæt hostname (fx `cr6se`), WiFi/SSH og bruger i Imagers indstillinger.
3. Boot, find IP'en i Deco-appen og giv den en **fast DHCP-reservation**.
4. Mainsail åbnes på `http://<pi-ip>`.

### 1.2 Byg og flash Klipper til BTT SKR CR6-boardet
```bash
ssh pi@<pi-ip>
cd ~/klipper
head -n 20 config/generic-bigtreetech-skr-cr6-v1.0.cfg   # læs build-instruktionerne øverst
make menuconfig     # brug præcis de indstillinger, filens header angiver
make clean && make
```
Kopiér `~/klipper/out/klipper.bin` til et tomt FAT32 microSD-kort som **`firmware.bin`**, sæt det i printeren og tænd. Når skærmen bliver sort/stille, er den flashet.

### 1.3 printer.cfg
Start fra Klippers egen BTT-config og CR-6 SE community-configs (de har strain-gauge-proben, macros osv.):
- Klipper: `config/generic-bigtreetech-skr-cr6-v1.0.cfg`
- Community: https://github.com/KoenVanduffel/CR-6_Klipper (testet på CR-6 SE med BTT CR6-board)

Find USB-stien og indsæt den i `[mcu]`:
```bash
ls /dev/serial/by-id/
```
Kalibrér derefter i rækkefølge: PID (dyse + bed) → rotation_distance (e-steps) → bed mesh → pressure advance → evt. input shaping.

### 1.4 Moonraker
Tilføj indholdet af `pi/moonraker-tilfoejelser.conf` til `~/printer_data/config/moonraker.conf` (kan gøres direkte i Mainsail → Machine). Det giver Tower adgang, slår print-historik til og konfigurerer køen.

**Om `automatic_transition`:** sæt den kun til `True`, hvis du har en måde at få printet af pladen på. Ellers lader du den være `False` og trykker ▶ på næste job i køen.

### 1.5 Kamera
crowsnest kører allerede i MainsailOS. Tilslut USB-kameraet og tjek `http://<pi-ip>/webcam/?action=stream`. Ved Pi-kamera: ret `~/printer_data/config/crowsnest.conf`.

### 1.6 Forbind til Obico (gøres efter Del 2)
```bash
cd ~ && git clone https://github.com/TheSpaghettiDetective/moonraker-obico.git
cd moonraker-obico && ./install.sh
```
Vælg **self-hosted** og angiv `http://192.168.68.58:3334`. Følg linkningen i Obico-UI'et.

---

## Del 2 – Tower (Unraid)

Kræver plugin'et **Compose Manager** (Apps → søg "Compose Manager").

### 2.1 Obico-server (AI-fejldetektion)
```bash
cd /mnt/user/appdata
git clone -b release https://github.com/TheSpaghettiDetective/obico-server.git
cd obico-server && docker compose up -d     # første build tager 15-30 min
```
- Åbn `http://192.168.68.58:3334`, opret bruger.
- Sæt site-domænet til `192.168.68.58:3334` i Django-admin (`/admin/sites/site/`) – ellers fejler linkning med Pi'en.
- Under printerens indstillinger: slå **"Pause on failure"** til og vælg følsomhed.
- Obico har en Unraid-guide og Nvidia-GPU-vejledning i deres docs (obico.io/docs/server-guides). CPU'en klarer én printer fint; GPU sparer strøm.

### 2.2 PrintHub + ntfy
1. Kopiér denne mappe til `/mnt/user/appdata/printhub` (fx via SMB-sharen).
2. Ret `docker-compose.yml`: **`MOONRAKER_URL`** (Pi'ens IP) og evt. notifikationer.
3. Compose Manager → *Add New Stack* → peg på mappen → **Compose Up** (bygger image'et).
4. Åbn `http://192.168.68.58:8095`.

På iPhone: Safari → Del → *Føj til hjemmeskærm* – så kører den som app.

---

## Notifikationer

PrintHub sender ved: start, pause, genoptag, færdig, annulleret, fejl og Klipper-shutdown (med kamerabillede ved pause/færdig/fejl).

| Kanal | Opsætning |
|---|---|
| **ntfy** | Installer ntfy-appen, abonnér på `http://192.168.68.58:8090/cr6se`. Udefra: brug Tailscale eller publicér via Cloudflare/NPM. |
| **Home Assistant** | Importér `pi/ha-automation-eksempel.yaml` som automation (webhook-id `printhub`). Så får du push via HA-appen – også udefra. |

Obico sender selv notifikationer ved AI-fejl (konfigureres i Obico → Preferences). Test PrintHubs kanaler med **Konsol → Test notifikation**.

---

## Adgang udefra
Du har Cloudflare Tunnel → NPM. To muligheder:
- **Tailscale (anbefalet, nul eksponering):** åbn bare `http://<tower-tailscale-ip>:8095`.
- **Cloudflare:** opret `print.ditdomæne.dk` → NPM → `192.168.68.58:8095`, slå *Websockets Support* til i NPM, og **sæt `APP_PASSWORD`** – helst også Cloudflare Access foran. En printer er en varmekilde; den skal ikke ligge åbent på internettet.

Kamera, thumbnails og alt andet går gennem PrintHub, så kun port 8095 skal ud.

---

## Hvad kan PrintHub
- **Printer:** live kamera (eller snapshot hvert 5. sek for at spare data), progress, forløbet/tilbage/færdig-klokkeslæt, lag, filament, pause/genoptag/annullér, nødstop, temperaturer med PLA/PETG-presets.
- **Filer:** upload (drag & drop eller fra mobilen) med valg *kun upload / læg i kø / print nu*, thumbnails fra sliceren, søgning, slet.
- **Kø:** Moonrakers egen kø – tilføj, fjern, flyt først, start/pause, ryd. "Seneste prints" med ↻ for at printe igen.
- **Konsol:** G-code-log og input, genveje (home, bed mesh, motorer fra, gem config, firmware-genstart).

**Thumbnails:** aktivér i sliceren (Orca/Prusa/SuperSlicer: *Printer settings → G-code thumbnails*, fx `32x32, 300x300`, PNG).

**Upload direkte fra sliceren:** Orca/Prusa → fysisk printer → type *OctoPrint* eller *Klipper (Moonraker)* → `http://<pi-ip>:7125`. Filerne dukker op i PrintHub med det samme.

---

## Udvikling / test lokalt
```bash
pip install -r requirements.txt
cd tests && uvicorn mock_moonraker:app --port 7125 &      # falsk printer
cd .. && MOONRAKER_URL=http://127.0.0.1:7125 uvicorn app.main:app --port 8095
```

### Miljøvariabler
| Variabel | Standard | Beskrivelse |
|---|---|---|
| `MOONRAKER_URL` | `http://printer.local:7125` | Moonraker på Pi'en |
| `MOONRAKER_API_KEY` | – | Kun hvis Tower ikke er trusted client |
| `CAMERA_STREAM_URL` / `CAMERA_SNAPSHOT_URL` | `http://<pi>/webcam/?action=…` | crowsnest |
| `PRINTER_NAME` | `CR-6 SE` | Vises i UI og notifikationer |
| `OBICO_URL` | – | Viser 🧠-knap til Obico |
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | – | ntfy-push |
| `HA_WEBHOOK_URL` | – | Home Assistant webhook |
| `APP_USER`, `APP_PASSWORD` | `admin`, – | Basic-login (tom = slået fra) |
