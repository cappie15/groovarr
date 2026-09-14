#!/bin/sh
# Groovarr container entrypoint.
#
# Starts as root (the image has no build-time USER directive) specifically
# so it can fix ownership of the bind-mounted volumes on first run: a plain
# `docker compose up` creates ./config, ./music-videos, ./downloads on the
# host as root:root if they don't already exist, which the fixed-uid
# non-root `groovarr` user (10001:10001) can't write to — without this step
# the very first boot fails with "unable to open database file". Ownership
# is only ever corrected, never assumed pre-existing.
#
# After that, drops privileges via `gosu` (a proper execve-based swap, not
# `su`+fork, so PID 1's identity is preserved) and runs Alembic migrations,
# then execs uvicorn as the actual, final PID 1 so it receives
# SIGTERM/SIGINT directly from the container runtime and can shut down
# gracefully (see backend/app/main.py's lifespan — in-flight requests
# finish, the DB engine is disposed). The application process itself never
# runs as root, satisfying §9's non-root-runtime-user requirement.
#
# Per docs/00-research-and-architecture-review.md §4/§6: SQLite + Alembic,
# `app.main:app` served by uvicorn, config/db under $CONFIG_DIR (default
# /config, matching the docker-compose ./config volume).

set -eu

BACKEND_DIR="${GROOVARR_BACKEND_DIR:-/app/backend}"
PORT="${PORT:-8080}"
APP_UID=10001
APP_GID=10001

fix_ownership_if_needed() {
    dir="$1"
    [ -d "$dir" ] || return 0
    owner_uid="$(stat -c '%u' "$dir")"
    if [ "$owner_uid" != "$APP_UID" ]; then
        echo "groovarr: fixing ownership of ${dir} (was uid ${owner_uid}, want ${APP_UID})..." >&2
        chown -R "${APP_UID}:${APP_GID}" "$dir"
    fi
}

if [ "$(id -u)" = "0" ]; then
    fix_ownership_if_needed /config
    fix_ownership_if_needed /music-videos
    fix_ownership_if_needed /downloads

    cd "$BACKEND_DIR"
    echo "groovarr: running database migrations (alembic upgrade head)..." >&2
    gosu groovarr:groovarr alembic upgrade head

    echo "groovarr: starting uvicorn on 0.0.0.0:${PORT} as uid ${APP_UID}..." >&2
    exec gosu groovarr:groovarr uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
else
    # Not started as root (e.g. a custom `docker run --user` override) —
    # skip the ownership fix-up (we couldn't chown anything anyway) and run
    # directly as whoever we already are.
    cd "$BACKEND_DIR"
    echo "groovarr: running database migrations (alembic upgrade head)..." >&2
    alembic upgrade head

    echo "groovarr: starting uvicorn on 0.0.0.0:${PORT}..." >&2
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
fi
