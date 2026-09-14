"""Metadata tag-writing (Phase 6, §42/§43 of the architecture doc): embeds
Spotify-canonical metadata and artwork into the final, always-MP4 media
file. Never uses YouTube's own title/channel text — Spotify's data is the
canonical source, YouTube text is noisy (§42).
"""
