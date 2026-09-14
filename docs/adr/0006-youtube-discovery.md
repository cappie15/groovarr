# 0006 — YouTube discovery: Data API v3 primary, `ytsearch:` fallback

Status: Accepted
Rationale: [architecture review §2 row G](../00-research-and-architecture-review.md)

## Context

The master spec requires discovery to be decoupled from yt-dlp acquisition, and requires no
YouTube account/cookie login. `ytsearch:` (yt-dlp's own pseudo-search) is implemented inside
yt-dlp's extractor code, so using it as the primary discovery mechanism would re-couple the two
systems the spec wants separate, and inherits yt-dlp's own anti-bot blocking risk.

## Decision

`app/integrations/youtube/discovery.py`'s `discover_candidates` tries the official YouTube Data
API v3 `search.list` first (`data_api.py`, using an operator-provided `YOUTUBE_API_KEY`, no
login), and falls back to `ytsearch_discover` (`ytdlp_client.py`) only when no API key is
configured or the Data API call fails — always logged as a degraded path
(`youtube.data_api_failed_falling_back_to_ytsearch` / `youtube.no_api_key_configured_using_ytsearch_fallback`).

## Consequences

- A yt-dlp extractor break can degrade discovery to nothing (if no API key is set) but can never
  silently corrupt discovery results, since the two code paths are structurally independent.
- Real per-candidate technical enrichment (duration, `media_type`, orientation for hard-filtering
  Shorts/portrait) still goes through yt-dlp's `extract_info(download=False)` regardless of which
  discovery path found the candidate — see [ADR 0007](0007-ytdlp-process-isolation.md).
- The Data API's free quota (100 units/search, 10,000/day default) is adequate for personal-scale
  use but not unlimited bulk library imports — not addressed further in v1.
