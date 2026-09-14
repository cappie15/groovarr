# Third-Party Notices

Groovarr builds on the following third-party projects. This table tracks the project, its
license, and how Groovarr uses it. See `docs/00-research-and-architecture-review.md` §10 for the
full rationale behind each choice, and pin exact versions here as dependencies are locked during
implementation.

| Project | License | Use in Groovarr |
|---|---|---|
| MusicGrabber (`gitlab.com/g33kphr33k/musicgrabber`) | Unlicense | Architectural reference only (polling/diff model, scoring heuristic shape, LRCLIB fallback chain) — **not** its Spotify-scraping auth method. Verbatim reuse is legally unrestricted given the license. |
| yt-dlp | Unlicense | Acquisition engine, embedded as a Python library (not shelled out). |
| FFmpeg | **GPL v2+ as actually shipped** (LGPL v2.1+ only if rebuilt without `--enable-gpl`) | Remux/transcode/ffprobe validation. Always targets MP4 output; the backend code prefers `libopenh264` over `libx264` for the H.264 transcode path when both are available. Honesty note: the Docker image installs Debian's packaged `ffmpeg` (`apt-get install ffmpeg`), which is built with `--enable-gpl` and ships `libx264`, not `libopenh264` — so the ffmpeg component actually distributed in the Groovarr image is GPL v2+ today, not the LGPL-only build originally envisioned in §10. Building a custom LGPL-only ffmpeg (with `libopenh264` instead of `libx264`) remains a documented future option if staying LGPL becomes a hard requirement, but is not implemented as of Phase 5. |
| mutagen | LGPL v2.1+ | MP4/M4V tag, artwork, and lyrics-atom writing (primary/default container). |
| LRCLIB (server + API) | MIT | Sole v1 lyrics provider. |
| beets (`beetsplug/lyrics.py`, `_utils/requests.py`) | MIT | Architectural reference only — `Backend` interface shape, duration-tolerance validation, retry/backoff + rate-limit adapter pattern. |
| python-plexapi | MIT | Reference for exact Plex REST call shapes (playlist `uri=` construction, scan endpoints) — may be vendored directly or reimplemented against the same documented calls. |
| FastAPI | MIT | Backend API framework. |
| SQLAlchemy | MIT | ORM / database access layer. |
| Alembic | MIT | Database migrations. |
| React | MIT | Frontend UI framework. |
| AtomicParsley (optional) | GPL v2 | Only if invoked as a separate, optionally-installed external subprocess for MP4 cover-art edge cases mutagen can't handle cleanly — not statically linked, to scope its GPL obligations to itself. |

Exact pinned versions, upstream license text, and attribution notices will be filled in as
dependencies are locked in Phase 1 (per `docs/00-research-and-architecture-review.md` §90).
