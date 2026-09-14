"""Unit tests for hardware-accelerator detection
(app/integrations/acquisition/hwaccel.py), mocking subprocess/filesystem so
these run identically regardless of what this particular test host has.
"""

import pytest

from app.integrations.acquisition import hwaccel


@pytest.fixture(autouse=True)
def _clear_cache():
    hwaccel.reset_hardware_acceleration_cache()
    yield
    hwaccel.reset_hardware_acceleration_cache()


async def _fake_encoders(present: set[str]):
    async def _has_encoder(name: str) -> bool:
        return name in present

    return _has_encoder


@pytest.mark.asyncio
async def test_nothing_available(monkeypatch):
    monkeypatch.setattr(hwaccel, "_ffmpeg_has_encoder", await _fake_encoders(set()))
    monkeypatch.setattr(hwaccel, "_nvidia_gpu_reachable", lambda: _async_false())
    monkeypatch.setattr(hwaccel, "_render_nodes", lambda: [])

    status = await hwaccel.detect_hardware_acceleration()
    assert status == hwaccel.HardwareAccelStatus(False, False, False)
    assert status.any_available is False
    assert status.best() is None


@pytest.mark.asyncio
async def test_vaapi_only_generic_render_node(monkeypatch, tmp_path):
    node = tmp_path / "renderD128"
    node.touch()
    monkeypatch.setattr(hwaccel, "_ffmpeg_has_encoder", await _fake_encoders({"h264_vaapi"}))
    monkeypatch.setattr(hwaccel, "_nvidia_gpu_reachable", lambda: _async_false())
    monkeypatch.setattr(hwaccel, "_render_nodes", lambda: [node])
    monkeypatch.setattr(hwaccel, "_render_node_is_intel", lambda n: False)  # e.g. an AMD node

    status = await hwaccel.detect_hardware_acceleration()
    assert status.vaapi_available is True
    assert status.qsv_available is False  # not Intel, so QSV specifically doesn't apply
    assert status.nvenc_available is False
    assert status.best() == "vaapi"


@pytest.mark.asyncio
async def test_qsv_requires_intel_render_node_not_just_any_render_node(monkeypatch, tmp_path):
    node = tmp_path / "renderD128"
    node.touch()
    monkeypatch.setattr(hwaccel, "_ffmpeg_has_encoder", await _fake_encoders({"h264_qsv", "h264_vaapi"}))
    monkeypatch.setattr(hwaccel, "_nvidia_gpu_reachable", lambda: _async_false())
    monkeypatch.setattr(hwaccel, "_render_nodes", lambda: [node])
    monkeypatch.setattr(hwaccel, "_render_node_is_intel", lambda n: True)

    status = await hwaccel.detect_hardware_acceleration()
    assert status.qsv_available is True
    assert status.vaapi_available is True  # VAAPI also true on an Intel node
    assert status.best() == "qsv"  # QSV outranks generic VAAPI once confirmed Intel


@pytest.mark.asyncio
async def test_nvenc_requires_both_encoder_and_reachable_gpu(monkeypatch):
    # Encoder compiled in but no GPU actually reachable (e.g. no device
    # passthrough into the container) — must NOT report available.
    monkeypatch.setattr(hwaccel, "_ffmpeg_has_encoder", await _fake_encoders({"h264_nvenc"}))
    monkeypatch.setattr(hwaccel, "_nvidia_gpu_reachable", lambda: _async_false())
    monkeypatch.setattr(hwaccel, "_render_nodes", lambda: [])

    status = await hwaccel.detect_hardware_acceleration()
    assert status.nvenc_available is False


@pytest.mark.asyncio
async def test_nvenc_available_when_encoder_and_gpu_both_present(monkeypatch):
    monkeypatch.setattr(hwaccel, "_ffmpeg_has_encoder", await _fake_encoders({"h264_nvenc"}))
    monkeypatch.setattr(hwaccel, "_nvidia_gpu_reachable", lambda: _async_true())
    monkeypatch.setattr(hwaccel, "_render_nodes", lambda: [])

    status = await hwaccel.detect_hardware_acceleration()
    assert status.nvenc_available is True
    assert status.best() == "nvenc"


@pytest.mark.asyncio
async def test_nvenc_outranks_qsv_and_vaapi_when_all_available(monkeypatch, tmp_path):
    node = tmp_path / "renderD128"
    node.touch()
    monkeypatch.setattr(
        hwaccel, "_ffmpeg_has_encoder", await _fake_encoders({"h264_nvenc", "h264_qsv", "h264_vaapi"})
    )
    monkeypatch.setattr(hwaccel, "_nvidia_gpu_reachable", lambda: _async_true())
    monkeypatch.setattr(hwaccel, "_render_nodes", lambda: [node])
    monkeypatch.setattr(hwaccel, "_render_node_is_intel", lambda n: True)

    status = await hwaccel.detect_hardware_acceleration()
    assert status.best() == "nvenc"


def test_ordered_candidates_matches_best_priority_and_includes_the_rest():
    # Live-verified gap (see ffmpeg_mux.py's cascading fallback): a
    # detected-available encoder can still fail at runtime for reasons
    # detection can't see (e.g. QSV's Intel Media SDK userspace runtime
    # missing even though the device and PCI vendor check both pass) — so
    # callers need every genuinely-detected candidate in priority order,
    # not just the single top pick `best()` returns.
    all_three = hwaccel.HardwareAccelStatus(nvenc_available=True, qsv_available=True, vaapi_available=True)
    assert all_three.ordered_candidates() == ["nvenc", "qsv", "vaapi"]
    assert all_three.ordered_candidates()[0] == all_three.best()

    qsv_and_vaapi = hwaccel.HardwareAccelStatus(nvenc_available=False, qsv_available=True, vaapi_available=True)
    assert qsv_and_vaapi.ordered_candidates() == ["qsv", "vaapi"]

    vaapi_only = hwaccel.HardwareAccelStatus(nvenc_available=False, qsv_available=False, vaapi_available=True)
    assert vaapi_only.ordered_candidates() == ["vaapi"]

    none_available = hwaccel.HardwareAccelStatus(nvenc_available=False, qsv_available=False, vaapi_available=False)
    assert none_available.ordered_candidates() == []


@pytest.mark.asyncio
async def test_result_is_cached_across_calls(monkeypatch):
    calls = {"n": 0}

    async def _has_encoder(name: str) -> bool:
        calls["n"] += 1
        return False

    monkeypatch.setattr(hwaccel, "_ffmpeg_has_encoder", _has_encoder)
    monkeypatch.setattr(hwaccel, "_nvidia_gpu_reachable", lambda: _async_false())
    monkeypatch.setattr(hwaccel, "_render_nodes", lambda: [])

    await hwaccel.get_hardware_acceleration_status()
    await hwaccel.get_hardware_acceleration_status()
    await hwaccel.get_hardware_acceleration_status()

    # 3 encoders probed once each on the FIRST call only — later calls hit
    # the cache and must not re-invoke the probe at all.
    assert calls["n"] == 3


async def _async_true() -> bool:
    return True


async def _async_false() -> bool:
    return False
