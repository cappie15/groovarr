# syntax=docker/dockerfile:1
#
# Groovarr container image.
#
# Conventions per docs/00-research-and-architecture-review.md §4/§6:
#   - Backend: Python 3.12+, FastAPI, run via `uvicorn app.main:app`.
#   - Frontend: React/TS/Vite, built to static assets and shipped in the same
#     single container (no separate frontend service — §4's stated
#     preference against extra infra containers).
#   - Single process, single container; SQLite DB + Alembic migrations live
#     under the bind-mounted /config volume.
#
# Three stages: build the frontend, install the backend's Python
# dependencies, then assemble a minimal non-root final image from both.

# ---------------------------------------------------------------------------
# Stage 1: frontend build
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-build

WORKDIR /src/frontend

# Copy lockfile + manifest first so `npm install` is cached independently of
# source changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build
# -> /src/frontend/dist (per frontend/vite.config.ts `build.outDir`)

# ---------------------------------------------------------------------------
# Stage 2: backend dependency build
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS backend-build

WORKDIR /src/backend

# Copy only what's needed to resolve+build the package (pyproject.toml +
# the `app` package setuptools packages onto, per
# `[tool.setuptools.packages.find] include = ["app*"]`) before pulling in
# the rest of the backend tree, so dependency installation layers cache
# independently of day-to-day app code changes.
COPY backend/pyproject.toml ./
COPY backend/app ./app

# Install into a throwaway prefix so only the resulting site-packages (not
# pip's cache, build tooling, etc.) gets copied into the final stage.
# Runtime deps only — the `dev` extra (pytest/ruff/httpx) is intentionally
# left out of the shipped image.
RUN pip install --no-cache-dir --prefix=/install .

# ---------------------------------------------------------------------------
# Stage 3: final runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS final

ARG PORT=8080
ENV PORT=${PORT} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GROOVARR_BACKEND_DIR=/app/backend

# Phase 5 (Acquisition) shells out to `ffmpeg`/`ffprobe` for remuxing,
# transcoding, and stream validation — required at runtime, not just for
# dev/tests. NOTE ON LICENSING (§10/THIRD_PARTY_NOTICES.md): Debian's
# packaged ffmpeg is built with --enable-gpl (it ships libx264), so this
# image's ffmpeg component is GPL v2+, not the LGPL-only build the
# architecture doc discusses as the ideal — see THIRD_PARTY_NOTICES.md for
# the honest license status of what's actually shipped here.
#
# The entrypoint's privilege-drop step (see docker/entrypoint.sh) uses
# `setpriv` (from util-linux, already present in this base image — no
# extra package needed) rather than `gosu`: the container starts as root
# so it can fix ownership of freshly-created bind-mounted host volumes on
# first run (a plain `docker compose up` on an operator's host creates
# ./config, ./music-videos, ./downloads as root:root — a fixed-uid
# non-root USER can't write to those) and, when hardware-accelerated
# transcoding device passthrough (/dev/dri) is configured, add the app
# user to whatever host-specific group owns those devices — then execs
# into the unprivileged `groovarr` user via `setpriv --init-groups` (a
# proper execve-based swap that correctly re-initializes supplementary
# groups for that dynamic membership, which `gosu` does NOT do — verified
# live: gosu silently drops supplementary groups even when the target
# user's /etc/group membership is genuinely present, which would silently
# break device-group-based hardware-acceleration access).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Fixed numeric uid/gid so bind-mounted host volumes can be chowned
# predictably by the operator (or by the entrypoint itself, see above).
# No `USER` directive here — this image intentionally starts as root and
# drops to this user at runtime via `setpriv` in entrypoint.sh, not at
# build time (§9's non-root-runtime-user requirement is still met: the
# actual application process never runs as root).
RUN groupadd --gid 10001 groovarr \
    && useradd --uid 10001 --gid groovarr --create-home --shell /usr/sbin/nologin groovarr

WORKDIR /app

# Installed Python dependencies + the backend package itself, from stage 2.
COPY --from=backend-build /install /usr/local

# Backend source tree, laid out exactly as it is in the repo (matches the
# documented run command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
# executed with cwd=backend/). Copied on disk in addition to being
# pip-installed above because Alembic resolves its migration scripts
# (`app/db/migrations/{env.py,versions/}`) relative to alembic.ini's own
# directory (`script_location = %(here)s/app/db/migrations`), not via
# Python's import system.
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/app ./backend/app

# Built frontend static assets — served by the backend's FastAPI
# StaticFiles mount (backend/app/main.py `_mount_frontend()`) at this exact
# path, resolved relative to the backend package location so it works
# identically in this container layout and in the dev repo layout.
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /config /music-videos /downloads \
    && chown -R groovarr:groovarr /app /config /music-videos /downloads

EXPOSE ${PORT}

# GET /health always returns HTTP 200 (it reports DB status *in* the body
# rather than via status code — see backend/app/api/router.py), so this
# healthcheck verifies "process is up and serving", matching that endpoint's
# own documented liveness semantics.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python3", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8080') + '/health', timeout=3)"]

ENTRYPOINT ["/app/entrypoint.sh"]
