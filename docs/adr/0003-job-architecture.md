# 0003 — Job architecture: SQLite-backed, no Redis/Celery

Status: Accepted
Rationale: [architecture review §76](../00-research-and-architecture-review.md), master spec §76

## Context

Groovarr must run long background work (Spotify sync, downloads, upgrade monitoring) without
blocking HTTP handlers, while staying Docker-first and avoiding extra infrastructure containers
for a normal single-user install.

## Decision

Three independent asyncio-based background components, all reading/writing state through the
same SQLite database (no Redis, no Celery):

- `app/jobs/spotify_scheduler.py` — periodic Spotify playlist sync, jittered per playlist.
- `app/jobs/download_queue.py` — bounded-concurrency download queue. A single poller claims
  `QUEUED` `MediaAsset` rows and hands each to its own worker coroutine, bounded by
  `AppSettings.max_concurrent_downloads` via a running-task-count check (not a fixed
  `asyncio.Semaphore`, since the limit is a runtime-configurable Settings value).
- `app/jobs/upgrade_monitor.py` — hourly-tick re-search pass for Incomplete/non-manually-selected
  Available assets, gated behind `monitor_better_versions_enabled` (default off).

All three `start()`/`stop()` from the FastAPI lifespan in `app/main.py`. Crash recovery
(`reconcile_interrupted_assets`, `reconcile_orphaned_replacement_attempts` in
`app/services/acquisition.py`/`replacement.py`) runs at startup to fix up state left mid-flight by
an unclean shutdown, rather than relying on a job queue's own at-least-once delivery guarantees.

## Consequences

- Zero additional infrastructure containers — matches the project's Docker-first, single-container
  deployment goal.
- Job durability is only as good as SQLite's own transactional guarantees plus the explicit
  reconciliation logic; there is no separate job-queue library's retry/dead-letter machinery to
  lean on, so every job type had to build its own idempotent resume behavior deliberately (see
  [ADR 0004](0004-sqlite-concurrency-strategy.md)).
