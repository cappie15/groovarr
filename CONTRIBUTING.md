# Contributing to Groovarr

Groovarr is a self-hosted, single-user *Arr-style music-video manager. Before diving in, skim
[`docs/00-research-and-architecture-review.md`](docs/00-research-and-architecture-review.md) (the
full research/architecture rationale) and [`docs/adr/`](docs/adr/) (short, skimmable records of
what was actually decided and built) — most "why did we do it this way" questions are answered
there already.

## Dev setup

**Backend** (Python 3.12+):

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

**Frontend** (Node 20+):

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` and `/health` to the backend (default `localhost:8080` — see
`vite.config.ts`), so run both together for a working local instance. In production, the backend
serves the built frontend itself (see [ADR 0016](docs/adr/0016-docker-image-architecture.md)) —
`npm run dev` is a dev-only convenience.

## Tests and linting

```bash
# backend, from backend/ with the venv active
pytest
ruff check .

# frontend, from frontend/
npm run build      # tsc -b && vite build
tsc --noEmit
```

The backend test suite requires `ffmpeg`/`ffprobe` on `PATH` — most integration tests (from the
acquisition/tagging/lyrics phases onward) run real ffmpeg against small generated fixture clips
rather than mocking media I/O. **Only genuinely external services are mocked in tests**: Spotify,
YouTube (Data API + yt-dlp), Jellyfin, Plex, and LRCLIB. Groovarr's own logic — file I/O, database
state, ffmpeg/mutagen calls — is exercised for real wherever practical. Keep new tests consistent
with that convention rather than mocking internal modules for convenience.

## Conventions

- **Logging**: `structlog`, structured key=value fields, not f-string messages.
- **Database**: async SQLAlchemy 2.0. Any schema change needs an Alembic migration
  (`alembic revision --autogenerate -m "..."`) — verify it round-trips (`upgrade head` →
  `downgrade -1` → `upgrade head`) before committing, including from a from-scratch database, not
  just an already-migrated one.
- **Config**: all runtime settings come through `app/core/config.py` (env vars) or the
  `AppSettings` database row (`app/services/settings_service.py`) for things a user changes at
  runtime via Settings — not both for the same value.
- **Filesystem safety**: any code that writes or deletes a file under the media root must validate
  the resolved path stays a descendant of that root (see
  [ADR 0014](docs/adr/0014-filesystem-safety.md)) — this is the one category of bug in this
  codebase with real "delete the user's media library" blast radius. PRs touching
  `app/services/deletion.py`, `app/domain/atomic_move.py`, `app/domain/reference_counting.py`, or
  anything that constructs a destination path deserve extra scrutiny and, ideally, an explicit new
  regression test for the specific case being changed.
- **Frontend API types**: `frontend/src/api/schema.ts` is generated from the backend's live
  OpenAPI schema (`npm run generate-api`, requires a running backend). Regenerate it after any
  backend API change and commit the result — it's tracked in git, not gitignored.

## Implementation history

Groovarr was built in the 10 phases documented in
[`docs/00-research-and-architecture-review.md` §11](docs/00-research-and-architecture-review.md#11-implementation-phases)
(Foundation → Spotify → Domain/library → Search/matching → Acquisition → Metadata → Lyrics → Media
servers → Lifecycle → Hardening), followed by frontend wiring passes for the same 11 required UI
sections. If you're orienting yourself in the codebase, that phase order roughly matches the
dependency order between modules (e.g. `app/services/acquisition.py` depends on
`app/matching/scoring.py`'s output, which depends on `app/integrations/spotify/`'s Track data).
