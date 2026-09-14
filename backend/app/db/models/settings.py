"""Application settings persisted in the database (as opposed to `Settings`
in app/core/config.py, which holds process/deployment-level config read from
environment variables). This is the single-row table for settings a user
changes at runtime through the UI — see §84 of the architecture doc.

Phase 2 only adds the fields this phase's features actually need (Spotify
auth mode + default sync interval). Later phases extend this table rather
than replacing it.
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: There is exactly one settings row, always with this primary key. A
#: single-row table (rather than a free-form key/value table) keeps the
#: schema typed and migratable, per §6/§84's "don't over-engineer" guidance.
SETTINGS_SINGLETON_ID = 1


class ContainerPolicy(enum.StrEnum):
    """Controls what `app/integrations/acquisition/ffmpeg_mux.py` does when
    the source video/audio codec pair isn't natively MP4-compatible (a
    stream-copy into MP4 is always tried first regardless of this setting —
    it only affects what happens when that's not possible). See §2 row D of
    the architecture doc for the full research tradeoff this encodes.
    """

    #: The project owner's original, still-default decision (§2 row D):
    #: always finish in MP4, transcoding when the source codec isn't
    #: MP4-compatible. Guarantees artist/title/artwork/lyrics tags are
    #: reliably stored in the container and read correctly by VLC, Jellyfin,
    #: and Plex, at the cost of re-encoding video in that case.
    ALWAYS_MP4 = "always_mp4"

    #: Avoids the transcode: falls back to MKV via pure stream-copy instead
    #: of re-encoding when MP4 isn't natively reachable. Trades away
    #: metadata reliability to get there — Plex does not read embedded MKV
    #: title/artist/artwork tags at all, and Jellyfin's support is only
    #: partial, so a file produced this way depends on the `.lrc` sidecar
    #: and Groovarr's own UI rather than in-app/in-container metadata.
    PREFER_MP4_ALLOW_MKV = "prefer_mp4_allow_mkv"


class HardwareAccelPolicy(enum.StrEnum):
    """Which encoder `ffmpeg_mux.py` tries for a transcode that's actually
    needed (only relevant under `ContainerPolicy.ALWAYS_MP4` — the MKV
    fallback path never transcodes at all). See
    `app/integrations/acquisition/hwaccel.py` for how availability is
    detected.
    """

    #: Use the best hardware encoder `hwaccel.py` detects as genuinely
    #: usable right now (NVENC, then QSV, then VAAPI); fall back to the
    #: existing software path (`libopenh264`/`libx264`) if none is
    #: available, or if the hardware attempt fails at runtime. Behaviorally
    #: identical to DISABLED on a host with nothing detected, which is why
    #: this is safe as the default (§ below).
    AUTO = "auto"
    #: Always software — today's original, unchanged behavior.
    DISABLED = "disabled"
    #: Force a specific accelerator even if auto-detection is inconclusive
    #: (e.g. it's actually usable but a detection heuristic doesn't catch
    #: it). Still falls back to software if the forced encoder fails.
    NVENC = "nvenc"
    QSV = "qsv"
    VAAPI = "vaapi"


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=SETTINGS_SINGLETON_ID)

    default_sync_interval_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)

    # Spotify Client Credentials Flow (default, no-login mode). If left null,
    # the values from the SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET env vars
    # (app/core/config.py) are used instead — the DB row lets an operator set
    # or change these from the Settings UI without editing the container's
    # environment. spotify_client_secret is stored encrypted at rest.
    spotify_client_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    spotify_client_secret_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Optional Authorization Code + PKCE "Connect your Spotify account"
    # feature, off by default (§2-E/§7) — the default Client Credentials Flow
    # needs none of the fields below.
    spotify_user_oauth_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spotify_refresh_token_encrypted: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    spotify_refresh_token_obtained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when a stored refresh token is confirmed dead (Spotify's 6-month
    # hard expiry, or any other invalid_grant) so the UI can surface a clear
    # "reconnect your Spotify account" prompt instead of the sync job failing
    # silently/repeatedly (§3/§50).
    spotify_needs_reauth: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Phase 4 (matching): the weighted score at/above which a candidate is
    # selected automatically rather than routed to Manual Review. Tuned
    # against app/matching's fixture corpus, not chosen by feel (§86/§104 —
    # precision over recall: a false positive is worse than a manual-review
    # item). See app/matching/scoring.py for the scoring scale this applies to.
    automatic_match_threshold: Mapped[int] = mapped_column(Integer, default=70, nullable=False)

    # Phase 5 (acquisition, §49/§50): bounded concurrency for the download
    # worker pool, and how many attempts (including the first) a MediaAsset
    # gets before landing on DOWNLOAD_FAILED. Both defaults match the
    # architecture doc exactly (2 concurrent downloads, 5 attempts total).
    max_concurrent_downloads: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    max_download_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Phase 7 (Lyrics): best-effort LRCLIB lookup, on by default. A lyrics
    # failure/miss never blocks acquisition either way (§48) — this toggle is
    # purely for an operator who wants to skip the lookup entirely (e.g. to
    # avoid any LRCLIB traffic), not a correctness/safety switch.
    lyrics_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Phase 8 (Jellyfin, §54/§56/§60): zero-or-one server (§97). `jellyfin_user_id`
    # is required for playlist mutations specifically — Jellyfin's playlist API
    # throws a Guid.Empty error when called with only an API key and no
    # resolvable user (confirmed bug #12999), so this is populated from a
    # `GET /Users` picker in Settings, not typed in freehand. `jellyfin_library_id`
    # is informational/used to scope item search, since `/Library/Refresh` itself
    # always refreshes every library (Jellyfin has no per-library refresh route).
    jellyfin_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    jellyfin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    jellyfin_api_key_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    jellyfin_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jellyfin_library_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # `jellyfin_media_path` mirrors `plex_media_path` below: the media
    # directory's absolute path *as Jellyfin's own filesystem sees it* — only
    # needed if Jellyfin mounts the same volume at a different container path
    # than Groovarr does; left null, Groovarr assumes both paths are identical.
    jellyfin_media_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Phase 8 (Plex, §53/§56/§60): zero-or-one server (§97). There is no
    # "Music Videos" library type in Plex (§2 row A) — `plex_library_section_id`
    # must point at a Music (artist-type) or Other Videos section, enforced by
    # the Settings UI/API, not by this column itself. `plex_media_path` is the
    # media directory's absolute path *as Plex's own filesystem sees it* — only
    # needed if Plex mounts the same volume at a different container path than
    # Groovarr does; left null, Groovarr assumes both paths are identical.
    plex_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    plex_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    plex_token_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    plex_library_section_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plex_media_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Phase 9 (§36/§37): "Monitor for Better Versions", off by default. When
    # enabled, app.jobs.upgrade_monitor periodically re-searches every
    # Incomplete (visualizer-class) asset and every non-manually-selected
    # Available asset, and — via app.services.replacement.replace_track_media
    # — may swap in a *materially different* video, not merely a
    # higher-resolution copy of the same one. See MONITOR_BETTER_VERSIONS_WARNING
    # below for the exact operator-facing warning text this setting must be
    # surfaced with (§36's explicit requirement that the UI not describe this
    # merely as "quality upgrades").
    monitor_better_versions_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Container output policy (§2 row D): default preserves the owner's
    # original "always MP4, even if that means transcoding" decision — this
    # field only makes that choice overridable, it does not change the
    # default for existing/new installs. See `ContainerPolicy` above for
    # what each value means and the metadata-reliability tradeoff involved.
    container_policy: Mapped[ContainerPolicy] = mapped_column(
        # LIVE-VERIFIED BUG (2026-09-14): SQLAlchemy's Enum type stores/reads
        # a Python enum member by its NAME ("ALWAYS_MP4") by default, not its
        # .value ("always_mp4") — but this migration's server_default (and
        # every other place in the app, including the JSON API contract)
        # uses the lowercase .value form. Without values_callable, an
        # already-deployed row backfilled by the migration's server_default
        # stores "always_mp4" in the DB, and reading it back raises
        # `KeyError: 'always_mp4'` (SQLAlchemy looks it up as a member name,
        # finds none). values_callable makes both directions consistently
        # use .value, matching the migration and the API.
        SAEnum(
            ContainerPolicy,
            native_enum=False,
            length=32,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=ContainerPolicy.ALWAYS_MP4,
        nullable=False,
    )

    # Hardware-accelerated transcode policy (see `HardwareAccelPolicy` above
    # and `app/integrations/acquisition/hwaccel.py`). Default AUTO is safe
    # for existing/new installs with no hardware encoder reachable: AUTO
    # falls back to the exact same software path as DISABLED whenever
    # nothing is genuinely detected, so it changes nothing for the common
    # case and only takes effect where hardware acceleration would actually
    # help.
    hardware_acceleration: Mapped[HardwareAccelPolicy] = mapped_column(
        SAEnum(
            HardwareAccelPolicy,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=HardwareAccelPolicy.AUTO,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<AppSettings>"


#: §36's exact required warning — surfaced by the Settings API alongside the
#: toggle so a future UI has no excuse to soften or omit it.
MONITOR_BETTER_VERSIONS_WARNING = (
    "Enabling better-version monitoring may replace an existing music video with a "
    "different video, not merely a higher-resolution copy."
)
