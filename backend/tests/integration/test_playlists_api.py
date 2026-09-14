"""Integration test for GET/PUT /api/playlists' Phase-"frontend wiring"
additions to `PlaylistOut`: jellyfin_enabled/plex_enabled/name-override
fields were previously write-only (settable via
PUT .../external-config but never readable), which would have left the
Playlists page unable to show current toggle state. Calls the route
functions directly against `db_session`, matching this codebase's
established integration-test convention (see test_dashboard.py).
"""

from app.api.pagination import Pagination
from app.api.playlists import ExternalPlaylistConfigRequest, list_playlists, update_external_config
from app.db.models.spotify import SpotifyPlaylist


async def test_list_playlists_exposes_current_external_toggle_state(db_session) -> None:
    playlist = SpotifyPlaylist(spotify_id="p1", name="My Playlist", connected=True)
    db_session.add(playlist)
    await db_session.commit()

    [before] = await list_playlists(page=Pagination(limit=50, offset=0), session=db_session)
    assert before.jellyfin_enabled is False
    assert before.plex_enabled is False
    assert before.jellyfin_name_override is None
    assert before.plex_name_override is None

    await update_external_config(
        "p1",
        ExternalPlaylistConfigRequest(jellyfin_enabled=True, plex_name_override="90s Music Videos"),
        session=db_session,
    )

    [after] = await list_playlists(page=Pagination(limit=50, offset=0), session=db_session)
    assert after.jellyfin_enabled is True
    assert after.plex_enabled is False  # untouched by the partial update
    assert after.plex_name_override == "90s Music Videos"
