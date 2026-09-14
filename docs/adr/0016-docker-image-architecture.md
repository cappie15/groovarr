# 0016 — Docker image architecture: single container, non-root, multi-stage

Status: Accepted
Rationale: [architecture review §4, §6](../00-research-and-architecture-review.md)

## Context

The project is Docker-first and explicitly avoids requiring multiple infrastructure containers for
a normal install — SQLite plus one application process should be enough.

## Decision

`docker/Dockerfile` is a multi-stage build: one stage builds the frontend (`node:20-slim`, `npm
run build` → `frontend/dist`), another installs the backend's Python dependencies
(`python:3.12-slim`, `uv export` + `uv pip install --prefix=/install` from `backend/uv.lock` for a
reproducible, hash-verified install — see CONTRIBUTING.md's "Dependency locking" section), and the
final stage assembles both into a
minimal `python:3.12-slim` runtime image, running as a fixed-uid non-root user, with `ffmpeg`
installed via apt (see [ADR 0008](0008-ffmpeg-strategy.md) for the resulting license
consequence), a `HEALTHCHECK` against `/health`, and `docker/entrypoint.sh` running `alembic
upgrade head` before `exec`-ing uvicorn (so it receives `SIGTERM`/`SIGINT` directly for graceful
shutdown). `backend/app/main.py` mounts the built `frontend/dist` via `StaticFiles` plus a
SPA-fallback route, so this one container serves both the API and the UI — no separate frontend
service, matching `docker/docker-compose.yml`'s single `groovarr` service.

## Consequences

- The `/config`, `/music-videos`, and `/downloads` volumes are the only state that needs to
  survive a container recreation — everything else is rebuilt from the image.
- Originally, no Docker daemon was available during development, so the `docker build`/`docker
  compose up` flow could only be verified by static analysis and by replicating its build+run
  steps manually, never by an actual container run. That gap has since closed: a real `docker
  build -f docker/Dockerfile .` (all stages, including the lockfile-driven backend-build change
  above) and a real container run — migrations applying, uvicorn booting, `/health` responding
  `{"status":"ok",...}`, Docker's own `HEALTHCHECK` reporting `healthy` — have both been confirmed
  end-to-end, and a live `groovarr` container built from this Dockerfile now runs continuously
  with a real connected Spotify account and Jellyfin server.
