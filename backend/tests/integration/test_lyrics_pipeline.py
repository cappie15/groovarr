"""Phase 7 (Lyrics) wired into the Phase 5/6 acquisition pipeline: the
`.lrc` sidecar is written next to the real imported video (same basename),
using a real ffmpeg-generated fixture — only the LRCLIB HTTP call and the
yt-dlp download are mocked (§92).
"""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models.lyrics import Lyrics, LyricsKind
from app.services.acquisition import claim_next_ready_asset, process_media_asset
from tests.fixtures.lrclib_backend import FakeLRCLIBBackend
from tests.integration.test_acquisition import _good_streams_factory, _make_candidate_selected_asset

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available in this environment")


@pytest.fixture
def media_dirs(tmp_path, monkeypatch):
    media_dir = tmp_path / "music-videos"
    downloads_dir = tmp_path / "downloads"
    media_dir.mkdir()
    downloads_dir.mkdir()
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("DOWNLOADS_DIR", str(downloads_dir))
    get_settings.cache_clear()
    yield media_dir, downloads_dir
    get_settings.cache_clear()


def _patch_lrclib(monkeypatch, fake: FakeLRCLIBBackend) -> None:
    monkeypatch.setattr("app.services.lyrics.LRCLIBBackend", lambda http: _FakeBackendAdapter(fake, http))
    monkeypatch.setattr("app.integrations.lyrics.lrclib._rate_limit_gate", _noop_gate)


class _FakeBackendAdapter:
    """Swaps in FakeLRCLIBBackend's mocked httpx transport for whatever real
    client app.services.lyrics.get_or_fetch_lyrics constructs, so the
    pipeline-level tests don't need their own httpx.AsyncClient plumbing.
    """

    def __init__(self, fake: FakeLRCLIBBackend, _http_ignored) -> None:
        from app.integrations.lyrics.lrclib import LRCLIBBackend

        self._real = LRCLIBBackend(fake.build_client())

    async def fetch(self, **kwargs):
        return await self._real.fetch(**kwargs)


async def _noop_gate() -> None:
    pass


@pytest.mark.asyncio
async def test_sidecar_written_next_to_the_imported_video_with_matching_basename(
    db_session, media_dirs, monkeypatch
):
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())
    fake = FakeLRCLIBBackend()
    fake.set_get_found(syncedLyrics="[00:01.00]La la la", duration=2, instrumental=False)
    _patch_lrclib(monkeypatch, fake)

    await _make_candidate_selected_asset(db_session, artist="Daft Punk", title="Around the World", duration_ms=2000)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    video_path = Path(claimed.local_path)
    sidecar_path = video_path.with_suffix(".lrc")
    assert sidecar_path.is_file()
    assert sidecar_path.read_text(encoding="utf-8") == "[00:01.00]La la la"

    lyrics_row = await db_session.scalar(select(Lyrics).where(Lyrics.track_id == claimed.track_id))
    assert lyrics_row.kind == LyricsKind.SYNCED
    assert lyrics_row.sidecar_path == str(sidecar_path)

    # Bonus embedded tag also carries the same text (§2-C — secondary, not
    # the authoritative artifact, but should still be present).
    from mutagen.mp4 import MP4

    tags = MP4(claimed.local_path)
    assert tags["\xa9lyr"] == ["[00:01.00]La la la"]


@pytest.mark.asyncio
async def test_lyrics_not_found_does_not_prevent_a_successful_import(db_session, media_dirs, monkeypatch):
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())
    fake = FakeLRCLIBBackend()
    fake.set_get_not_found()
    fake.set_search_results([])
    _patch_lrclib(monkeypatch, fake)

    await _make_candidate_selected_asset(db_session)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert Path(claimed.local_path).is_file()
    assert not Path(claimed.local_path).with_suffix(".lrc").exists()


@pytest.mark.asyncio
async def test_lyrics_backend_raising_never_fails_the_import(db_session, media_dirs, monkeypatch):
    """The critical Phase 7 correctness requirement: a lyrics fetch that
    throws entirely must never cost the user an otherwise-successfully-
    acquired video (§48).
    """
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())

    class ExplodingBackend:
        def __init__(self, http) -> None:
            pass

        async def fetch(self, **kwargs):
            raise RuntimeError("simulated total LRCLIB outage")

    monkeypatch.setattr("app.services.lyrics.LRCLIBBackend", ExplodingBackend)

    await _make_candidate_selected_asset(db_session)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)
    await db_session.refresh(claimed)

    assert result.succeeded is True
    assert Path(claimed.local_path).is_file()


@pytest.mark.asyncio
async def test_lyrics_disabled_setting_skips_the_lookup_entirely(db_session, media_dirs, monkeypatch):
    monkeypatch.setattr("app.services.acquisition.download_streams", _good_streams_factory())
    fake = FakeLRCLIBBackend()
    fake.set_get_found(plainLyrics="Should never be fetched", duration=2, instrumental=False)
    _patch_lrclib(monkeypatch, fake)

    from app.services.settings_service import get_app_settings

    app_settings = await get_app_settings(db_session)
    app_settings.lyrics_enabled = False
    await db_session.commit()

    await _make_candidate_selected_asset(db_session)
    claimed = await claim_next_ready_asset(db_session)
    result = await process_media_asset(db_session, claimed)

    assert result.succeeded is True
    assert fake.get_requests == 0
    assert fake.search_requests == 0
