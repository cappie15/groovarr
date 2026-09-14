"""Typed errors for the acquisition pipeline (yt-dlp download, FFmpeg mux,
ffprobe validation). Each carries whether it's worth an automatic retry —
mirrors the yt-dlp exit-code research in the architecture doc §3: a
transient network/extraction failure is retryable, a caller-side mistake or
an intentional/permanent failure is not.
"""


class AcquisitionError(Exception):
    """Base class for every acquisition-pipeline failure. `retryable=True`
    unless a subclass says otherwise — most failures at this stage (network
    hiccups, a temporarily unavailable format, a corrupt partial download)
    are worth another attempt (§50).
    """

    retryable: bool = True


class DownloadError(AcquisitionError):
    """yt-dlp could not fetch the requested streams."""


class NonRetryableDownloadError(DownloadError):
    """yt-dlp failed in a way further attempts cannot fix (video removed/
    private, no selected candidate, etc.) — do not retry."""

    retryable = False


class MuxError(AcquisitionError):
    """ffmpeg could not remux/transcode the downloaded streams."""


class ValidationError(AcquisitionError):
    """The muxed output failed post-download validation (§51) — missing/
    empty file, unparseable, missing a stream, or an implausible duration.
    Never imported into the media library.
    """
