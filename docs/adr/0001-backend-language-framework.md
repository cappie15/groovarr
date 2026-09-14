# 0001 — Backend language/framework: Python 3.12 + FastAPI

Status: Accepted
Rationale: [architecture review §4](../00-research-and-architecture-review.md#4-proposed-technology-stack)

## Context

Groovarr's two most safety-critical integrations — yt-dlp (video acquisition) and mutagen
(MP4 tag/artwork writing) — are both mature, actively-maintained Python libraries with no
equally mature equivalent in other ecosystems considered.

## Decision

Backend is Python 3.12+, served by FastAPI on uvicorn. yt-dlp is embedded as a library
(`from yt_dlp import YoutubeDL`) rather than shelled out (see [ADR 0007](0007-ytdlp-process-isolation.md)),
and mutagen is the tag-writing engine (see [ADR 0010](0010-metadata-writing.md)). Async SQLAlchemy 2.0
+ Alembic handle persistence; pydantic-settings loads config from environment variables;
structlog provides structured logging throughout.

## Consequences

- Aligning the acquisition and tagging stack on one language removed an entire cross-language
  subprocess/IPC layer that a split (e.g. Node backend + Python acquisition sidecar) would have
  required.
- FastAPI's OpenAPI generation is what makes the frontend's typed API client
  (`frontend/src/api/schema.ts`, generated via `openapi-typescript`) possible without hand-written
  type duplication.
- Dev dependency footprint (`backend/pyproject.toml`): fastapi, uvicorn[standard], sqlalchemy[asyncio],
  alembic, aiosqlite, pydantic-settings, structlog, httpx, cryptography, mutagen, yt-dlp.
