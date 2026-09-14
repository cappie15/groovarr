# 0010 — Metadata writing: mutagen, Spotify-canonical data only

Status: Accepted
Rationale: [architecture review §3 (MP4 metadata findings), §42](../00-research-and-architecture-review.md)

## Context

YouTube titles are noisy presentation strings and must never overwrite clean Spotify metadata.
Research confirmed mutagen is the most complete, battle-tested library for MP4 iTunes-atom tag
writing, including artwork and the `rtng` (explicit/clean advisory) atom whose mutagen support was
an open question in the original research and needed direct confirmation.

## Decision

`app/integrations/tagging/mp4_tags.py`'s `write_mp4_tags`/`apply_tags_best_effort` write `©nam`
(title), `©ART` (artist — `format_artist_tag` combines primary + featured artists into one string,
since MP4's `©ART` atom is a single text field), `©day` (year), `©lyr` (plain-text lyrics, a
best-effort bonus — see [ADR 0011](0011-lyrics-providers.md)), `covr` (artwork, sniffed
JPEG/PNG), and `rtng` (confirmed writable via mutagen's high-level API by a direct round-trip
test, `_RTNG_EXPLICIT`/`_RTNG_CLEAN` — resolved, not left as a TODO). Every field is sourced from
the Track/SpotifyPlaylist data already in the database, never from `VideoCandidate`'s YouTube
title/channel text.

## Consequences

- `apply_tags_best_effort` never raises — a tagging failure is caught, logged, and recorded via
  `MediaAsset.tags_written`, and never fails the overall import (mirrors the same
  errors-must-not-block-import principle used for lyrics).
- ffprobe re-validates the file after tagging to confirm mutagen's write didn't disturb the
  audio/video streams (tag-writing should never re-encode, but this is verified, not assumed).
