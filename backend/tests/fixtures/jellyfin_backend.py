"""A tiny in-memory fake of the Jellyfin API endpoints Groovarr calls, wired
up via `httpx.MockTransport` (§92, mirrors tests/fixtures/spotify_backend.py).

Deliberately reproduces real, researched/live-verified Jellyfin quirks
rather than just recording calls: (1) a POST /Playlists call with no real
UserId gets the actual documented Guid.Empty-style failure (issue #12999),
so a test proves Groovarr's client never triggers it, rather than merely
asserting it *tried* to send one; (2) `parent_folder_missing` reproduces
issue #14025's first-run ArgumentException; (3) the dedicated
`POST /Playlists/{id}` rename endpoint is modeled as ALWAYS returning 400,
because that is what a real, live Jellyfin 12.0.0 server actually did when
tested on 2026-09-14 (every request-body shape tried was rejected
identically) — Groovarr's client no longer calls that endpoint at all (see
`JellyfinClient.rename_playlist`'s docstring), so this fixture existing in
the "always fails" state is what proves the client has genuinely stopped
relying on it, not an oversight.
"""

import re

import httpx

_USER_ITEM_RE = re.compile(r"/Users/([^/]+)/Items/([^/]+)$")
_USERS_ITEMS_RE = re.compile(r"/Users/([^/]+)/Items$")
_PLAYLIST_RE = re.compile(r"/Playlists/([^/]+)$")
_ITEM_RE = re.compile(r"/Items/([^/]+)$")
_SCHEDULED_TASK_RE = re.compile(r"/ScheduledTasks/([^/]+)$")


class FakeJellyfinBackend:
    def __init__(self) -> None:
        self.users: list[dict] = [{"Id": "user-1", "Name": "ben"}]
        self.items: dict[str, dict] = {}  # item id -> {Id, Name, Path}
        self.playlists: dict[str, dict] = {}  # playlist id -> {Id, Name}
        self._next_id = 1

        self.parent_folder_missing = False
        self.scan_task_state = "Idle"
        # Number of /ScheduledTasks/{id} polls before scan_task_state (if
        # "Running") flips to "Idle" — lets a test simulate a slow-but-
        # eventually-successful scan without hanging the poll loop forever.
        self._polls_until_idle: int | None = None
        self._poll_count = 0

        self.library_refresh_count = 0
        self.create_playlist_calls: list[dict] = []

    def add_item(self, *, name: str, path: str) -> str:
        item_id = f"item-{self._next_id}"
        self._next_id += 1
        self.items[item_id] = {"Id": item_id, "Name": name, "Path": path}
        return item_id

    def set_slow_scan(self, *, polls_until_idle: int) -> None:
        self.scan_task_state = "Running"
        self._polls_until_idle = polls_until_idle
        self._poll_count = 0

    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: C901 - fake backend dispatch table
        path = request.url.path
        auth = request.headers.get("Authorization", "")
        if 'MediaBrowser Token="' not in auth:
            return httpx.Response(401, json={"error": "missing MediaBrowser auth"})

        if path == "/System/Info":
            return httpx.Response(200, json={"ServerName": "fake-jellyfin", "Version": "10.11.11"})

        if path == "/Users":
            return httpx.Response(200, json=self.users)

        if path == "/Library/Refresh":
            self.library_refresh_count += 1
            return httpx.Response(204)

        if path == "/ScheduledTasks":
            return httpx.Response(
                200,
                json=[
                    {
                        "Id": "task-1",
                        "Name": "Scan Media Library",
                        "Key": "RefreshLibrary",
                        "State": self.scan_task_state,
                    }
                ],
            )

        if match := _SCHEDULED_TASK_RE.search(path):
            if match.group(1) == "task-1":
                if self._polls_until_idle is not None:
                    self._poll_count += 1
                    if self._poll_count >= self._polls_until_idle:
                        self.scan_task_state = "Idle"
                return httpx.Response(200, json={"Id": "task-1", "State": self.scan_task_state})
            return httpx.Response(404)

        if match := _USER_ITEM_RE.search(path):
            item_id = match.group(2)
            item = self.playlists.get(item_id) or self.items.get(item_id)
            if item is None:
                return httpx.Response(404)
            return httpx.Response(200, json=item)

        if match := _USERS_ITEMS_RE.search(path):
            params = request.url.params
            include_types = params.get("IncludeItemTypes", "")
            if include_types == "Playlist":
                items = list(self.playlists.values())
            else:
                search_term = (params.get("searchTerm") or "").lower()
                items = [i for i in self.items.values() if search_term in i["Name"].lower()]
            return httpx.Response(200, json={"Items": items})

        if path == "/Playlists" and request.method == "POST":
            body = _json(request)
            self.create_playlist_calls.append(body)
            user_id = body.get("UserId")
            if not user_id or user_id == "00000000-0000-0000-0000-000000000000":
                # Reproduces the real issue #12999 failure mode.
                return httpx.Response(500, json={"error": "Guid.Empty — no resolvable user for this request"})
            if self.parent_folder_missing:
                return httpx.Response(
                    400, text="System.ArgumentException: Value cannot be null. (Parameter 'parentFolder')"
                )
            playlist_id = f"playlist-{self._next_id}"
            self._next_id += 1
            self.playlists[playlist_id] = {
                "Id": playlist_id,
                "Name": body["Name"],
                "_items": body.get("Ids", []),
                # An arbitrary extra field, unrelated to renaming, that a
                # naive partial-body rename would silently wipe out — a
                # read-modify-write rename must leave this untouched.
                "SortName": "unchanged-by-rename",
            }
            return httpx.Response(200, json={"Id": playlist_id})

        if (match := _PLAYLIST_RE.search(path)) and request.method == "POST":
            # LIVE-VERIFIED (2026-09-14, real Jellyfin 12.0.0): this
            # dedicated rename endpoint unconditionally 400s regardless of
            # body shape. Groovarr's client no longer calls it (it uses
            # GET+POST against /Items/{id} instead) — this fixture models
            # the real failure so a regression that reintroduces a call here
            # would be caught.
            return httpx.Response(400, text="Error processing request.")

        if (match := _ITEM_RE.search(path)) and request.method == "POST":
            item_id = match.group(1)
            if item_id not in self.playlists:
                return httpx.Response(404)
            body = _json(request)
            if "Name" in body:
                self.playlists[item_id]["Name"] = body["Name"]
            return httpx.Response(204)

        if (match := _ITEM_RE.search(path)) and request.method == "DELETE":
            item_id = match.group(1)
            self.playlists.pop(item_id, None)
            return httpx.Response(204)

        return httpx.Response(404, json={"error": f"unhandled path in FakeJellyfinBackend: {path}"})

    def build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _json(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content) if request.content else {}
