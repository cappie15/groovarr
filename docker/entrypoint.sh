#!/bin/sh
# Groovarr container entrypoint.
#
# Runs Alembic migrations against the SQLite DB under /config, then execs
# uvicorn as PID 1's replacement (not a backgrounded/wrapped child) so it
# receives SIGTERM/SIGINT directly from the container runtime and can shut
# down gracefully (see backend/app/main.py's lifespan — in-flight requests
# finish, the DB engine is disposed). Doesn't drop privileges itself: the
# image's final stage already runs this script as the non-root `groovarr`
# user via Dockerfile's USER directive, so there's nothing to de-escalate
# here.
#
# Per docs/00-research-and-architecture-review.md §4/§6: SQLite + Alembic,
# `app.main:app` served by uvicorn, config/db under $CONFIG_DIR (default
# /config, matching the docker-compose ./config volume).

set -eu

BACKEND_DIR="${GROOVARR_BACKEND_DIR:-/app/backend}"
PORT="${PORT:-8080}"

cd "$BACKEND_DIR"

echo "groovarr: running database migrations (alembic upgrade head)..." >&2
alembic upgrade head

echo "groovarr: starting uvicorn on 0.0.0.0:${PORT}..." >&2
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
