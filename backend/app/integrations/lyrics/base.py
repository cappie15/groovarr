"""Lyrics provider interface (§44). LRCLIB (app/integrations/lyrics/lrclib.py)
is the only v1 implementation, but the interface is kept small and separate
so a second provider could be added later without restructuring callers —
mirroring the `Backend.fetch(...)` shape cited in the beets architecture
research (§3), not the fuller multi-provider fallback-chain machinery beets
itself has, since v1 only ever needs one provider.
"""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class LyricsResult:
    kind: Literal["synced", "plain"]
    # Raw provider text: LRC-formatted (with `[mm:ss.xx]` line timestamps)
    # when kind == "synced", plain newline-separated lines otherwise.
    content: str


class LyricsBackend(Protocol):
    async def fetch(
        self, *, artist: str, title: str, album: str | None, duration_s: float
    ) -> LyricsResult | None:
        """Return the best available lyrics for one track, or `None` if
        nothing plausible was found. Must never raise for an ordinary
        "not found"/network hiccup — callers treat lyrics as strictly
        best-effort (§48) and a raised exception here would need to be
        caught anyway, so implementations should swallow their own
        transient errors and return `None` instead.
        """
        ...
