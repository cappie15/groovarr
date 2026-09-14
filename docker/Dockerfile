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
# Stage 2: yt-dlp PO-Token provider helper (source only — never run as its
# own container; see docs/adr/0007-ytdlp-process-isolation.md).
#
# YouTube's anti-bot restrictions can 403 an otherwise-valid download (live-
# verified on this project) — yt-dlp's own documented, non-evasive mitigation
# is its PO Token Provider Framework, and `bgutil-ytdlp-pot-provider`
# (GPL-3.0, github.com/Brainicism/bgutil-ytdlp-pot-provider) is the
# community-maintained implementation it recommends. It ships as a prebuilt
# Docker image containing a small Node.js HTTP server (there is no npm
# package or single binary to install directly) — pulling the already-built
# `build/` output and its `node_modules` from that image, plus the exact
# Node runtime it was built for, avoids reimplementing its own multi-stage
# TypeScript build here. Pinned to an exact released version, not `:latest`.
FROM brainicism/bgutil-ytdlp-pot-provider:2.0.0-node AS potprovider

# ---------------------------------------------------------------------------
# Stage 3: backend dependency build
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS backend-build

# `uv` — used here purely as a fast, hash-checking installer (see
# CONTRIBUTING.md for the full dependency-locking workflow and why `uv` was
# chosen over pip-tools). Copied from Astral's own distroless image per
# their documented Docker pattern rather than `pip install uv`, so
# installing `uv` itself doesn't need network access to PyPI at a version
# pip would freely re-resolve. Pinned to the exact release used to
# generate backend/uv.lock — bump both together.
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

WORKDIR /src/backend

# Copy only what's needed to resolve+build the package (pyproject.toml +
# uv.lock + the `app` package setuptools packages onto, per
# `[tool.setuptools.packages.find] include = ["app*"]`) before pulling in
# the rest of the backend tree, so dependency installation layers cache
# independently of day-to-day app code changes.
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/app ./app

# Resolve the exact, hash-verified versions recorded in uv.lock (`--frozen`
# fails the build outright if pyproject.toml and uv.lock have drifted,
# rather than silently re-resolving) into a plain pip-style requirements
# file, then install both that and the local project into a throwaway
# prefix so only the resulting site-packages (not uv's cache, build
# tooling, etc.) gets copied into the final stage. `uv export` is the
# documented bridge from a uv project lockfile to tooling that expects a
# requirements.txt shape — needed here because the final stage's
# `--prefix=/install` layout (copied wholesale into the final image's
# `/usr/local`, see below) isn't a uv-managed venv. Runtime deps only —
# the `dev` extra (pytest/ruff/httpx) is intentionally left out of the
# shipped image.
RUN uv export --frozen --no-dev --no-emit-project -o requirements.txt \
    && uv pip install --system --no-cache --prefix=/install -r requirements.txt .

# ---------------------------------------------------------------------------
# Stage 4: final runtime image
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
# LIVE-VERIFIED GAP: ffmpeg is compiled with VAAPI/QSV encoder support, and
# `libva2`/`libvpl2` (the generic runtime shims) come along as its own
# dependencies — but neither actually WORKS without a vendor driver
# backend, which is a separate package Debian doesn't pull in
# automatically. Confirmed on this project's own real Intel iGPU: without
# these, ffmpeg fails with "unknown libva error" (VAAPI) and "Error
# creating a MFX session" (QSV) even though hwaccel.py's detection
# correctly reports the device as present — detection can only see the
# device file + compiled encoder + PCI vendor ID, not whether a driver is
# actually installed to back it.
#   - intel-media-va-driver: the open-source "iHD" VAAPI backend for
#     Intel Gen8+ (Broadwell, 2014) and newer iGPUs/dGPUs.
#   - libmfx-gen1.2: the oneVPL GPU runtime QSV needs (the Intel Media SDK
#     predecessor is end-of-life; oneVPL is the current, correct package).
#   - vainfo: a ~10 KB diagnostic CLI (`docker exec groovarr vainfo`) an
#     operator can use to confirm what their own host's driver actually
#     supports — cheap to include, useful when hardware acceleration
#     doesn't behave as expected on a different GPU/driver combination.
# AMD/older-Intel VAAPI and NVIDIA NVENC need their own vendor driver
# packages this image does not attempt to bundle (`mesa-va-drivers` for
# AMD, the host's own NVIDIA driver + Container Toolkit for NVENC per
# docker-compose.yml's existing commented-out device mapping) — adding
# every vendor's drivers unconditionally would bloat the image for
# operators who use none of them; Intel's is included because it's the
# one this project could concretely verify end-to-end.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        intel-media-va-driver \
        libmfx-gen1.2 \
        vainfo \
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

# yt-dlp PO-Token provider helper: the exact Node runtime it was built
# against (copied rather than apt-installing a possibly-mismatched Debian
# `nodejs` package) plus its built server and production node_modules.
# Best-effort only (see docker/entrypoint.sh) — the Python plugin installed
# via pyproject.toml auto-discovers this server at its default
# 127.0.0.1:4416 with zero Groovarr-side configuration; if it isn't
# running for any reason, yt-dlp logs one warning and proceeds exactly as
# it does today (confirmed by reading the plugin's own source).
COPY --from=potprovider /usr/local/bin/node /usr/local/bin/node
COPY --from=potprovider /app/build /app/potprovider/build
COPY --from=potprovider /app/node_modules /app/potprovider/node_modules

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /config /music-videos /downloads \
    && chown -R groovarr:groovarr /app /config /music-videos /downloads /app/potprovider

EXPOSE ${PORT}

# GET /health always returns HTTP 200 (it reports DB status *in* the body
# rather than via status code — see backend/app/api/router.py), so this
# healthcheck verifies "process is up and serving", matching that endpoint's
# own documented liveness semantics.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python3", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8080') + '/health', timeout=3)"]

ENTRYPOINT ["/app/entrypoint.sh"]
