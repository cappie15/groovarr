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
# After that, drops privileges via `setpriv --init-groups` (a proper
# execve-based swap, not `su`+fork, so PID 1's identity is preserved) and
# runs Alembic migrations, then execs uvicorn as the actual, final PID 1
# so it receives SIGTERM/SIGINT directly from the container runtime and
# can shut down gracefully (see backend/app/main.py's lifespan —
# in-flight requests finish, the DB engine is disposed). The application
# process itself never runs as root, satisfying §9's non-root-runtime-user
# requirement.
#
# `setpriv --init-groups`, not `gosu`, is used deliberately: live-verified
# that `gosu groovarr:groovarr` silently drops ALL supplementary group
# membership even when /etc/group genuinely lists groovarr as a member of
# another group — which would silently break the hardware-acceleration
# device-group grant below (the running process would have R/W group
# permission on paper but no actual access). `setpriv --init-groups`
# correctly re-resolves supplementary groups from /etc/group at drop time.
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

# Hardware-accelerated transcoding (VAAPI/QSV) needs read/write access to
# the host's DRM render node(s) passed through via docker-compose's
# `devices: [/dev/dri:/dev/dri]` (see that file — commented out by
# default). The device is group-owned (typically group "render" or
# "video") by a GID that varies per host, so it can't be baked into the
# image at build time — detect it at boot and add the app user to a
# matching group, creating one if this GID isn't already named on the
# container's own /etc/group. A no-op when /dev/dri wasn't passed
# through (the common case — app/integrations/acquisition/hwaccel.py
# then correctly detects no hardware acceleration is available).
grant_device_group_access() {
    device="$1"
    [ -e "$device" ] || return 0
    gid="$(stat -c '%g' "$device")"
    [ "$gid" = "0" ] && return 0
    if ! getent group "$gid" >/dev/null 2>&1; then
        groupadd -g "$gid" "hwaccel-${gid}" 2>/dev/null || true
    fi
    group_name="$(getent group "$gid" | cut -d: -f1)"
    if ! id -nG groovarr 2>/dev/null | tr ' ' '\n' | grep -qx "$group_name"; then
        usermod -aG "$group_name" groovarr
        echo "groovarr: granted app user access to ${device} via group '${group_name}' (gid ${gid})" >&2
    fi
}

# yt-dlp PO-Token provider helper (see docker/Dockerfile stage 2 and
# docs/adr/0007-ytdlp-process-isolation.md): a small, best-effort local
# HTTP server that lets yt-dlp mitigate YouTube's anti-bot restrictions
# without cookies/login. Bound to 127.0.0.1 only — this server has no
# authentication of its own, and the upstream project's own README warns
# that exposing it beyond localhost lets any reachable client mint tokens
# and potentially worse (see the plugin's own security notice). Started
# in the background, BEFORE the final `exec` below hands off PID 1 to
# uvicorn — a background job survives that `exec` (it's a separate child
# process, not something the shell replacing itself affects) and simply
# becomes an orphan the container's init reaps normally on shutdown.
# Deliberately never allowed to fail container startup: yt-dlp's own
# plugin logs one warning and proceeds exactly as it does today if this
# is unreachable (confirmed by reading the plugin's source), so a bad
# Node install, a missing /app/potprovider (e.g. a custom image build
# that skipped that COPY), or any other startup problem here is caught
# and merely logged, never `set -e`-fatal. Takes an optional run-as
# prefix (`$SETPRIV`, or empty to run as the current user) since the two
# call sites below (root branch dropping privileges vs. an already-
# unprivileged override) need different invocations.
start_potprovider_helper() {
    run_as="$1"
    [ -x /usr/local/bin/node ] || return 0
    [ -f /app/potprovider/build/main.js ] || return 0
    (
        cd /app/potprovider
        # shellcheck disable=SC2086
        exec $run_as /usr/local/bin/node build/main.js --host 127.0.0.1 --port 4416
    ) >/tmp/potprovider.log 2>&1 &
    echo "groovarr: started yt-dlp PO-Token provider helper on 127.0.0.1:4416 (pid $!, best-effort — see /tmp/potprovider.log)" >&2
}

if [ "$(id -u)" = "0" ]; then
    fix_ownership_if_needed /config
    fix_ownership_if_needed /music-videos
    fix_ownership_if_needed /downloads

    if [ -d /dev/dri ]; then
        for dri_device in /dev/dri/*; do
            [ -e "$dri_device" ] && grant_device_group_access "$dri_device"
        done
    fi

    SETPRIV="setpriv --reuid ${APP_UID} --regid ${APP_GID} --init-groups"

    cd "$BACKEND_DIR"
    echo "groovarr: running database migrations (alembic upgrade head)..." >&2
    $SETPRIV alembic upgrade head

    start_potprovider_helper "$SETPRIV"

    echo "groovarr: starting uvicorn on 0.0.0.0:${PORT} as uid ${APP_UID}..." >&2
    # --proxy-headers makes uvicorn trust X-Forwarded-Proto/X-Forwarded-Host
    # from whatever's in front of it, so URLs Groovarr builds for itself
    # (notably the Spotify PKCE redirect_uri, which must be an exact match
    # of what the operator registered in the Spotify Dashboard) come out
    # correct when Groovarr sits behind a TLS-terminating reverse proxy —
    # required for that flow at all, since Spotify rejects a bare-LAN-IP
    # http:// redirect URI outright. --forwarded-allow-ips=* trusts this
    # from any upstream; acceptable for a single-user self-hosted app whose
    # operator controls what's allowed to reach it (§98), but an operator
    # running a genuinely untrusted network path in front of Groovarr
    # should narrow this to their actual proxy's address instead.
    exec $SETPRIV uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" \
        --proxy-headers --forwarded-allow-ips='*'
else
    # Not started as root (e.g. a custom `docker run --user` override) —
    # skip the ownership fix-up (we couldn't chown anything anyway) and run
    # directly as whoever we already are.
    cd "$BACKEND_DIR"
    echo "groovarr: running database migrations (alembic upgrade head)..." >&2
    alembic upgrade head

    start_potprovider_helper ""

    echo "groovarr: starting uvicorn on 0.0.0.0:${PORT}..." >&2
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" \
        --proxy-headers --forwarded-allow-ips='*'
fi
