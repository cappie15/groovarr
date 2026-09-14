"""Core Spotify playlist sync logic: connect, sync (with snapshot_id-based
change detection and full-replace playlist-entry diffing), and disconnect.

See docs/00-research-and-architecture-review.md §7/§10/§11/§12/§60 for the
semantics this implements: duplicates preserved via `occurrence_index`,
disconnect never touches existing data, and a playlist can be connected in
the default no-login mode purely from its public Spotify ID.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.secrets import decrypt_secret
from app.db.models.external_playlist import ExternalPlatform
from app.db.models.media import MediaAsset, PlaylistMediaReference
from app.db.models.spotify import LIKED_SONGS_SPOTIFY_ID, PlaylistEntry, SpotifyPlaylist, Track
from app.domain.reference_counting import ensure_reference
from app.integrations.spotify.client import SpotifyClient
from app.integrations.spotify.errors import SpotifyAccessDeniedError, SpotifyReauthRequiredError
from app.integrations.spotify.utils import (
    parse_playlist_id,
    parse_release_year,
    parse_version,
    split_artists,
    strip_version_and_feature_text,
)
from app.services.deletion import delete_media_asset_if_eligible
from app.services.external_playlists import sync_external_playlist
from app.services.history import record_event
from app.services.settings_service import (
    get_app_settings,
    get_effective_spotify_credentials,
    mark_spotify_needs_reauth,
)

logger = structlog.get_logger(__name__)

# Small jitter so many playlists configured with the same interval don't all
# come due in the same instant (§9's "avoid a thundering herd" principle).
_NEXT_SYNC_JITTER_MINUTES = 15


@dataclass
class SyncResult:
    unchanged: bool
    tracks_upserted: int
    entries_written: int


class PlaylistAlreadyFinalizedError(ValueError):
    """Raised when trying to connect/sync a playlist that was previously
    disconnected — reconnecting a finalized playlist is out of scope for
    Phase 2 (the user would need to explicitly create a fresh connection,
    which the API layer can decide how to handle later).
    """


async def connect_playlist(session: AsyncSession, http: httpx.AsyncClient, url_or_id: str) -> SpotifyPlaylist:
    """Connect a new playlist by Spotify URL/URI/ID and perform its initial
    sync. Idempotent: calling this again for an already-connected playlist
    just returns the existing row without duplicating anything.
    """
    spotify_id = parse_playlist_id(url_or_id)

    existing = await session.scalar(select(SpotifyPlaylist).where(SpotifyPlaylist.spotify_id == spotify_id))
    if existing is not None:
        if existing.finalized_at is not None:
            raise PlaylistAlreadyFinalizedError(
                f"Playlist {spotify_id!r} was previously disconnected/finalized and cannot be reconnected."
            )
        return existing

    settings_row = await get_app_settings(session)
    playlist = SpotifyPlaylist(
        spotify_id=spotify_id,
        name=spotify_id,  # placeholder until the first sync fills in the real name
        connected=True,
        sync_interval_hours=settings_row.default_sync_interval_hours,
    )
    session.add(playlist)
    await session.flush()

    await sync_playlist(session, http, playlist, force=True)
    return playlist


async def connect_liked_songs(session: AsyncSession, http: httpx.AsyncClient) -> SpotifyPlaylist:
    """Connect Spotify's "Liked Songs" (Saved Tracks) as a special
    pseudo-playlist row (see `LIKED_SONGS_SPOTIFY_ID`), distinct from
    `connect_playlist` because there is no URL/ID to parse — Liked Songs is
    reached via `GET /me/tracks`, never `/playlists/{id}`. Idempotent, same
    as `connect_playlist`: calling this again just returns the existing row.

    Liked Songs is inherently private, user-specific data — Spotify's
    Client Credentials (app-only) mode can *never* read it, for any account,
    public-playlist-style fallback or not. So unlike a regular playlist
    (which only needs the optional PKCE "Connect your Spotify account"
    feature for private/collaborative playlists, or in practice today for
    any playlist's tracks per §2-E), Liked Songs flatly requires it to
    already be enabled and completed — checked up front here so a user who
    hasn't set it up gets one clear, actionable 4xx instead of a confusing
    failure partway through a sync.
    """
    existing = await session.scalar(
        select(SpotifyPlaylist).where(SpotifyPlaylist.spotify_id == LIKED_SONGS_SPOTIFY_ID)
    )
    if existing is not None:
        if existing.finalized_at is not None:
            raise PlaylistAlreadyFinalizedError(
                "Liked Songs was previously disconnected/finalized and cannot be reconnected."
            )
        return existing

    settings_row = await get_app_settings(session)
    if not settings_row.spotify_user_oauth_enabled or not settings_row.spotify_refresh_token_encrypted:
        raise SpotifyAccessDeniedError(
            "Liked Songs is private, user-specific Spotify data — it can never be read via "
            'Client Credentials (app-only) auth. Complete "Connect your Spotify account" in '
            "Settings first, then try connecting Liked Songs again."
        )

    playlist = SpotifyPlaylist(
        spotify_id=LIKED_SONGS_SPOTIFY_ID,
        name="Liked Songs",
        is_liked_songs=True,
        connected=True,
        sync_interval_hours=settings_row.default_sync_interval_hours,
    )
    session.add(playlist)
    await session.flush()

    await sync_playlist(session, http, playlist, force=True)
    return playlist


async def sync_playlist(
    session: AsyncSession, http: httpx.AsyncClient, playlist: SpotifyPlaylist, *, force: bool = False
) -> SyncResult:
    """Sync one playlist against Spotify: fetch (or skip, if `snapshot_id`
    is unchanged), normalize tracks, and fully replace this playlist's
    `PlaylistEntry` rows to exactly match the current Spotify order —
    including duplicate occurrences of the same track (§12).

    Liked Songs (`playlist.is_liked_songs`) flows through this exact same
    function rather than a parallel code path — it only diverges at the two
    points where it genuinely must: how the access token is obtained (always
    PKCE, never Client Credentials — see `connect_liked_songs`) and which
    Spotify endpoint/response shape supplies the raw track items. Everything
    downstream (track upsert, full-replace diffing, reference reconciliation,
    external-playlist sync) is identical.
    """
    client = SpotifyClient(http)
    creds = await get_effective_spotify_credentials(session)

    if playlist.is_liked_songs:
        # Inherently private, user-specific data — there is no app-only path
        # that could ever work, so go straight to the PKCE user token rather
        # than attempting (and always failing) a Client Credentials call
        # first like a regular playlist does.
        access_token = await _get_user_access_token(session, http, client)
        # No `/playlists/{id}`-equivalent metadata call exists for Liked
        # Songs — name is fixed, and there is no snapshot_id (see the field
        # comment on SpotifyPlaylist.snapshot_id for why that's fine).
        playlist_data: dict[str, Any] = {"name": "Liked Songs", "snapshot_id": None, "images": []}
    else:
        access_token = await client.get_app_access_token(creds.client_id, creds.client_secret)
        try:
            playlist_data = await client.get_playlist(playlist.spotify_id, access_token)
        except SpotifyAccessDeniedError:
            # Default (Client Credentials) auth can only read public/unlisted
            # playlists — a 401/403 here means this one is private/collaborative,
            # so fall back to the optional PKCE user token if it's set up (§2-E).
            access_token = await _get_user_access_token(session, http, client)
            playlist_data = await client.get_playlist(playlist.spotify_id, access_token)

    new_snapshot_id = playlist_data.get("snapshot_id")

    if not force and playlist.snapshot_id is not None and playlist.snapshot_id == new_snapshot_id:
        _schedule_next_sync(playlist)
        await session.commit()
        return SyncResult(unchanged=True, tracks_upserted=0, entries_written=0)

    if playlist.is_liked_songs:
        raw_items = await client.get_saved_tracks(access_token)
    else:
        try:
            raw_items = await client.get_playlist_tracks(playlist.spotify_id, access_token)
        except SpotifyAccessDeniedError:
            # LIVE-VERIFIED (2026-09-14, against a real, genuinely-public
            # playlist): Spotify's Client Credentials flow can read a playlist's
            # own metadata (the `get_playlist` call above, which only requests
            # id/name/snapshot_id/images/public/collaborative) but denies the
            # *track-listing* sub-resource outright — 403, even for a public,
            # non-collaborative playlist. This is NOT limited to private/
            # collaborative playlists as originally assumed from documentation;
            # it fires far more often than the fallback above, and PKCE is
            # effectively required to read ANY playlist's tracks today, not just
            # private ones. `access_token` may already be a user token here (if
            # the metadata call above also fell back) — re-fetching is harmless.
            access_token = await _get_user_access_token(session, http, client)
            raw_items = await client.get_playlist_tracks(playlist.spotify_id, access_token)

    playlist.name = playlist_data.get("name") or playlist.name
    playlist.snapshot_id = new_snapshot_id

    images = playlist_data.get("images") or []
    if images:
        await _cache_playlist_artwork(http, playlist, images[0]["url"])

    # Captured before the full-replace below so we can tell, afterward,
    # exactly which tracks disappeared from this playlist entirely (§13/§14 —
    # a disappeared track's PlaylistMediaReference must be dropped, and its
    # MediaAsset re-checked for deletion eligibility; see
    # `_reconcile_references`). Only meaningful for a real (non-`force`)
    # re-sync — on first connect there are no prior entries to compare
    # against, which is exactly what an empty set represents here.
    old_track_ids = set(
        (
            await session.scalars(
                select(PlaylistEntry.track_id).where(PlaylistEntry.playlist_id == playlist.id).distinct()
            )
        ).all()
    )

    occurrence_counts: dict[int, int] = {}
    new_entries: list[PlaylistEntry] = []
    tracks_upserted = 0
    position = 0

    for item in raw_items:
        # `/playlists/{id}/items` (the now-required replacement for the
        # dead `/tracks` endpoint, see client.py) nests the track object
        # under `.item`, not `.track`; `/me/tracks` (Liked Songs) nests it
        # under `.track` instead — same inner shape either way.
        raw_track = item.get("track") if playlist.is_liked_songs else item.get("item")
        # Removed/unavailable tracks, episodes, and local files carry no
        # usable Spotify track object — skip them rather than raising, they
        # simply don't occupy a playlist slot.
        if not raw_track or raw_track.get("is_local") or not raw_track.get("id"):
            continue

        track = await _upsert_track(session, http, raw_track)
        tracks_upserted += 1

        occurrence_index = occurrence_counts.get(track.id, 0)
        occurrence_counts[track.id] = occurrence_index + 1

        new_entries.append(
            PlaylistEntry(
                playlist_id=playlist.id,
                track_id=track.id,
                position=position,
                occurrence_index=occurrence_index,
            )
        )
        position += 1

    # Full replace-in-one-transaction: the simplest strategy that's
    # guaranteed correct for adds/removes/reorders/duplicates (§11/§60).
    # Nothing references PlaylistEntry.id across syncs (PlaylistMediaReference,
    # per §7, keys on playlist_id/track_id instead), so row identity doesn't
    # need to survive a sync — only the final ordered set matters.
    await session.execute(delete(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist.id))
    session.add_all(new_entries)

    playlist.last_synced_at = datetime.now(UTC)
    _schedule_next_sync(playlist)

    record_event(
        session,
        event_type="spotify.playlist_synced",
        detail=f"{playlist.name!r} synced: {tracks_upserted} tracks, {len(new_entries)} entries.",
    )
    await session.commit()

    logger.info(
        "spotify.playlist_synced",
        spotify_id=playlist.spotify_id,
        tracks=tracks_upserted,
        entries=len(new_entries),
    )

    # Phase 9 (§13/§14): keep PlaylistMediaReference correct in both
    # directions now that this playlist's PlaylistEntry set is final —
    # see `_reconcile_references` for what each direction does. Best-effort
    # in the same spirit as the external-playlist sync below: a problem here
    # must never fail the Spotify sync that already succeeded and committed.
    try:
        new_track_ids = {e.track_id for e in new_entries}
        await _reconcile_references(session, playlist.id, old_track_ids, new_track_ids)
    except Exception:
        logger.exception("spotify.reference_reconciliation_failed", spotify_id=playlist.spotify_id)

    # Phase 8 (§60): additions/removals/reorders must propagate to whichever
    # external playlists this Spotify playlist feeds. Best-effort — a
    # Jellyfin/Plex failure here must never fail the Spotify sync itself,
    # which is what just succeeded and already committed above.
    try:
        if playlist.jellyfin_enabled:
            await sync_external_playlist(session, http, ExternalPlatform.JELLYFIN, playlist)
        if playlist.plex_enabled:
            await sync_external_playlist(session, http, ExternalPlatform.PLEX, playlist)
    except Exception:
        logger.exception("spotify.external_playlist_sync_failed", spotify_id=playlist.spotify_id)

    return SyncResult(unchanged=False, tracks_upserted=tracks_upserted, entries_written=len(new_entries))


async def disconnect_playlist(session: AsyncSession, playlist: SpotifyPlaylist) -> None:
    """Disconnect/finalize a playlist (§10). Deliberately does *not* touch
    `PlaylistEntry` rows, downloaded media, or anything else — it only stops
    future Spotify-driven changes from being applied.
    """
    playlist.connected = False
    playlist.finalized_at = datetime.now(UTC)
    record_event(
        session,
        event_type="spotify.playlist_finalized",
        detail=f"{playlist.name!r} disconnected/finalized — existing media and references left untouched.",
    )
    await session.commit()


async def _reconcile_references(
    session: AsyncSession, playlist_id: int, old_track_ids: set[int], new_track_ids: set[int]
) -> None:
    """Keep `PlaylistMediaReference` correct after a playlist's `PlaylistEntry`
    set changes (§13/§14/§60):

    - a track that DISAPPEARED from this playlist entirely (not merely
      reordered/duplicated differently) must have its reference to this
      playlist removed, and its `MediaAsset` re-checked for deletion
      eligibility — a routine sync is exactly the event §70 warns must not
      be trusted alone without re-verifying zero-references at delete time,
      which `delete_media_asset_if_eligible` does.
    - a track newly present in this playlist that ALREADY has a `MediaAsset`
      (e.g. shared with another playlist, or matched by the existing-library
      scanner) needs a reference added now, so this playlist "counts" toward
      keeping the file alive and so Phase 8's playlist reconstruction can see
      it. A brand-new track with no `MediaAsset` yet needs nothing here —
      once Automatic/Manual Search creates one, `sync_references_for_track`
      (called from `app.services.search`) wires up every playlist that
      currently lists that track, this one included.
    """
    removed_track_ids = old_track_ids - new_track_ids
    for track_id in removed_track_ids:
        stale_refs = (
            await session.scalars(
                select(PlaylistMediaReference).where(
                    PlaylistMediaReference.playlist_id == playlist_id,
                    PlaylistMediaReference.track_id == track_id,
                )
            )
        ).all()
        for ref in stale_refs:
            media_asset_id = ref.media_asset_id
            await session.delete(ref)
            await session.commit()
            asset = await session.get(MediaAsset, media_asset_id)
            if asset is not None:
                await delete_media_asset_if_eligible(session, asset)

    for track_id in new_track_ids:
        asset = await session.scalar(select(MediaAsset).where(MediaAsset.track_id == track_id).order_by(MediaAsset.id))
        if asset is not None:
            await ensure_reference(session, playlist_id=playlist_id, track_id=track_id, media_asset_id=asset.id)
    await session.commit()


# --- internals ----------------------------------------------------------------


async def _get_user_access_token(session: AsyncSession, http: httpx.AsyncClient, client: SpotifyClient) -> str:
    settings_row = await get_app_settings(session)
    if not settings_row.spotify_user_oauth_enabled or not settings_row.spotify_refresh_token_encrypted:
        raise SpotifyAccessDeniedError(
            "Spotify denied this app-only request — this can happen even for public playlists, "
            'not just private/collaborative ones. Enable and complete "Connect your Spotify '
            'account" in Settings to read it.'
        )
    creds = await get_effective_spotify_credentials(session)
    refresh_token = decrypt_secret(settings_row.spotify_refresh_token_encrypted)
    try:
        token_response = await client.refresh_pkce_token(client_id=creds.client_id, refresh_token=refresh_token)
    except SpotifyReauthRequiredError:
        await mark_spotify_needs_reauth(session)
        raise
    return token_response["access_token"]  # type: ignore[no-any-return]


async def _upsert_track(session: AsyncSession, http: httpx.AsyncClient, raw_track: dict) -> Track:
    spotify_track_id: str = raw_track["id"]
    track = await session.scalar(select(Track).where(Track.spotify_track_id == spotify_track_id))

    artist_names = [a["name"] for a in raw_track.get("artists", []) if a.get("name")]
    primary_artist, featured_artists = split_artists(artist_names or ["Unknown Artist"])

    raw_name: str = raw_track.get("name", "")
    version = parse_version(raw_name)
    canonical_title = strip_version_and_feature_text(raw_name) or raw_name

    album = raw_track.get("album") or {}
    release_year = parse_release_year(album.get("release_date"))

    if track is None:
        track = Track(spotify_track_id=spotify_track_id)
        session.add(track)

    track.canonical_artist = primary_artist
    track.featured_artists = featured_artists
    track.canonical_title = canonical_title
    track.parsed_version = version
    track.release_year = release_year
    track.duration_ms = int(raw_track.get("duration_ms") or 0)
    track.explicit = bool(raw_track.get("explicit", False))

    # LIVE-VERIFIED GAP (2026-09-14): `Track.album_artwork_path` existed as a
    # column but nothing ever populated it — only playlist-cover art was
    # cached, so every acquired video shipped with no embedded artwork at
    # all (confirmed via mutagen on a real downloaded file: `covr` was
    # `None`). Fixed here: cache the track's own album image the same way
    # `_cache_playlist_artwork` already does for playlists, skipping the
    # fetch when a cached file already exists (album art doesn't change).
    album_images = album.get("images") or []
    if album_images and not (track.album_artwork_path and Path(track.album_artwork_path).is_file()):
        await _cache_track_artwork(http, track, album_images[0]["url"])

    await session.flush()  # assign track.id (needed by the caller) without committing yet
    return track


async def _cache_playlist_artwork(http: httpx.AsyncClient, playlist: SpotifyPlaylist, image_url: str) -> None:
    """Spotify playlist cover URLs expire in under 24h (§3/§7) — fetch and
    cache the bytes locally rather than persisting the URL. Best-effort:
    a failure here must never fail the whole sync.
    """
    settings = get_settings()
    artwork_dir = settings.config_dir / "artwork"
    try:
        artwork_dir.mkdir(parents=True, exist_ok=True)
        response = await http.get(image_url, timeout=10.0)
        response.raise_for_status()
        dest = artwork_dir / f"{playlist.spotify_id}.jpg"
        dest.write_bytes(response.content)
        playlist.artwork_path = str(dest)
    except (httpx.HTTPError, OSError):
        logger.warning("spotify.artwork_cache_failed", spotify_id=playlist.spotify_id)


async def _cache_track_artwork(http: httpx.AsyncClient, track: Track, image_url: str) -> None:
    """Same rationale as `_cache_playlist_artwork` (Spotify image URLs
    expire quickly, so cache bytes not URLs) — this is what lets Phase 6's
    tagging step embed real cover art (`covr`) instead of silently having
    nothing to embed. Best-effort: never fails the sync.
    """
    settings = get_settings()
    artwork_dir = settings.config_dir / "artwork" / "tracks"
    try:
        artwork_dir.mkdir(parents=True, exist_ok=True)
        response = await http.get(image_url, timeout=10.0)
        response.raise_for_status()
        dest = artwork_dir / f"{track.spotify_track_id}.jpg"
        dest.write_bytes(response.content)
        track.album_artwork_path = str(dest)
    except (httpx.HTTPError, OSError):
        logger.warning("spotify.track_artwork_cache_failed", spotify_track_id=track.spotify_track_id)


def _schedule_next_sync(playlist: SpotifyPlaylist) -> None:
    jitter_minutes = random.uniform(-_NEXT_SYNC_JITTER_MINUTES, _NEXT_SYNC_JITTER_MINUTES)
    playlist.next_sync_at = (
        datetime.now(UTC) + timedelta(hours=playlist.sync_interval_hours) + timedelta(minutes=jitter_minutes)
    )
