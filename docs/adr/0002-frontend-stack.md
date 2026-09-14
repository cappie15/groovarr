# 0002 — Frontend stack: React + TypeScript + Vite

Status: Accepted
Rationale: [architecture review §4, §83](../00-research-and-architecture-review.md#4-proposed-technology-stack)

## Context

The UI needs to be a dense, information-first admin interface (Sonarr/Radarr-inspired), not a
consumer-styled app, built and shipped as static assets served by the same single container as
the backend (no separate frontend service).

## Decision

React 18 + TypeScript + Vite (`frontend/package.json`), with `react-router-dom` v7 for
client-side routing across the 11 required sections. No heavy component library — shared
primitives (`DataTable`, `StatusBadge`, `StatCard`, `Toast`, `ErrorBanner`) are hand-built in
plain CSS (`frontend/src/styles/`) to keep information density high and avoid consumer-app chrome.
The API client (`frontend/src/api/schema.ts`) is generated from the backend's live OpenAPI schema
via `openapi-typescript` (an `npm run generate-api` script), committed to the repo rather than
generated in CI (the frontend CI job doesn't boot a live backend).

## Consequences

- `frontend/dist` (the Vite build output) is what `docker/Dockerfile` copies into the final image,
  and what the backend now mounts via `StaticFiles` + a SPA-fallback catch-all route
  (`backend/app/main.py`) so deep links like `/playlists` work when loaded directly.
- Type drift between backend and frontend is caught at build time (`tsc --noEmit`) as long as
  `schema.ts` is regenerated after a backend API change — this is a manual step, not automatic;
  see `CONTRIBUTING.md`.
