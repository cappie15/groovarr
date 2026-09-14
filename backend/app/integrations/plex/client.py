"""Plex Media Server API client.

Auth is a single `X-Plex-Token` header (§2 row F). There is no "Music
Videos" library type in Plex (§2 row A) — `list_library_sections` returns
whatever section types actually exist (Music/artist, Other Videos/movie,
etc.); it is the caller's (Settings UI's) job to restrict the picker to a
sensible type, not this client's.

Playlist creation/item-add both go through a `uri=server://<machineIdentifier>
/com.plexapp.plugins.library/library/metadata/<ratingKey1>,<ratingKey2>,...`
parameter — order is controlled purely by the order of ratingKeys in that
string. **Plex silently deduplicates repeated items when added to a
playlist** (§2 row B) — this client does not attempt to work around that
(there is no known way to), it just reports the collapse by comparing the
requested count to what the resulting playlist actually contains, so the
caller (app/services/external_playlists.py) can record it honestly rather
than pretending full fidelity.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.integrations.plex.errors import PlexAuthError, PlexConnectionError, PlexNotFoundError


@dataclass(frozen=True)
class PlexLibrarySection:
    key: str
    title: str
    type: str  # Plex's own section type string, e.g. "artist", "movie", "photo".


@dataclass(frozen=True)
class PlexPlaylist:
    rating_key: str
    title: str
    smart: bool


@dataclass(frozen=True)
class PlexSearchResult:
    rating_key: str
    title: str
    file_path: str | None


class PlexClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._machine_identifier: str | None = None

    def _headers(self) -> dict[str, str]:
        # Requesting JSON explicitly — Plex's API defaults to XML otherwise,
        # but honors this header for a JSON response on current versions.
        return {"X-Plex-Token": self._token, "Accept": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method, f"{self._base_url}{path}", headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise PlexConnectionError(f"Could not reach Plex at {self._base_url}: {exc}") from exc
        if response.status_code == 401:
            raise PlexAuthError("Plex rejected the configured X-Plex-Token")
        if response.status_code == 404:
            raise PlexNotFoundError(f"Plex returned 404 for {method} {path}")
        response.raise_for_status()
        if not response.content:
            return {}
        result: dict[str, Any] = response.json()
        return result

    async def test_connection(self) -> None:
        await self._get_machine_identifier()

    async def _get_machine_identifier(self) -> str:
        if self._machine_identifier is None:
            data = await self._request("GET", "/")
            self._machine_identifier = data["MediaContainer"]["machineIdentifier"]
        return self._machine_identifier

    async def list_library_sections(self) -> list[PlexLibrarySection]:
        data = await self._request("GET", "/library/sections")
        directories = data.get("MediaContainer", {}).get("Directory", [])
        return [PlexLibrarySection(key=d["key"], title=d.get("title", ""), type=d.get("type", "")) for d in directories]

    async def refresh_section(self, section_id: str, *, path: str | None = None) -> None:
        params = {"path": path} if path else None
        await self._request("GET", f"/library/sections/{section_id}/refresh", params=params)

    async def list_playlists(self) -> list[PlexPlaylist]:
        data = await self._request("GET", "/playlists")
        items = data.get("MediaContainer", {}).get("Metadata", [])
        return [
            PlexPlaylist(rating_key=str(i["ratingKey"]), title=i.get("title", ""), smart=bool(i.get("smart", False)))
            for i in items
        ]

    async def search_section(self, section_id: str, *, title: str) -> list[PlexSearchResult]:
        data = await self._request("GET", f"/library/sections/{section_id}/all", params={"title": title})
        items = data.get("MediaContainer", {}).get("Metadata", [])
        results = []
        for item in items:
            file_path = None
            media = item.get("Media") or []
            if media and (parts := media[0].get("Part")):
                file_path = parts[0].get("file")
            results.append(
                PlexSearchResult(rating_key=str(item["ratingKey"]), title=item.get("title", ""), file_path=file_path)
            )
        return results

    async def create_playlist(self, *, title: str, rating_keys: list[str]) -> str:
        """Returns the new playlist's own ratingKey. `rating_keys` must be
        non-empty — Plex has no "create empty, add later" call worth relying
        on for this.
        """
        machine_id = await self._get_machine_identifier()
        uri = self._metadata_uri(machine_id, rating_keys)
        data = await self._request(
            "POST", "/playlists", params={"type": "video", "title": title, "smart": "0", "uri": uri}
        )
        metadata = data.get("MediaContainer", {}).get("Metadata", [])
        result: str = str(metadata[0]["ratingKey"])
        return result

    async def add_items(self, playlist_rating_key: str, rating_keys: list[str]) -> None:
        machine_id = await self._get_machine_identifier()
        uri = self._metadata_uri(machine_id, rating_keys)
        await self._request("PUT", f"/playlists/{playlist_rating_key}/items", params={"uri": uri})

    async def get_playlist_item_count(self, playlist_rating_key: str) -> int:
        data = await self._request("GET", f"/playlists/{playlist_rating_key}/items")
        items = data.get("MediaContainer", {}).get("Metadata", [])
        return len(items)

    async def rename_playlist(self, playlist_rating_key: str, title: str) -> None:
        await self._request("PUT", f"/playlists/{playlist_rating_key}", params={"title": title})

    async def delete_playlist(self, playlist_rating_key: str) -> None:
        await self._request("DELETE", f"/playlists/{playlist_rating_key}")

    @staticmethod
    def _metadata_uri(machine_identifier: str, rating_keys: list[str]) -> str:
        keys = ",".join(rating_keys)
        return f"server://{machine_identifier}/com.plexapp.plugins.library/library/metadata/{keys}"
