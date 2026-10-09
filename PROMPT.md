Du skal videreudvikle PrintHub i dette repo: en selvhostet web-frontend til min 3D-printer, der kører som én Docker-container på min Unraid-server.

Kontekst:
- Printer: Creality CR-6 SE med BigTreeTech SKR CR6-board, Klipper + Moonraker på en Raspberry Pi 3B+.
- PrintHub (FastAPI i `app/main.py`, vanilla HTML/CSS/JS i `app/static/index.html`) taler med Moonrakers HTTP-API og websocket og proxyer kameraet (crowsnest).
- AI-fejldetektion leveres af self-hosted Obico; PrintHub linker kun til den i dag.
- Notifikationer går til ntfy og en Home Assistant-webhook.
- Opsætning er beskrevet i `README.md`.

Læs først `README.md` og `plan.md`. `plan.md` er arbejdsplanen; følg dens prioriterede rækkefølge og start med Fase 1 (visuel finpudsning).

Arbejdsform:
1. Start mock-printeren (`cd tests && uvicorn mock_moonraker:app --port 7125`) og PrintHub (`MOONRAKER_URL=http://127.0.0.1:7125 uvicorn app.main:app --port 8095`). Jeg har ingen rigtig printer tilgængelig for dig, så alt testes mod mocken. Udvid mocken, når en ny funktion kræver det.
2. Tag skærmbilleder med Playwright (390×844 og 1280×800, lyst og mørkt tema) af alle faner før du ændrer noget, og igen efter hver fase. Kig selv på billederne og ret det, der ser forkert ud.
3. Arbejd én fase ad gangen. Når en fase er færdig: kort opsummering af hvad der er ændret, skærmbilleder, og commit med en beskrivende besked. Spørg mig før du går videre til næste fase.
4. Hvis et punkt i planen viser sig at være en dårlig idé eller kræver et valg fra mig, så sig det i stedet for at gætte.

Rammer:
- Behold én container uden build-step (ingen React/Vite/npm-bundling). Små vendored biblioteker eller inline SVG er fine.
- UI-tekster på dansk. Mobil først, men desktop skal også se gennemtænkt ud. Stilen skal minde om Apple Home: rolig, luftig, store tal, bløde kort.
- Moonraker er eneste datakilde. Gem PrintHub-indstillinger i Moonrakers database (namespace `printhub`), ikke i lokale filer.
- Bryd ikke de eksisterende miljøvariabler i `docker-compose.yml` uden at opdatere README.
- Nødstop og annullering skal altid kræve bekræftelse.
