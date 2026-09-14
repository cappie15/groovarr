"""Typed exceptions for the YouTube discovery/enrichment integration."""


class YouTubeError(Exception):
    """Base class for all YouTube-integration errors."""


class YouTubeDiscoveryUnavailableError(YouTubeError):
    """Neither the YouTube Data API (no key configured, or the call failed/
    quota-exhausted) nor the yt-dlp `ytsearch:` fallback could produce
    results. Callers should treat this the same as "zero candidates found"
    rather than crashing the whole search — see app/services/search.py.
    """
