# Facilitation_server — Collaborative Facilitation Toolbox (backend)

Django + DRF + **Django Channels** (ASGI/WebSocket) backend for **facilitation.foxugly.com**,
the Collaborative Facilitation Toolbox. Forked from `Poker_server` — Delegation Poker is
the first activity, not the product. Part of the Foxugly fleet — conventions in `foxugly-ops/OPERATIONS.md`.

> **Two servers:** Channels needs ASGI, so **daphne** (`facilitation-asgi`, `127.0.0.1:8009`)
> serves only the WebSockets `/ws/`; the HTTP API (and the admin) runs on **gunicorn**
> (`facilitation-gunicorn`, `127.0.0.1:8011`) since 2026-10-09 — nginx routes `/ws/` to
> daphne and everything else to gunicorn. Celery (`facilitation-celery`, `--pool=solo`) +
> `facilitation-celery-beat` complete the units (`deploy/systemd/`). The realtime brick is
> isolated in the `realtime` app + `config/asgi.py`.

## Stack

Django 6 · DRF · Channels + Redis · django-parler (i18n in DB) · Celery · PostgreSQL (prod) ·
simplejwt (Phase 2 auth). Python 3.14 locally, 3.12 on the box; ports `127.0.0.1:8009` (daphne, `/ws/`) and
`127.0.0.1:8011` (gunicorn, HTTP API).

## Apps

| App | Role |
|-----|------|
| `accounts` | Email-only `User` (no username, §3.16) — created now, auth features are Phase 2. |
| `decks` | Editable referential: `VoteType` / `Deck` / `Card` / `TextLayer` (parler translations). |
| `rooms` | Runtime: `Room` / `Participant` / `Subject` / `VoteSession` / `Vote` / `Result` + HTTP API. |
| `realtime` | Channels consumer + domain services (state machine, authority, secret votes). |
| `health` | `/health/` (status + DB check). |

## Local dev

```bash
py -m venv .venv
.venv/Scripts/python -m pip install -r requirements.in
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py runserver   # ASGI via daphne (Channels)
```

Dependencies: `requirements.in` lists the direct deps (ranges); `requirements.txt` is the
hash-pinned lock generated from it for the box (Linux, Python 3.12) — what CI and the deploy
install (`pip install --require-hashes -r requirements.txt`). It is not usable on Windows
(platform-only deps such as `tzdata` are resolved out), hence `requirements.in` above. After
editing `requirements.in`, regenerate the lock:

```bash
uv pip compile requirements.in --generate-hashes --python-version 3.12 --python-platform x86_64-unknown-linux-gnu -o requirements.txt
```

Dev uses sqlite + the in-memory channel layer (no Redis needed). Tests: `.venv/Scripts/python -m pytest`.

## Docs

- `docs/superpowers/specs/README-handoff-claude-code.md` — index + conventions de flotte. À lire en premier.
- `docs/superpowers/specs/delegation-poker-realtime-contract.md` — contrat WebSocket.
- `docs/superpowers/specs/2026-07-08-data-model.md` — modèle de données détaillé (livrable n°0).
- `CLAUDE.md` — état du dépôt, vocabulaire, cible, écarts connus et pièges.
