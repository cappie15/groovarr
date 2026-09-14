"""Integration tests for the Settings sub-sections added for the frontend
Settings page (§84): Matching threshold, Download limits, and Lyrics toggle.
These fields existed on `AppSettings` from earlier phases but were never
exposed for reading or writing via the API — this closes that gap. Calls
route functions directly against `db_session`, matching the convention in
test_media_api.py/test_dashboard.py.
"""

import pytest
from pydantic import ValidationError

from app.api.settings import (
    DownloadSettingsRequest,
    LyricsSettingsRequest,
    MatchingThresholdRequest,
    read_settings,
    update_download_settings,
    update_lyrics_settings,
    update_matching_settings,
)


async def test_settings_out_includes_previously_unexposed_fields(db_session) -> None:
    out = await read_settings(session=db_session)
    # Defaults from the AppSettings model (§49/§50/§7), now actually visible.
    assert out.max_concurrent_downloads == 2
    assert out.max_download_attempts == 5
    assert out.lyrics_enabled is True
    assert out.automatic_match_threshold == 70


async def test_update_matching_threshold(db_session) -> None:
    out = await update_matching_settings(MatchingThresholdRequest(automatic_match_threshold=85), session=db_session)
    assert out.automatic_match_threshold == 85

    refetched = await read_settings(session=db_session)
    assert refetched.automatic_match_threshold == 85


async def test_update_download_settings_persists_and_is_bounded(db_session) -> None:
    out = await update_download_settings(
        DownloadSettingsRequest(max_concurrent_downloads=4, max_download_attempts=8), session=db_session
    )
    assert out.max_concurrent_downloads == 4
    assert out.max_download_attempts == 8

    refetched = await read_settings(session=db_session)
    assert refetched.max_concurrent_downloads == 4
    assert refetched.max_download_attempts == 8

    with pytest.raises(ValidationError):
        # Pydantic's own ge=1 constraint on the request model — proves the
        # bound is enforced at the API boundary, not just in the service.
        DownloadSettingsRequest(max_concurrent_downloads=0, max_download_attempts=8)


async def test_update_lyrics_enabled_toggle(db_session) -> None:
    out = await update_lyrics_settings(LyricsSettingsRequest(enabled=False), session=db_session)
    assert out.lyrics_enabled is False

    refetched = await read_settings(session=db_session)
    assert refetched.lyrics_enabled is False

    out_again = await update_lyrics_settings(LyricsSettingsRequest(enabled=True), session=db_session)
    assert out_again.lyrics_enabled is True
