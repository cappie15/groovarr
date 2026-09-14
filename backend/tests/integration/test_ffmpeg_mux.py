"""Real-ffmpeg tests for app/integrations/acquisition/{ffmpeg_mux,probe}.py
(§40/§80/§81). No mocking here — these prove the actual subprocess/ffprobe
behavior, not just the orchestration around it.
"""

import shutil

import pytest

from app.db.models.settings import ContainerPolicy
from app.integrations.acquisition.errors import ValidationError
from app.integrations.acquisition.ffmpeg_mux import mux_media, mux_to_mp4
from app.integrations.acquisition.probe import probe_media, resolution_label
from tests.fixtures.media_clips import (
    make_aac_audio,
    make_h264_aac_video,
    make_opus_audio,
    make_truncated_garbage,
    make_vp9_video,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available in this environment")


@pytest.mark.asyncio
async def test_stream_copy_path_for_mp4_compatible_sources(tmp_path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    make_h264_aac_video(video)
    make_aac_audio(audio)

    dest = tmp_path / "out.mp4"
    result = await mux_to_mp4(video, audio, dest)

    assert result.transcoded_video is False
    assert result.transcoded_audio is False
    assert dest.is_file() and dest.stat().st_size > 0

    probe = await probe_media(dest)
    assert probe.video_codec == "h264"
    assert probe.audio_codec == "aac"
    assert probe.duration_s is not None and probe.duration_s > 0


@pytest.mark.asyncio
async def test_transcode_path_for_mp4_incompatible_sources(tmp_path):
    video = tmp_path / "video.webm"
    audio = tmp_path / "audio.opus"
    make_vp9_video(video)
    make_opus_audio(audio)

    dest = tmp_path / "out.mp4"
    result = await mux_to_mp4(video, audio, dest)

    assert result.transcoded_video is True
    assert result.transcoded_audio is True

    probe = await probe_media(dest)
    # Whatever H.264 encoder was actually available (libopenh264 or the
    # libx264 fallback) always produces "h264" as the codec_name ffprobe
    # reports — the encoder choice is an implementation detail, not
    # something a downstream reader needs to distinguish.
    assert probe.video_codec == "h264"
    assert probe.audio_codec == "aac"


@pytest.mark.asyncio
async def test_never_trims_duration(tmp_path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    make_h264_aac_video(video, duration_s=3)
    make_aac_audio(audio, duration_s=3)

    dest = tmp_path / "out.mp4"
    await mux_to_mp4(video, audio, dest)

    probe = await probe_media(dest)
    assert probe.duration_s == pytest.approx(3.0, abs=0.3)


@pytest.mark.asyncio
async def test_probe_media_rejects_missing_file(tmp_path):
    with pytest.raises(ValidationError):
        await probe_media(tmp_path / "does-not-exist.mp4")


@pytest.mark.asyncio
async def test_probe_media_rejects_garbage_file(tmp_path):
    garbage = tmp_path / "garbage.mp4"
    make_truncated_garbage(garbage)
    with pytest.raises(ValidationError):
        await probe_media(garbage)


@pytest.mark.asyncio
async def test_mux_raises_mux_error_on_unreadable_input(tmp_path):
    video = tmp_path / "video.mp4"
    make_h264_aac_video(video)
    missing_audio = tmp_path / "does-not-exist.m4a"

    with pytest.raises(ValidationError):
        # probe_media on the missing audio input raises first — mux_to_mp4
        # always probes both inputs before ever invoking ffmpeg itself.
        await mux_to_mp4(video, missing_audio, tmp_path / "out.mp4")


@pytest.mark.asyncio
async def test_mux_media_always_mp4_policy_transcodes_incompatible_source(tmp_path):
    """Default policy, unchanged behavior: an MP4-incompatible source still
    ends up as a transcoded MP4, never MKV."""
    video = tmp_path / "video.webm"
    audio = tmp_path / "audio.opus"
    make_vp9_video(video)
    make_opus_audio(audio)

    result = await mux_media(video, audio, tmp_path, ContainerPolicy.ALWAYS_MP4)

    assert result.container == "mp4"
    assert result.transcoded_video is True
    assert result.output_path.suffix == ".mp4"
    probe = await probe_media(result.output_path)
    assert probe.video_codec == "h264"


@pytest.mark.asyncio
async def test_mux_media_prefer_mkv_policy_avoids_transcode_for_incompatible_source(tmp_path):
    """The alternative policy: same incompatible source, but no transcode —
    falls back to a pure-stream-copy MKV instead."""
    video = tmp_path / "video.webm"
    audio = tmp_path / "audio.opus"
    make_vp9_video(video)
    make_opus_audio(audio)

    result = await mux_media(video, audio, tmp_path, ContainerPolicy.PREFER_MP4_ALLOW_MKV)

    assert result.container == "mkv"
    assert result.transcoded_video is False
    assert result.transcoded_audio is False
    assert result.output_path.suffix == ".mkv"
    probe = await probe_media(result.output_path)
    # Stream-copied, so the original (MP4-incompatible) codecs survive as-is
    # in the MKV — that's the whole point of this policy.
    assert probe.video_codec == "vp9"
    assert probe.audio_codec == "opus"


@pytest.mark.asyncio
async def test_mux_media_prefer_mkv_policy_still_prefers_mp4_when_compatible(tmp_path):
    """"Prefer MP4, allow MKV" means prefer — a compatible source still
    lands as MP4 under this policy, MKV is only the fallback."""
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    make_h264_aac_video(video)
    make_aac_audio(audio)

    result = await mux_media(video, audio, tmp_path, ContainerPolicy.PREFER_MP4_ALLOW_MKV)

    assert result.container == "mp4"
    assert result.transcoded_video is False
    assert result.transcoded_audio is False


def test_resolution_label_thresholds():
    assert resolution_label(2160) == "2160p"
    assert resolution_label(1440) == "1440p"
    assert resolution_label(1080) == "1080p"
    assert resolution_label(720) == "720p"
    assert resolution_label(480) == "480p"
    assert resolution_label(360) == "360p"
    assert resolution_label(None) is None
