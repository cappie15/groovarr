"""Jellyfin API client.

Auth uses the `Authorization: MediaBrowser Token="..."` header scheme, not
the legacy `X-Emby-Token`/`?api_key=` forms — those can be (and, per Jellyfin
12.0's plans, eventually will be) disabled server-side (§2 row F).

Every playlist-mutation call requires an explicit, real Jellyfin `userId`.
Calling those endpoints with only an API key and no resolvable user throws a
server-side Guid.Empty error (confirmed issue #12999) — this client refuses
to even attempt such a call without a userId (`JellyfinMissingUserError`)
rather than let that opaque failure surface from Jellyfin itself.

`POST /Library/Refresh` is fire-and-forget (204, no blocking) — there is no
per-library variant, it always refreshes every configured library. Discovering
whether a newly-imported file is actually indexed afterward is inherently
best-effort in this phase: this client polls `GET /ScheduledTasks` for the
library-scan task's `State` to return to `Idle` (bounded — 10.11.x can
trigger a full rescan from a single new file, and large flat folders scan
slowly per the researched issues, so this must not hang forever), then
resolves a specific file to a Jellyfin item id via a title search filtered by
exact `Path` match. That path-matching step is a reasonable inference from
the documented `GET /Items` search behavior, not something confirmed against
a live server in this environment — flagged for a spot-check per §13.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.integrations.jellyfin.errors import (
    JellyfinAuthError,
    JellyfinConnectionError,
    JellyfinMissingUserError,
    JellyfinNotFoundError,
    JellyfinPlaylistsDirectoryMissingError,
)

# The library-scan scheduled task's well-known key in current Jellyfin
# versions. Falls back to a name-based search (see _find_library_scan_task)
# if a future/older server doesn't use this key, since this specific string
# was not independently re-confirmed against live server source in this
# session (research covered the *behavior*, not this literal constant).
_LIBRARY_SCAN_TASK_KEY = "RefreshLibrary"


@dataclass(frozen=True)
class JellyfinUser:
    id: str
    name: str


@dataclass(frozen=True)
class JellyfinPlaylist:
    id: str
    name: str


@dataclass(frozen=True)
class JellyfinItem:
    id: str
    name: str
    path: str | None


class JellyfinClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                f'MediaBrowser Token="{self._api_key}", Client="Groovarr", '
                'Device="Groovarr", DeviceId="groovarr", Version="1.0"'
            )
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(
                method, f"{self._base_url}{path}", headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise JellyfinConnectionError(f"Could not reach Jellyfin at {self._base_url}: {exc}") from exc
        if response.status_code in (401, 403):
            raise JellyfinAuthError("Jellyfin rejected the configured API key")
        if response.status_code == 404:
            raise JellyfinNotFoundError(f"Jellyfin returned 404 for {method} {path}")
        if response.status_code == 500 and "Guid.Empty" in response.text:
            # Defense in depth: this should be unreachable in practice since
            # every playlist-mutation method below refuses to call at all
            # without a real userId, but translate the real server-side
            # failure mode cleanly if it ever is hit (issue #12999).
            raise JellyfinMissingUserError(
                "Jellyfin rejected this call for lacking a resolvable user (Guid.Empty, issue #12999)"
            )
        if response.status_code == 400 and "parentFolder" in response.text:
            # Issue #14025: a fresh/Docker install has no Playlists directory
            # yet on the very first playlist creation.
            raise JellyfinPlaylistsDirectoryMissingError(
                "Jellyfin has no Playlists directory yet (a known first-run issue on fresh "
                "installs, jellyfin/jellyfin#14025) — create one playlist manually in the "
                "Jellyfin web UI once, or restart the Jellyfin server, then try again."
            )
        response.raise_for_status()
        return response

    async def test_connection(self) -> None:
        await self._request("GET", "/System/Info")

    async def list_users(self) -> list[JellyfinUser]:
        response = await self._request("GET", "/Users")
        return [JellyfinUser(id=u["Id"], name=u.get("Name", "")) for u in response.json()]

    async def trigger_library_refresh(self) -> None:
        await self._request("POST", "/Library/Refresh")

    async def _find_library_scan_task_id(self) -> str | None:
        response = await self._request("GET", "/ScheduledTasks")
        tasks = response.json()
        for task in tasks:
            if task.get("Key") == _LIBRARY_SCAN_TASK_KEY:
                return task.get("Id")
        # Fall back to a name-based match — the literal task Key is not
        # something this session independently confirmed against live
        # Jellyfin source, only the ScheduledTasks API shape itself.
        for task in tasks:
            name = (task.get("Name") or "").lower()
            if "scan" in name and "librar" in name:
                return task.get("Id")
        return None

    async def get_library_scan_task_state(self) -> str | None:
        """Returns the scan task's current `State` (e.g. "Idle", "Running"),
        or None if the task couldn't be identified at all — callers should
        treat that the same as "can't confirm, not necessarily a failure".
        """
        task_id = await self._find_library_scan_task_id()
        if task_id is None:
            return None
        response = await self._request("GET", f"/ScheduledTasks/{task_id}")
        state: str | None = response.json().get("State")
        return state

    async def list_playlists(self, user_id: str) -> list[JellyfinPlaylist]:
        response = await self._request(
            "GET",
            f"/Users/{user_id}/Items",
            params={"IncludeItemTypes": "Playlist", "Recursive": "true"},
        )
        items = response.json().get("Items", [])
        return [JellyfinPlaylist(id=i["Id"], name=i.get("Name", "")) for i in items]

    async def find_item_by_path(self, user_id: str, *, search_term: str, expected_path: str) -> JellyfinItem | None:
        """Best-effort resolution of a Groovarr-imported file to its
        Jellyfin item id: search by title text, then require an exact `Path`
        match among the results (never guess — a title-only match without a
        path confirmation could easily pick the wrong item, especially for
        a popular song title).
        """
        response = await self._request(
            "GET",
            f"/Users/{user_id}/Items",
            params={
                "searchTerm": search_term,
                "Recursive": "true",
                "IncludeItemTypes": "MusicVideo,Video",
                "Fields": "Path",
            },
        )
        for item in response.json().get("Items", []):
            if item.get("Path") == expected_path:
                return JellyfinItem(id=item["Id"], name=item.get("Name", ""), path=item.get("Path"))
        return None

    async def create_playlist(self, *, user_id: str, name: str, item_ids: list[str]) -> str:
        if not user_id:
            raise JellyfinMissingUserError("Cannot create a Jellyfin playlist without a configured Jellyfin user")
        response = await self._request(
            "POST",
            "/Playlists",
            json={"Name": name, "Ids": item_ids, "UserId": user_id, "MediaType": "Video"},
        )
        result: str = response.json()["Id"]
        return result

    async def rename_playlist(self, playlist_id: str, name: str) -> None:
        await self._request("POST", f"/Playlists/{playlist_id}", json={"Name": name})

    async def delete_playlist(self, playlist_id: str) -> None:
        await self._request("DELETE", f"/Items/{playlist_id}")
