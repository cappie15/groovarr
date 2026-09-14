# 0016 — Docker image architecture: single container, non-root, multi-stage

Status: Accepted
Rationale: [architecture review §4, §6](../00-research-and-architecture-review.md)

## Context

The project is Docker-first and explicitly avoids requiring multiple infrastructure containers for
a normal install — SQLite plus one application process should be enough.

## Decision

`docker/Dockerfile` is a three-stage build: stage 1 builds the frontend (`node:20-slim`, `npm run
build` → `frontend/dist`), stage 2 installs the backend's Python dependencies
(`python:3.12-slim`, `pip install --prefix=/install .`), and the final stage assembles both into a
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
- No Docker daemon was available during development, so the actual `docker build`/`docker compose
  up` flow has been verified by static analysis of the Dockerfile/compose logic and by replicating
  its build+run steps manually (frontend build, backend install, migrate, boot, curl), but never by
  an actual container run — this is the one part of the stack still awaiting a real end-to-end
  confirmation on a machine with Docker installed.
