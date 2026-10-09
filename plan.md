# PrintHub – plan for finpudsning

Status: v0.1 fungerer funktionelt (testet mod `tests/mock_moonraker.py`), men UI'et er en prototype.
Mål: et UI, der føles som en rigtig iOS-app (Apple Home-stil), og som kan erstatte SimplyPrint i hverdagen.

Rammer der ikke må ændres:
- Én container (FastAPI-backend + statisk frontend), ingen build-step krævet for at køre.
- Moonraker er eneste kilde til printer-data; PrintHub gemmer selv så lidt som muligt.
- Dansk UI. Mobil først (iPhone, 390 px), men skal også være god på desktop.
- Alt skal kunne testes mod mock-Moonraker uden rigtig printer.

---

## Fase 1 – Visuel finpudsning (højeste prioritet)

Problemer set på skærmbilleder af v0.1:

| # | Problem | Løsning |
|---|---|---|
| 1.1 | Emoji-ikoner i tab-bar og knapper ser billige ud og renderer forskelligt pr. platform | Inline SVG-ikonsæt (fx Lucide, kopieret ind som `<symbol>`), ens stregtykkelse |
| 1.2 | Header er trang: navn, status-pill, Obico og STOP konkurrerer | Stor titel + status under titlen; nødstop flyttes til et tydeligt men mindre dominerende sted (fx lang-tryk eller bekræftelses-sheet fra jobkort) |
| 1.3 | Kameraet fylder hele første skærm, jobinfo kræver scroll | 16:9 kamera, kompakt jobkort ovenpå/ved siden af; vigtigste info (status, %, færdig kl.) synlig uden scroll |
| 1.4 | Ingen tom-tilstand for kamera/thumbnail (sorte/grå felter) | Pæne placeholders med ikon + tekst; skjul thumbnail-boks når der ikke er en |
| 1.5 | Fillisten: tre knapper per række klemmer filnavnet | Tryk på række åbner detalje-sheet (stor thumbnail, metadata, Print / Kø / Slet); evt. swipe-handlinger |
| 1.6 | Upload-feltet er tungt og stiplet | Diskret "+ Upload"-knap i kortets header; drag-and-drop overlay på hele siden på desktop |
| 1.7 | Lang forklaringstekst i Kø-fanen | Kort hjælpetekst eller info-ikon; vis kun hvis køen er tom |
| 1.8 | Typografi og spacing er ujævn (kort-titler, stats, knapper) | Definér spacing-skala (4/8/12/16/24) og typografi-tokens; gennemgå alle kort |
| 1.9 | Tab-bar overlapper indhold på lange sider | Korrekt bund-padding inkl. safe-area; test på full-page |
| 1.10 | Temperatur-sheet er basalt | Pænere sheet med stor værdi, stepper ±5°, presets som segmenteret kontrol |
| 1.11 | Desktop-layout er bare strakt mobil | Tokolonne-layout ≥ 900 px: kamera + job til venstre, temperaturer/kø/konsol til højre |
| 1.12 | Ingen loading-/fejltilstande ud over toast | Skeleton-loaders, inline fejl, deaktiverede knapper mens et kald kører |

Acceptkriterie: skærmbilleder (lyst + mørkt, 390 px og 1280 px) af alle fire faner ser konsistente og "færdige" ud.

## Fase 2 – Funktioner der mangler ift. SimplyPrint/Mainsail

- **Temperaturgraf** (sidste 20 min fra `/server/temperature_store`, live opdateret), let SVG/canvas uden tungt bibliotek.
- **Finjustering under print:** hastighed % og flow % (M220/M221), Z-offset babystep (`SET_GCODE_OFFSET Z_ADJUST=±0.01 MOVE=1`), blæser %.
- **Exclude object** (Moonraker/Klipper `EXCLUDE_OBJECT`) med simpel 2D-visning af objekter.
- **Mapper** i filbiblioteket (navigér, opret, flyt).
- **Bevægelse/jog** (home, X/Y/Z ±) – kun når der ikke printes.
- **Obico-status i UI:** vis om AI-overvågning er aktiv og seneste detektions-score (via Obico API) i stedet for kun et link.
- **Filament/Spoolman** (valgfrit): vis aktiv spole hvis Spoolman er sat op.
- **Indstillingsside** i UI: notifikationskanaler, hvilke hændelser der notificerer, kameratilstand, presets for temperaturer (gemt i Moonrakers database-namespace `printhub`).

## Fase 3 – Notifikationer og app-følelse

- **Web Push** (VAPID) direkte fra PrintHub, så PWA'en på iPhone kan få push uden ntfy/HA.
- Progress-notifikationer (25/50/75 %) som valgfri indstilling.
- Service worker til offline-skal og hurtigere opstart; rigtige PWA-ikoner (PNG 180/192/512).
- Haptik-agtige mikrointeraktioner (knap-tryk, sheet-animationer), respekter `prefers-reduced-motion`.

## Fase 4 – Robusthed og drift

- Opdel backend i moduler (`moonraker.py`, `notify.py`, `routes/*.py`); type hints og pydantic-modeller for request bodies.
- Tests: pytest + httpx mod mock-Moonraker (status, kø, upload, notifikations-tilstandsmaskine). Playwright-smoketest der tager skærmbilleder.
- Udvid mock-Moonraker: simuleret print der skrider frem over tid, temperatur-opvarmning, fejl-scenarier.
- Bedre login end Basic Auth: simpel session-cookie-login-side (Basic Auth spiller dårligt med PWA på iOS).
- Strukturerede logs; `/healthz` der også tjekker kamera.
- CI: byg image ved push til Gitea (Gitea Actions) og publicér til Gitea container registry; Unraid trækker `:latest`.
- Unraid Community Apps-template (XML) som alternativ til Compose Manager.

---

## Prioriteret rækkefølge
1. Fase 1 (hele)
2. Fase 2: temperaturgraf, finjustering, indstillingsside
3. Fase 4: login-side, tests, modulopdeling
4. Resten af fase 2 og 3

## Definition of done pr. punkt
- Virker mod mock-Moonraker, ingen konsol-fejl i browseren.
- Skærmbilleder taget og kigget efter i lyst og mørkt tema, mobil og desktop.
- README opdateret hvis opsætning ændres.
