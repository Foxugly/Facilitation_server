# Facilitation_server — Collaborative Facilitation Toolbox (backend)

Django + DRF + **Django Channels** (ASGI/WebSocket) backend for **facilitation.foxugly.com**,
the Collaborative Facilitation Toolbox. Forked from `Poker_server` — Delegation Poker is
the first activity, not the product. Part of the Foxugly fleet — conventions in `foxugly-ops/OPERATIONS.md`.

> **Fleet exception:** this is the only site that runs under an **ASGI** server (daphne),
> not gunicorn/WSGI, because Channels needs it (README-handoff §4). The realtime brick is
> isolated in the `realtime` app + `config/asgi.py`.

## Stack

Django 6 · DRF · Channels + Redis · django-parler (i18n in DB) · Celery · PostgreSQL (prod) ·
simplejwt (Phase 2 auth). Python 3.14, port `127.0.0.1:8009`.

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
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py runserver   # ASGI via daphne (Channels)
```

Dev uses sqlite + the in-memory channel layer (no Redis needed). Tests: `.venv/Scripts/python -m pytest`.

## Docs

- `docs/superpowers/specs/README-handoff-claude-code.md` — index + conventions de flotte. À lire en premier.
- `docs/superpowers/specs/delegation-poker-realtime-contract.md` — contrat WebSocket.
- `docs/superpowers/specs/2026-07-08-data-model.md` — modèle de données détaillé (livrable n°0).
- `CLAUDE.md` — état du dépôt, vocabulaire, cible, écarts connus et pièges.
