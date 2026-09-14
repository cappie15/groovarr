# Architecture Decision Records

Short, skimmable records of what was decided and what it implies for the code. Each ADR links to
the relevant section of [`../00-research-and-architecture-review.md`](../00-research-and-architecture-review.md)
for the full research and rationale behind the decision — these files intentionally do not repeat
that reasoning, they record the outcome and its practical consequences as actually implemented.

All ADRs below are **Accepted** and implemented in the current codebase.

| ADR | Decision |
|---|---|
| [0001](0001-backend-language-framework.md) | Backend language/framework: Python 3.12 + FastAPI |
| [0002](0002-frontend-stack.md) | Frontend stack: React + TypeScript + Vite |
| [0003](0003-job-architecture.md) | Job architecture: SQLite-backed, no Redis/Celery |
| [0004](0004-sqlite-concurrency-strategy.md) | SQLite concurrency strategy |
| [0005](0005-spotify-integration.md) | Spotify integration: Client Credentials default, PKCE optional |
| [0006](0006-youtube-discovery.md) | YouTube discovery: Data API v3 primary, `ytsearch:` fallback |
| [0007](0007-ytdlp-process-isolation.md) | yt-dlp process isolation: embedded library, not shelled out |
| [0008](0008-ffmpeg-strategy.md) | FFmpeg strategy: always-MP4 output, stream-copy first |
| [0009](0009-matching-scoring.md) | Matching/scoring: deterministic weighted scoring, not ML |
| [0010](0010-metadata-writing.md) | Metadata writing: mutagen, Spotify-canonical data only |
| [0011](0011-lyrics-providers.md) | Lyrics providers: LRCLIB + `.lrc` sidecar as primary artifact |
| [0012](0012-plex-integration.md) | Plex integration and its platform limitations |
| [0013](0013-jellyfin-integration.md) | Jellyfin integration and its platform limitations |
| [0014](0014-filesystem-safety.md) | Filesystem safety: media-root validation, guarded deletion |
| [0015](0015-secrets.md) | Secrets: Fernet encryption at rest |
| [0016](0016-docker-image-architecture.md) | Docker image architecture: single container, non-root, multi-stage |
