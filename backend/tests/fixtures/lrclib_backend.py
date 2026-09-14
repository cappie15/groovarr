"""A tiny in-memory fake of the LRCLIB endpoints Groovarr calls, wired up
via `httpx.MockTransport` so integration tests never touch the network
(§92). Not a test module itself — no `test_` prefix.
"""

import httpx

from app.integrations.lyrics.lrclib import BASE_URL


class FakeLRCLIBBackend:
    """Configure canned responses, then get an `httpx.AsyncClient` via
    `build_client()` that LRCLIBBackend talks to exactly as if it were the
    real API. Tracks `get_requests`/`search_requests` counts so tests can
    assert on negative-cache short-circuiting (no additional HTTP calls).
    """

    def __init__(self) -> None:
        self._get_found_payload: dict | None = None
        # A queue of (status, retry_after) to return from /get, popped one
        # per request, in order — once empty, `_get_found_payload` (200) or
        # a plain 404 is returned depending on what's configured.
        self._get_status_queue: list[tuple[int, float | None]] = []
        self._search_results: list[dict] = []
        self.get_requests = 0
        self.search_requests = 0

    def set_get_not_found(self) -> None:
        self._get_found_payload = None

    def set_get_found(self, **fields) -> None:
        self._get_found_payload = fields

    def queue_get_statuses(self, statuses: list[tuple[int, float | None]]) -> None:
        """Statuses to return from the *next* N `/get` calls, in order,
        before falling back to whatever `set_get_found`/`set_get_not_found`
        configured — e.g. `[(429, 0.01), (429, 0.01)]` then a real 200 from
        `set_get_found`, to exercise retry/backoff.
        """
        self._get_status_queue = list(statuses)

    def set_search_results(self, results: list[dict]) -> None:
        self._search_results = results

    def handler(self, request: httpx.Request) -> httpx.Response:
        base = str(request.url).split("?")[0]

        if base == f"{BASE_URL}/get":
            self.get_requests += 1
            if self._get_status_queue:
                status, retry_after = self._get_status_queue.pop(0)
                headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
                return httpx.Response(status, json={"message": "simulated"}, headers=headers)
            if self._get_found_payload is not None:
                return httpx.Response(200, json=self._get_found_payload)
            return httpx.Response(404, json={"message": "not found"})

        if base == f"{BASE_URL}/search":
            self.search_requests += 1
            return httpx.Response(200, json=self._search_results)

        return httpx.Response(404, json={"error": f"unhandled path in FakeLRCLIBBackend: {base}"})

    def build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
