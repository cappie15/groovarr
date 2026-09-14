# 0004 — SQLite concurrency strategy

Status: Accepted
Rationale: [architecture review §6, §94](../00-research-and-architecture-review.md)

## Context

SQLite is the mandated v1 database (single-user, self-hosted, hundreds/thousands of rows, not a
multi-tenant scale problem). The app has several concurrent writers: the HTTP API, the Spotify
scheduler, the download queue's worker pool, and the upgrade monitor.

## Decision

A single process-wide async engine (`app/db/session.py`, `create_async_engine` via `aiosqlite`,
cached with `lru_cache`) shared by every component in the process — there is no per-job or
per-request separate engine. Concurrency safety comes from two things working together rather
than from SQLite-level tuning: (1) the download queue's own bounded worker count keeps write
concurrency low by design ([ADR 0003](0003-job-architecture.md)), and (2) SQLAlchemy's async
session-per-request/per-task pattern (`get_session()` dependency, `async_sessionmaker`) keeps
transactions short.

Notably, no explicit `PRAGMA journal_mode=WAL` or connection-pool tuning is set — the current
codebase relies on aiosqlite/SQLAlchemy defaults. This was sufficient to pass the full test suite
(235 tests, including concurrent-download and crash-recovery scenarios) but is the one item in
this ADR set most worth revisiting under real multi-week production load, per the honest
uncertainty already flagged in the architecture review's own assumptions.

## Consequences

- No connection-pool exhaustion issues observed in testing at this scale.
- If write contention becomes a real problem later (e.g. Spotify sync and several downloads
  committing simultaneously under default SQLite locking), the first lever to pull is enabling WAL
  mode — not migrating off SQLite, which was explicitly out of scope for v1.
- Reference-counted deletion (`app/services/deletion.py`) deliberately re-verifies its
  zero-references check inside the same commit as the delete, closing a TOCTOU gap that would
  otherwise be a real risk under this concurrency model.
