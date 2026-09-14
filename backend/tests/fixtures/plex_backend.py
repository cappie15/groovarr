"""A tiny in-memory fake of the Plex Media Server API endpoints Groovarr
calls, wired up via `httpx.MockTransport` (§92).

Deliberately reproduces Plex's own server-side item deduplication on
playlist creation/add (§2 row B) — `create_playlist` stores only the unique
ratingKeys it receives, in first-occurrence order — so a test can assert
Groovarr's `duplicates_collapsed_count` computation against real (faked)
Plex behavior rather than an assumption about it.
"""

import re

import httpx

_SECTION_ALL_RE = re.compile(r"/library/sections/([^/]+)/all$")
_SECTION_REFRESH_RE = re.compile(r"/library/sections/([^/]+)/refresh$")
_PLAYLIST_RE = re.compile(r"/playlists/([^/]+)$")
_PLAYLIST_ITEMS_RE = re.compile(r"/playlists/([^/]+)/items$")


class FakePlexBackend:
    def __init__(self, *, machine_identifier: str = "fake-machine-id") -> None:
        self.machine_identifier = machine_identifier
        self.sections: list[dict] = [{"key": "1", "title": "Music", "type": "artist"}]
        self.section_items: dict[str, list[dict]] = {}  # section_id -> [{ratingKey, title, file}]
        self.playlists: dict[str, dict] = {}  # ratingKey -> {ratingKey, title, items: [ratingKey,...]}
        self._next_rating_key = 100
        self.refresh_calls: list[tuple[str, str | None]] = []

    def add_item(self, section_id: str, *, title: str, file_path: str) -> str:
        rating_key = str(self._next_rating_key)
        self._next_rating_key += 1
        self.section_items.setdefault(section_id, []).append(
            {"ratingKey": rating_key, "title": title, "file": file_path}
        )
        return rating_key

    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: C901 - fake backend dispatch table
        path = request.url.path
        if request.headers.get("X-Plex-Token") != "fake-token":
            return httpx.Response(401, json={"error": "bad token"})

        if path == "/":
            return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": self.machine_identifier}})

        if path == "/library/sections" and request.method == "GET":
            return httpx.Response(200, json={"MediaContainer": {"Directory": self.sections}})

        if (match := _SECTION_REFRESH_RE.search(path)) and request.method == "GET":
            section_id = match.group(1)
            self.refresh_calls.append((section_id, request.url.params.get("path")))
            return httpx.Response(200)

        if (match := _SECTION_ALL_RE.search(path)) and request.method == "GET":
            section_id = match.group(1)
            title_filter = request.url.params.get("title")
            items = self.section_items.get(section_id, [])
            if title_filter:
                items = [i for i in items if i["title"] == title_filter]
            metadata = [
                {"ratingKey": i["ratingKey"], "title": i["title"], "Media": [{"Part": [{"file": i["file"]}]}]}
                for i in items
            ]
            return httpx.Response(200, json={"MediaContainer": {"Metadata": metadata}})

        if path == "/playlists" and request.method == "GET":
            metadata = [
                {"ratingKey": p["ratingKey"], "title": p["title"], "smart": False} for p in self.playlists.values()
            ]
            return httpx.Response(200, json={"MediaContainer": {"Metadata": metadata}})

        if path == "/playlists" and request.method == "POST":
            uri = request.url.params.get("uri", "")
            keys = _keys_from_uri(uri)
            unique_keys = list(dict.fromkeys(keys))  # Plex's own real dedup behavior
            rating_key = str(self._next_rating_key)
            self._next_rating_key += 1
            self.playlists[rating_key] = {
                "ratingKey": rating_key,
                "title": request.url.params.get("title", ""),
                "items": unique_keys,
            }
            return httpx.Response(200, json={"MediaContainer": {"Metadata": [{"ratingKey": rating_key}]}})

        if (match := _PLAYLIST_ITEMS_RE.search(path)) and request.method == "GET":
            rating_key = match.group(1)
            playlist = self.playlists.get(rating_key)
            if playlist is None:
                return httpx.Response(404)
            metadata = [{"ratingKey": k} for k in playlist["items"]]
            return httpx.Response(200, json={"MediaContainer": {"Metadata": metadata}})

        if (match := _PLAYLIST_RE.search(path)) and request.method == "PUT":
            rating_key = match.group(1)
            if rating_key not in self.playlists:
                return httpx.Response(404)
            if title := request.url.params.get("title"):
                self.playlists[rating_key]["title"] = title
            return httpx.Response(200)

        if (match := _PLAYLIST_RE.search(path)) and request.method == "DELETE":
            self.playlists.pop(match.group(1), None)
            return httpx.Response(200)

        return httpx.Response(404, json={"error": f"unhandled path in FakePlexBackend: {path}"})

    def build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _keys_from_uri(uri: str) -> list[str]:
    tail = uri.rsplit("/", 1)[-1]
    return tail.split(",") if tail else []
