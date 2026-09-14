# Groovarr

Groovarr is a self-hosted, single-user, Docker-first *Arr-style application that turns your
Spotify playlists into a deduplicated, correctly-tagged local library of music videos, kept in
sync with Jellyfin and/or Plex. It isn't a one-shot downloader script — it's a stateful library
manager in the spirit of Sonarr/Radarr, with monitored items, a wanted/missing list, automatic vs.
manual search, a download queue, history, and explainable match decisions. The pipeline runs
Spotify (canonical playlist/track truth) → local library check → YouTube discovery → deterministic
weighted-scoring candidate selection → automatic acquisition or manual review → download/remux →
metadata, artwork, and lyrics enrichment → atomic import → Jellyfin/Plex library refresh →
playlist reconstruction in Spotify order.

## Quick start (Docker Compose)

1. From the `docker/` directory, copy the example environment file and fill in your values
   (Compose looks for `.env` next to `docker-compose.yml`, so it must live in `docker/`, not the
   repo root):

   ```bash
   cd docker
   cp ../.env.example .env
   ```

2. Start Groovarr:

   ```bash
   docker compose up -d --build
   ```

3. Open `http://localhost:<PORT>` (see `docker/.env`) and finish setup (media/download
   directories, Spotify app credentials, and optionally Jellyfin/Plex connections) from the
   Settings page.

## Documentation

Full research, architecture decisions, and rationale live in [`docs/`](docs/), starting with
[`docs/00-research-and-architecture-review.md`](docs/00-research-and-architecture-review.md).
Third-party project licenses are tracked in
[`docs/THIRD_PARTY_NOTICES.md`](docs/THIRD_PARTY_NOTICES.md).
