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

This gets you a working dev environment fast, but `pyproject.toml`'s dependencies are deliberately
loose ranges (e.g. `fastapi>=0.115,<1.0`) so day-to-day installs aren't forever pinned to whatever
was newest the day a dependency was added — which means a plain `pip install -e .` run today can
resolve different exact versions than the same command run last month (this has already caused
real, if minor, version drift during this project's own development). `backend/uv.lock` exists to
make *reproducible* installs (CI, Docker image builds, "why does it work on my machine")
possible on top of those ranges — see below.

## Dependency locking (`uv.lock`)

The backend dependency set is locked with [`uv`](https://docs.astral.sh/uv/) into
`backend/uv.lock`, which pins every dependency (direct and transitive) to an exact,
hash-verified version consistent with the ranges in `pyproject.toml`. `docker/Dockerfile`'s
backend-build stage installs from this lockfile (via `uv export`), so a production image build is
byte-for-byte reproducible against a given commit rather than re-resolving ranges at build time.

**Why `uv` over `pip-tools`**: this is already a `pyproject.toml`-based package (not a bare
`requirements.txt` project), and `uv lock` operates directly on it with one command and no
separate `requirements.in`/`requirements.txt` split to keep in sync — less ceremony than
`pip-compile` for the same guarantee. It's also simply fast (the initial lock and every
`uv sync` in this repo took well under a second). Nothing else in the project depends on `uv`
day-to-day — the venv/`pip install -e ".[dev]"` workflow above still works unchanged; `uv` is an
added reproducibility layer, not a replacement for it.

Install `uv` once (see [astral.sh/uv/install](https://docs.astral.sh/uv/getting-started/installation/)):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Adding or updating a dependency**:

1. Edit the version range in `backend/pyproject.toml` as usual (`[project.dependencies]` or the
   `dev` extra).
2. Regenerate the lock from `backend/`:

   ```bash
   cd backend
   uv lock
   ```

3. Verify the new lock actually installs and the test suite passes against it:

   ```bash
   uv sync --frozen --extra dev
   source .venv/bin/activate   # if not already using uv run
   pytest
   ruff check .
   ```

4. Commit both the `pyproject.toml` change and the updated `uv.lock` together — a `pyproject.toml`
   change without a matching lock update means the Docker image build (which uses `--frozen` and
   fails outright on drift) is now out of date with the source of truth.

`uv sync --frozen` (used above and by CI) refuses to install if `pyproject.toml` and `uv.lock`
have diverged, rather than silently re-resolving — that mismatch is exactly the failure mode this
lockfile exists to catch.

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
