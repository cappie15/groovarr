# Groovarr backend

Python/FastAPI backend for Groovarr. This is the **Phase 1 (Foundation)** skeleton —
see `docs/00-research-and-architecture-review.md` §6/§11 for the full intended shape
and phase plan. Only `GET /health` is implemented as a real endpoint; every
`app/integrations/*`, `app/matching/`, `app/jobs/`, `app/services/`, and
`app/domain/` package exists only as an empty, documented placeholder for later
phases.

## Requirements

- Python 3.12+
- (Debian/Ubuntu) the `python3.12-venv` system package, to create a venv at all

## Setup

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # then fill in GROOVARR_SECRET_KEY at minimum
```

`GROOVARR_SECRET_KEY` is the only required setting — the app refuses to start
without it (it will later derive the at-rest encryption key for stored
Spotify/Jellyfin/Plex credentials).

## Running

```bash
# apply migrations (creates {CONFIG_DIR}/groovarr.db, default CONFIG_DIR=/config)
.venv/bin/alembic upgrade head

# run the API
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
# or: .venv/bin/python -m app.main
```

```bash
curl http://localhost:8080/health
# {"status":"ok","version":"0.1.0","db":"ok"}
```

`/health`'s `db` field is a real connectivity check (`SELECT 1` against the
configured SQLite database), not a hardcoded value — it reports `"error"` if
the database can't be reached.

Logs are structured JSON on stdout (via `structlog`), including uvicorn's own
access/error logs, which are routed through the same formatter. SIGTERM/SIGINT
are handled by uvicorn's default signal handling, which drives the FastAPI
lifespan's shutdown phase (see `app/main.py`) — the DB engine's connection pool
is disposed cleanly on shutdown.

## Configuration

All configuration is environment variables, loaded via `pydantic-settings`
(`app/core/config.py`); see `.env.example` for the full list:

| Variable | Default | Notes |
|---|---|---|
| `CONFIG_DIR` | `/config` | Created automatically if missing. Holds `groovarr.db`. |
| `MEDIA_DIR` | `/music-videos` | Final library location (not used yet in Phase 1). |
| `DOWNLOADS_DIR` | `/downloads` | Working/staging area (not used yet in Phase 1). |
| `PORT` | `8080` | |
| `LOG_LEVEL` | `INFO` | |
| `GROOVARR_SECRET_KEY` | *(required)* | Secret used later for at-rest credential encryption. |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | *(unset)* | Unused placeholders until Phase 2. |
| `YOUTUBE_API_KEY` | *(unset)* | Unused placeholder until Phase 4. |

## Database / migrations

SQLAlchemy 2.0 async + Alembic, targeting `sqlite+aiosqlite:///{CONFIG_DIR}/groovarr.db`.
Alembic's `app/db/migrations/env.py` derives the DB URL from `Settings` at
runtime rather than from `alembic.ini`, so the app and Alembic can never point
at two different databases.

```bash
.venv/bin/alembic revision --autogenerate -m "message"   # after adding models to app/db/models/
.venv/bin/alembic upgrade head
.venv/bin/alembic downgrade -1
```

The current baseline revision (`app/db/migrations/versions/`) is intentionally
empty — no ORM models exist yet. Real models start in Phase 3 (Domain/library).

## Tests

```bash
.venv/bin/pytest
```

`tests/conftest.py` sets `GROOVARR_SECRET_KEY` and a temp `CONFIG_DIR` before
anything imports app config/db modules (both are process-wide cached
singletons). `tests/test_health.py` drives the app in-process via
`httpx.AsyncClient` + `ASGITransport` — no running server required.

## Lint

```bash
.venv/bin/ruff check app tests
```

(Auto-generated Alembic revision scripts under `app/db/migrations/versions/`
are excluded — they follow Alembic's own template style, not ours.)

## Repository layout (this package)

```
backend/
├── app/
│   ├── api/            # FastAPI routers — only /health so far
│   ├── core/            # config.py (Settings), logging.py (structlog JSON setup)
│   ├── domain/          # entities/state machines — placeholder, Phase 3+
│   ├── db/
│   │   ├── base.py      # SQLAlchemy declarative Base
│   │   ├── session.py   # async engine/session, DB connectivity check
│   │   ├── models/      # ORM models — placeholder, Phase 3+
│   │   └── migrations/  # Alembic env + versions/
│   ├── integrations/    # spotify/youtube/acquisition/lyrics/jellyfin/plex — placeholders, Phase 2/4/5/7/8
│   ├── matching/        # scoring engine — placeholder, Phase 4
│   ├── jobs/             # job queue/worker — placeholder, Phase 5
│   ├── services/         # use-case orchestration — placeholder, later phases
│   └── main.py           # FastAPI app + lifespan
├── tests/
│   ├── unit/, integration/, fixtures/   # empty for now
│   └── test_health.py
├── alembic.ini
├── pyproject.toml
├── .env.example
└── README.md
```
