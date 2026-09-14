"""Real-ffmpeg tests for app/integrations/acquisition/{ffmpeg_mux,probe}.py
(§40/§80/§81). No mocking here — these prove the actual subprocess/ffprobe
behavior, not just the orchestration around it.
"""

import os
import shutil

import pytest

from app.db.models.settings import ContainerPolicy, HardwareAccelPolicy
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


@pytest.mark.asyncio
async def test_forced_hardware_encoder_falls_back_to_software_when_device_inaccessible(tmp_path):
    """The default test-runner process has no `render` group membership
    (confirmed on this host: a real Intel iGPU + /dev/dri/renderD128 exist,
    but this process can't open it — the same situation a Docker container
    without `--device /dev/dri` passthrough would see). Forcing VAAPI here
    must therefore hit a real ffmpeg device-open failure and fall back to
    the existing software path, producing a valid file — never a hard
    failure just because the forced hardware encoder didn't pan out.
    """
    video = tmp_path / "video.webm"
    audio = tmp_path / "audio.opus"
    make_vp9_video(video)
    make_opus_audio(audio)

    dest = tmp_path / "out.mp4"
    result = await mux_to_mp4(video, audio, dest, hardware_policy=HardwareAccelPolicy.VAAPI)

    assert result.transcoded_video is True
    assert dest.is_file() and dest.stat().st_size > 0
    probe = await probe_media(dest)
    assert probe.video_codec == "h264"


@pytest.mark.asyncio
async def test_auto_cascades_through_multiple_hw_candidates_before_software(tmp_path, monkeypatch):
    """Orchestration-only test — deliberately mocked, unlike the rest of
    this file (see its module docstring) — because the real-world scenario
    it proves (a detected-available encoder failing at runtime while
    another detected candidate on the same device succeeds) can't be
    reliably reproduced with real hardware in every environment this suite
    runs in. Live-verified motivation: on this project's own test host, QSV
    was detected as available (real Intel device, correct PCI vendor) but
    failed with a genuine MFX session error at runtime, while VAAPI on the
    exact same device succeeded — `AUTO` must try both, in priority order,
    before giving up to software. The real per-encoder ffmpeg argument
    shapes are exercised with a real ffmpeg process by the tests above.
    """
    import app.integrations.acquisition.ffmpeg_mux as ffmpeg_mux_module
    from app.integrations.acquisition.hwaccel import HardwareAccelStatus

    video = tmp_path / "video.webm"
    audio = tmp_path / "audio.opus"
    make_vp9_video(video)
    make_opus_audio(audio)
    dest = tmp_path / "out.mp4"

    attempted: list[str] = []

    async def fake_status() -> HardwareAccelStatus:
        return HardwareAccelStatus(nvenc_available=False, qsv_available=True, vaapi_available=True)

    async def fake_run_ffmpeg(args, dest_path):
        if "h264_qsv" in args:
            attempted.append("qsv")
            return False, "simulated MFX session error"
        if "h264_vaapi" in args:
            attempted.append("vaapi")
            return True, ""
        raise AssertionError(f"unexpected ffmpeg invocation in this mocked test: {args}")

    monkeypatch.setattr(ffmpeg_mux_module, "get_hardware_acceleration_status", fake_status)
    monkeypatch.setattr(ffmpeg_mux_module, "_run_ffmpeg", fake_run_ffmpeg)

    result = await mux_to_mp4(video, audio, dest, hardware_policy=HardwareAccelPolicy.AUTO)

    assert attempted == ["qsv", "vaapi"]
    assert result.transcoded_video is True


@pytest.mark.skipif(
    not (os.path.exists("/dev/dri/renderD128") and os.access("/dev/dri/renderD128", os.R_OK | os.W_OK)),
    reason="No VAAPI render device accessible to this process (expected in most CI/sandbox runs)",
)
@pytest.mark.asyncio
async def test_real_vaapi_hardware_encode_when_device_is_genuinely_accessible(tmp_path):
    """Only runs where /dev/dri/renderD128 is genuinely usable by the test
    process (this exact host has a real Intel UHD 630 iGPU and was
    confirmed, via `sg render -c ffmpeg ...`, to actually VAAPI-encode
    successfully — see the hwaccel fork's report). Proves the real
    hardware path end-to-end, not just its absence/fallback.
    """
    video = tmp_path / "video.webm"
    audio = tmp_path / "audio.opus"
    make_vp9_video(video)
    make_opus_audio(audio)

    dest = tmp_path / "out.mp4"
    result = await mux_to_mp4(video, audio, dest, hardware_policy=HardwareAccelPolicy.VAAPI)

    assert result.transcoded_video is True
    probe = await probe_media(dest)
    assert probe.video_codec == "h264"
    assert probe.duration_s is not None and probe.duration_s > 0


def test_resolution_label_thresholds():
    assert resolution_label(2160) == "2160p"
    assert resolution_label(1440) == "1440p"
    assert resolution_label(1080) == "1080p"
    assert resolution_label(720) == "720p"
    assert resolution_label(480) == "480p"
    assert resolution_label(360) == "360p"
    assert resolution_label(None) is None
