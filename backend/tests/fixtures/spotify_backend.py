"""A tiny in-memory fake of the Spotify Web API endpoints Groovarr calls,
wired up via `httpx.MockTransport` so integration tests never touch the
network (§92). Not a test module itself — no `test_` prefix, so pytest
doesn't try to collect it.
"""

import re

import httpx

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_PLAYLIST_RE = re.compile(r"https://api\.spotify\.com/v1/playlists/([^/?]+)$")
_TRACKS_RE = re.compile(r"https://api\.spotify\.com/v1/playlists/([^/?]+)/tracks")


class FakeSpotifyBackend:
    """Configure playlists/tracks with `set_playlist`/`set_tracks`, then get
    an `httpx.AsyncClient` via `build_client()` that Groovarr's SpotifyClient
    talks to exactly as if it were the real API.
    """

    def __init__(self) -> None:
        self.playlists: dict[str, dict] = {}
        self.playlist_tracks: dict[str, list[dict]] = {}
        self.token_requests = 0
        # Live-verified (2026-09-14, against the real Spotify API): the
        # track-listing endpoint can deny Client Credentials ("app") tokens
        # even for a genuinely public playlist, while a real user
        # (Authorization Code / PKCE) token succeeds. Playlist IDs in this
        # set 403 the /tracks call unless the request carries the fixture's
        # "fake-user-token" (i.e. came from `refresh_pkce_token`).
        self.deny_tracks_for_app_token: set[str] = set()

    def set_playlist(self, playlist_id: str, *, name: str, snapshot_id: str, images: list[dict] | None = None) -> None:
        self.playlists[playlist_id] = {
            "id": playlist_id,
            "name": name,
            "snapshot_id": snapshot_id,
            "images": images or [],
            "public": True,
            "collaborative": False,
        }

    def set_tracks(self, playlist_id: str, tracks: list[dict]) -> None:
        self.playlist_tracks[playlist_id] = tracks

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if url.startswith(_TOKEN_URL):
            self.token_requests += 1
            is_user_token = "grant_type=refresh_token" in (request.content or b"").decode("utf-8", "ignore")
            token = "fake-user-token" if is_user_token else "fake-app-token"
            return httpx.Response(200, json={"access_token": token, "token_type": "Bearer", "expires_in": 3600})

        base = url.split("?")[0]
        if match := _TRACKS_RE.match(base):
            playlist_id = match.group(1)
            tracks = self.playlist_tracks.get(playlist_id)
            if tracks is None:
                return httpx.Response(404, json={"error": {"status": 404, "message": "Not found"}})
            auth = request.headers.get("authorization", "")
            if playlist_id in self.deny_tracks_for_app_token and auth != "Bearer fake-user-token":
                return httpx.Response(403, json={"error": {"status": 403, "message": "Forbidden"}})
            items = [{"track": t} for t in tracks]
            return httpx.Response(200, json={"items": items, "next": None})

        if match := _PLAYLIST_RE.match(base):
            playlist_id = match.group(1)
            data = self.playlists.get(playlist_id)
            if data is None:
                return httpx.Response(404, json={"error": {"status": 404, "message": "Not found"}})
            return httpx.Response(200, json=data)

        return httpx.Response(404, json={"error": f"unhandled path in FakeSpotifyBackend: {url}"})

    def build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def playlist_id(n: int) -> str:
    """A deterministic, regex-valid (22 alnum chars) fake Spotify playlist ID."""
    return f"PID{n:019d}"


def make_track(
    spotify_track_id: str,
    name: str,
    *,
    artists: list[str] | None = None,
    duration_ms: int = 200_000,
    explicit: bool = False,
    release_date: str = "2020-01-01",
) -> dict:
    return {
        "id": spotify_track_id,
        "name": name,
        "duration_ms": duration_ms,
        "explicit": explicit,
        "is_local": False,
        "artists": [{"name": a} for a in (artists or ["Test Artist"])],
        "album": {"release_date": release_date, "release_date_precision": "day"},
    }
