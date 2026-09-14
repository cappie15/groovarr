"""Typed exceptions for the Jellyfin integration. Callers branch on these
rather than parsing HTTP status codes/messages themselves.
"""


class JellyfinError(Exception):
    """Base class for all Jellyfin-integration errors."""


class JellyfinAuthError(JellyfinError):
    """The configured API key was rejected (HTTP 401/403)."""


class JellyfinConnectionError(JellyfinError):
    """Could not reach the configured Jellyfin server at all (DNS/timeout/
    connection refused) — distinct from an auth failure so the UI can tell
    "wrong URL" from "wrong API key".
    """


class JellyfinNotFoundError(JellyfinError):
    """A referenced item/playlist/user does not exist on the server."""


class JellyfinMissingUserError(JellyfinError):
    """No Jellyfin user is configured in Settings. Playlist mutation calls
    require an explicit, real userId — Jellyfin throws a Guid.Empty error
    when only an API key is supplied with no resolvable user (confirmed
    issue #12999) — so Groovarr refuses to even attempt the call rather than
    let that opaque server-side error surface.
    """


class JellyfinPlaylistsDirectoryMissingError(JellyfinError):
    """A fresh/Docker Jellyfin install can throw `ArgumentException:
    parentFolder` on the very first playlist creation if its `Playlists`
    directory doesn't exist yet (confirmed issue #14025). This is a
    remote-server quirk Groovarr cannot fix itself — surfaced distinctly so
    the operator gets an actionable message (create the folder once /
    restart Jellyfin) instead of a generic 500 being logged.
    """


class JellyfinScanTimeoutError(JellyfinError):
    """The library-scan scheduled task did not return to Idle within the
    bounded poll budget. Not necessarily a real failure (10.11.x can trigger
    a full rescan from one new file, and large flat folders scan slowly per
    the researched issues) — callers should treat this as "still pending",
    not fatal, and leave the sync retryable later.
    """
