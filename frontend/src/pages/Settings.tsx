import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import {
  getHardwareAccelerationDetection,
  getSettings,
  listJellyfinUsers,
  listPlexLibrarySections,
  spotifyOAuthAuthorizeUrl,
  testJellyfinConnection,
  testPlexConnection,
  updateContainerPolicy,
  updateDownloadSettings,
  updateHardwareAcceleration,
  updateJellyfinConfig,
  updateLyricsEnabled,
  updateMatchingThreshold,
  updateMonitorBetterVersions,
  updatePlexConfig,
  updateSpotifyCredentials,
  updateSpotifyUserOAuthEnabled,
  updateSyncInterval,
  type ContainerPolicy,
  type HardwareAccelPolicy,
  type HardwareAccelStatusOut,
  type JellyfinUserOut,
  type PlexLibrarySectionOut,
  type SettingsOut,
} from '../api/settings';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { useToast } from '../components/Toast';
import { useApiQuery } from '../hooks/useApiQuery';

// `errorMessage`, `SettingsSection` and `SaveButton` are exported so the
// Setup Wizard (pages/SetupWizard.tsx) can reuse this page's own card look
// and error-formatting instead of re-implementing either — the wizard's
// steps call the exact same api/settings.ts functions as the sections
// below, just walked through one at a time with guided copy.
export function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Unexpected error — please try again.';
}

export function SettingsSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="settings-section">
      <h2>{title}</h2>
      {hint && <p className="settings-section__hint">{hint}</p>}
      {children}
    </div>
  );
}

export function SaveButton({ busy, onClick, label = 'Save' }: { busy: boolean; onClick: () => void; label?: string }) {
  return (
    <button type="button" className="btn btn--primary btn--small" disabled={busy} onClick={onClick}>
      {busy ? 'Saving…' : label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Spotify — Client Credentials alone covers playlist metadata (name,
// artwork, change detection). Live-verified 2026-09-14 against a real
// Spotify app: Spotify now requires the PKCE "Connect your account" flow
// to actually read ANY playlist's tracks, public playlists included — this
// used to be true only for private/collaborative playlists (§2-E). The
// Client ID/Secret fields below are still required (metadata + the PKCE
// flow both use them), but the "Connect your Spotify account" step is now
// effectively mandatory for real use, not an optional add-on.
// ---------------------------------------------------------------------------
function SpotifySection({ settings, onSaved }: { settings: SettingsOut; onSaved: (s: SettingsOut) => void }) {
  const toast = useToast();
  const [clientId, setClientId] = useState(settings.spotify_client_id ?? '');
  const [clientSecret, setClientSecret] = useState('');
  const [syncInterval, setSyncInterval] = useState(String(settings.default_sync_interval_hours));
  const [busyCreds, setBusyCreds] = useState(false);
  const [busyInterval, setBusyInterval] = useState(false);
  const [busyOAuthToggle, setBusyOAuthToggle] = useState(false);

  async function saveCredentials() {
    if (!clientId.trim() || !clientSecret.trim()) {
      toast.showError('Both Client ID and Client Secret are required.');
      return;
    }
    setBusyCreds(true);
    try {
      const updated = await updateSpotifyCredentials({ client_id: clientId.trim(), client_secret: clientSecret });
      setClientSecret('');
      onSaved(updated);
      toast.showInfo('Spotify credentials saved.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusyCreds(false);
    }
  }

  async function saveInterval() {
    const hours = Number(syncInterval);
    if (!Number.isFinite(hours) || hours < 1) {
      toast.showError('Sync interval must be a number of hours (1 or more).');
      return;
    }
    setBusyInterval(true);
    try {
      onSaved(await updateSyncInterval(hours));
      toast.showInfo('Default sync interval saved.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusyInterval(false);
    }
  }

  async function toggleUserOAuth(enabled: boolean) {
    setBusyOAuthToggle(true);
    try {
      onSaved(await updateSpotifyUserOAuthEnabled(enabled));
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusyOAuthToggle(false);
    }
  }

  return (
    <SettingsSection
      title="Spotify"
      hint="Client ID/Secret from a free Spotify Developer Dashboard app let Groovarr read a playlist's name, artwork and change status without logging in. To actually read a playlist's tracks, Spotify now requires the “Connect your Spotify account” step below for every playlist — public ones included, not just private/collaborative ones."
    >
      <div className="settings-section__row">
        <StatusBadge
          status={settings.spotify_client_configured ? 'synced' : 'not_configured'}
          label={settings.spotify_client_configured ? 'Client Credentials configured' : 'Not configured'}
        />
      </div>
      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="spotify-client-id">Client ID (metadata + used by the Connect step below)</label>
          <input
            id="spotify-client-id"
            type="text"
            style={{ width: 280 }}
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="spotify-client-secret">Client Secret</label>
          <input
            id="spotify-client-secret"
            type="password"
            style={{ width: 220 }}
            placeholder={settings.spotify_client_configured ? '••••••••  (leave blank to keep)' : ''}
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
          />
        </div>
        <SaveButton busy={busyCreds} onClick={saveCredentials} label="Save Credentials" />
      </div>

      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="spotify-sync-interval">Default sync interval (hours)</label>
          <input
            id="spotify-sync-interval"
            type="text"
            inputMode="numeric"
            style={{ width: 100 }}
            value={syncInterval}
            onChange={(e) => setSyncInterval(e.target.value)}
          />
        </div>
        <SaveButton busy={busyInterval} onClick={saveInterval} />
      </div>

      <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '14px 0' }} />

      <label className="checkbox-field">
        <input
          type="checkbox"
          checked={settings.spotify_user_oauth_enabled}
          disabled={busyOAuthToggle}
          onChange={(e) => toggleUserOAuth(e.target.checked)}
        />
        Connect your Spotify account (required to read any playlist's tracks — Spotify no longer allows this
        without logging in, even for public playlists)
      </label>

      {settings.spotify_user_oauth_enabled && (
        <div className="settings-section__row" style={{ marginTop: 10 }}>
          <a className="btn btn--small" href={spotifyOAuthAuthorizeUrl()}>
            {settings.spotify_needs_reauth ? 'Reconnect your Spotify account' : 'Connect / Reconnect'}
          </a>
          {settings.spotify_needs_reauth && (
            <span className="subtle-note" style={{ color: 'var(--danger)' }}>
              Your stored authorization has expired (Spotify requires re-authorization roughly every 6 months) —
              playlist track sync is paused for every playlist until you reconnect.
            </span>
          )}
        </div>
      )}
      {!settings.spotify_user_oauth_enabled && (
        <span className="subtle-note" style={{ display: 'block', marginTop: 8 }}>
          Without this, Groovarr can still detect that a playlist changed but can't read which tracks are in it.
        </span>
      )}
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Jellyfin
// ---------------------------------------------------------------------------
function JellyfinSection({ settings, onSaved }: { settings: SettingsOut; onSaved: (s: SettingsOut) => void }) {
  const toast = useToast();
  const [enabled, setEnabled] = useState(settings.jellyfin_enabled);
  const [url, setUrl] = useState(settings.jellyfin_url ?? '');
  const [apiKey, setApiKey] = useState('');
  const [userId, setUserId] = useState(settings.jellyfin_user_id ?? '');
  const [libraryId, setLibraryId] = useState(settings.jellyfin_library_id ?? '');
  const [mediaPath, setMediaPath] = useState(settings.jellyfin_media_path ?? '');
  const [users, setUsers] = useState<JellyfinUserOut[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<'ok' | 'failed' | null>(null);

  async function save() {
    setBusy(true);
    setTestResult(null);
    try {
      const updated = await updateJellyfinConfig({
        enabled,
        url: url.trim() || null,
        api_key: apiKey.trim() || null,
        user_id: userId.trim() || null,
        library_id: libraryId.trim() || null,
        media_path: mediaPath.trim() || null,
      });
      setApiKey('');
      onSaved(updated);
      toast.showInfo('Jellyfin settings saved.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    setBusy(true);
    try {
      await testJellyfinConnection();
      setTestResult('ok');
      toast.showInfo('Jellyfin connection succeeded.');
    } catch (err) {
      setTestResult('failed');
      toast.showError(`Jellyfin connection failed: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  async function loadUsers() {
    setBusy(true);
    try {
      setUsers(await listJellyfinUsers());
    } catch (err) {
      toast.showError(`Could not load Jellyfin users: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection
      title="Jellyfin"
      hint="Zero or one Jellyfin server (§97). Playlist mutations require a specific Jellyfin user account, not just the API key — save the URL and API key first, then load the user list below."
    >
      <label className="checkbox-field" style={{ marginBottom: 12 }}>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enable Jellyfin integration
      </label>

      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="jellyfin-url">Server URL</label>
          <input
            id="jellyfin-url"
            type="url"
            style={{ width: 260 }}
            placeholder="http://jellyfin.local:8096"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="jellyfin-api-key">API Key</label>
          <input
            id="jellyfin-api-key"
            type="password"
            style={{ width: 220 }}
            placeholder={settings.jellyfin_configured ? '••••••••  (leave blank to keep)' : ''}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>
        <button type="button" className="btn btn--small" disabled={busy} onClick={testConnection}>
          Test Connection
        </button>
        {testResult && <StatusBadge status={testResult === 'ok' ? 'synced' : 'failed'} />}
      </div>

      <div className="settings-section__row">
        <div className="field" style={{ minWidth: 220 }}>
          <label htmlFor="jellyfin-user">Jellyfin user (required for playlists)</label>
          {users ? (
            <select id="jellyfin-user" value={userId} onChange={(e) => setUserId(e.target.value)}>
              <option value="">— select a user —</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
          ) : (
            <input
              id="jellyfin-user"
              type="text"
              placeholder="Load the user list below, or paste a user ID"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
            />
          )}
        </div>
        <button type="button" className="btn btn--small" disabled={busy} onClick={loadUsers}>
          Load Users
        </button>
      </div>

      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="jellyfin-library">Library ID (optional, scopes item lookup)</label>
          <input
            id="jellyfin-library"
            type="text"
            style={{ width: 200 }}
            value={libraryId}
            onChange={(e) => setLibraryId(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="jellyfin-path-remap">
            Media path as Jellyfin sees it <span className="field-hint">(only if different from Groovarr's)</span>
          </label>
          <input
            id="jellyfin-path-remap"
            type="text"
            style={{ width: 260 }}
            placeholder="/data/music-videos"
            value={mediaPath}
            onChange={(e) => setMediaPath(e.target.value)}
          />
        </div>
      </div>

      <p className="settings-section__hint" style={{ marginTop: 4 }}>
        Whether an individual Spotify playlist generates a Jellyfin playlist is controlled per-playlist on the
        Playlists page, once Jellyfin is enabled here. Whether an import/upgrade actually triggers a Jellyfin
        library refresh is controlled on the <Link to="/connect">Connect</Link> page — this section only configures
        the server itself.
      </p>

      <SaveButton busy={busy} onClick={save} />
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Plex
// ---------------------------------------------------------------------------
function PlexSection({ settings, onSaved }: { settings: SettingsOut; onSaved: (s: SettingsOut) => void }) {
  const toast = useToast();
  const [enabled, setEnabled] = useState(settings.plex_enabled);
  const [url, setUrl] = useState(settings.plex_url ?? '');
  const [token, setToken] = useState('');
  const [librarySectionId, setLibrarySectionId] = useState(settings.plex_library_section_id ?? '');
  const [mediaPath, setMediaPath] = useState(settings.plex_media_path ?? '');
  const [sections, setSections] = useState<PlexLibrarySectionOut[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<'ok' | 'failed' | null>(null);

  async function save() {
    setBusy(true);
    setTestResult(null);
    try {
      const updated = await updatePlexConfig({
        enabled,
        url: url.trim() || null,
        token: token.trim() || null,
        library_section_id: librarySectionId.trim() || null,
        media_path: mediaPath.trim() || null,
      });
      setToken('');
      onSaved(updated);
      toast.showInfo('Plex settings saved.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    setBusy(true);
    try {
      await testPlexConnection();
      setTestResult('ok');
      toast.showInfo('Plex connection succeeded.');
    } catch (err) {
      setTestResult('failed');
      toast.showError(`Plex connection failed: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  async function loadSections() {
    setBusy(true);
    try {
      setSections(await listPlexLibrarySections());
    } catch (err) {
      toast.showError(`Could not load Plex library sections: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  // Plex has no dedicated "Music Videos" library type (§2 row A) — only
  // offer sections that could plausibly hold them.
  const eligibleSections = (sections ?? []).filter((s) => s.type === 'artist' || s.type === 'movie');

  return (
    <SettingsSection
      title="Plex"
      hint="Zero or one Plex server (§97). Plex has no dedicated “Music Videos” library type — point this at the Music library (or an “Other Videos” library) that actually holds your music videos."
    >
      <label className="checkbox-field" style={{ marginBottom: 12 }}>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enable Plex integration
      </label>

      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="plex-url">Server URL</label>
          <input
            id="plex-url"
            type="url"
            style={{ width: 260 }}
            placeholder="http://plex.local:32400"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="plex-token">Token</label>
          <input
            id="plex-token"
            type="password"
            style={{ width: 220 }}
            placeholder={settings.plex_configured ? '••••••••  (leave blank to keep)' : ''}
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </div>
        <button type="button" className="btn btn--small" disabled={busy} onClick={testConnection}>
          Test Connection
        </button>
        {testResult && <StatusBadge status={testResult === 'ok' ? 'synced' : 'failed'} />}
      </div>

      <div className="settings-section__row">
        <div className="field" style={{ minWidth: 240 }}>
          <label htmlFor="plex-section">Library section (Music / Other Videos)</label>
          {sections ? (
            <select id="plex-section" value={librarySectionId} onChange={(e) => setLibrarySectionId(e.target.value)}>
              <option value="">— select a library —</option>
              {eligibleSections.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.title} ({s.type})
                </option>
              ))}
            </select>
          ) : (
            <input
              id="plex-section"
              type="text"
              placeholder="Load libraries below, or paste a section key"
              value={librarySectionId}
              onChange={(e) => setLibrarySectionId(e.target.value)}
            />
          )}
        </div>
        <button type="button" className="btn btn--small" disabled={busy} onClick={loadSections}>
          Load Libraries
        </button>
      </div>

      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="plex-path-remap">
            Media path as Plex sees it <span className="field-hint">(only if different from Groovarr's)</span>
          </label>
          <input
            id="plex-path-remap"
            type="text"
            style={{ width: 260 }}
            placeholder="/data/music-videos"
            value={mediaPath}
            onChange={(e) => setMediaPath(e.target.value)}
          />
        </div>
      </div>

      <p className="settings-section__hint" style={{ marginTop: 4 }}>
        Whether an individual Spotify playlist generates a Plex playlist is controlled per-playlist on the Playlists
        page, once Plex is enabled here. Whether an import/upgrade actually triggers a Plex library refresh is
        controlled on the <Link to="/connect">Connect</Link> page — this section only configures the server itself.
        Note: Plex silently collapses duplicate track occurrences within one playlist — this is a Plex limitation,
        not a Groovarr bug.
      </p>

      <SaveButton busy={busy} onClick={save} />
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Matching / Download / Lyrics — small, single-purpose sections.
// ---------------------------------------------------------------------------
function MatchingSection({ settings, onSaved }: { settings: SettingsOut; onSaved: (s: SettingsOut) => void }) {
  const toast = useToast();
  const [threshold, setThreshold] = useState(String(settings.automatic_match_threshold));
  const [busy, setBusy] = useState(false);

  async function save() {
    const value = Number(threshold);
    if (!Number.isFinite(value) || value < 0) {
      toast.showError('Threshold must be a non-negative number.');
      return;
    }
    setBusy(true);
    try {
      onSaved(await updateMatchingThreshold(value));
      toast.showInfo('Matching threshold saved.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection
      title="Matching"
      hint="The weighted score a candidate needs to be selected automatically rather than sent to Manual Review. Higher is stricter: precision matters more than recall here — a wrong automatic download is worse than one extra manual review."
    >
      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="matching-threshold">Automatic match threshold</label>
          <input
            id="matching-threshold"
            type="text"
            inputMode="numeric"
            style={{ width: 100 }}
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
          />
        </div>
        <SaveButton busy={busy} onClick={save} />
      </div>
    </SettingsSection>
  );
}

function DownloadSection({ settings, onSaved }: { settings: SettingsOut; onSaved: (s: SettingsOut) => void }) {
  const toast = useToast();
  const [maxConcurrent, setMaxConcurrent] = useState(String(settings.max_concurrent_downloads));
  const [maxAttempts, setMaxAttempts] = useState(String(settings.max_download_attempts));
  const [busy, setBusy] = useState(false);

  async function save() {
    const concurrent = Number(maxConcurrent);
    const attempts = Number(maxAttempts);
    if (!Number.isFinite(concurrent) || concurrent < 1 || !Number.isFinite(attempts) || attempts < 1) {
      toast.showError('Both values must be numbers of 1 or more.');
      return;
    }
    setBusy(true);
    try {
      onSaved(await updateDownloadSettings({ max_concurrent_downloads: concurrent, max_download_attempts: attempts }));
      toast.showInfo('Download settings saved.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection
      title="Download"
      hint="Bounded concurrency for the acquisition worker pool, and how many attempts a track gets before it lands on Download Failed. If Hardware Acceleration (below) is genuinely available, transcoding is dramatically faster than software-only encoding — you may be able to raise this without the CPU contention that limited it before."
    >
      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="max-concurrent">Max concurrent downloads</label>
          <input
            id="max-concurrent"
            type="text"
            inputMode="numeric"
            style={{ width: 80 }}
            value={maxConcurrent}
            onChange={(e) => setMaxConcurrent(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="max-attempts">Max retry attempts</label>
          <input
            id="max-attempts"
            type="text"
            inputMode="numeric"
            style={{ width: 80 }}
            value={maxAttempts}
            onChange={(e) => setMaxAttempts(e.target.value)}
          />
        </div>
        <SaveButton busy={busy} onClick={save} />
      </div>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Media Management — container policy. §2 row D: the project owner's
// original default ("always transcode to MP4") stays the default, but is
// now an overridable setting. The copy below must state, per the owner's
// explicit instruction, that this choice affects whether metadata is
// reliably stored/read in the video container — do not soften or omit that.
// ---------------------------------------------------------------------------
function MediaManagementSection({ settings, onSaved }: { settings: SettingsOut; onSaved: (s: SettingsOut) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function choose(policy: ContainerPolicy) {
    if (policy === settings.container_policy) return;
    setBusy(true);
    try {
      onSaved(await updateContainerPolicy(policy));
      toast.showInfo('Container policy saved.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection
      title="Media Management"
      hint="Controls what happens when a source video/audio pair isn't already MP4-compatible and a stream-copy into MP4 isn't possible. This setting directly affects whether artist/title/artwork/lyrics metadata is reliably stored in the video file itself — read both options below before changing it."
    >
      <label className="radio-field">
        <input
          type="radio"
          name="container-policy"
          checked={settings.container_policy === 'always_mp4'}
          disabled={busy}
          onChange={() => choose('always_mp4')}
        />
        <span>
          <strong>Always transcode to MP4</strong> (recommended, default) — guarantees artist/title/artwork/lyrics
          tags are reliably stored in the file and read correctly by VLC, Jellyfin, and Plex, at the cost of
          re-encoding video when the source isn't already MP4-compatible.
        </span>
      </label>
      <label className="radio-field" style={{ marginTop: 8 }}>
        <input
          type="radio"
          name="container-policy"
          checked={settings.container_policy === 'prefer_mp4_allow_mkv'}
          disabled={busy}
          onChange={() => choose('prefer_mp4_allow_mkv')}
        />
        <span>
          <strong>Prefer MP4, allow MKV</strong> — avoids re-encoding by falling back to MKV (via a lossless
          stream-copy) when needed. MKV's embedded metadata is <em>not</em> reliably read by Plex, and only
          partially by Jellyfin — files produced this way depend on the <code>.lrc</code> lyrics sidecar and
          Groovarr's own UI instead of in-app/in-container metadata.
        </span>
      </label>
    </SettingsSection>
  );
}

const HW_ACCEL_LABELS: Record<HardwareAccelPolicy, string> = {
  auto: 'Auto (recommended)',
  disabled: 'Disabled (software only)',
  nvenc: 'Force NVENC (Nvidia)',
  qsv: 'Force Quick Sync (Intel)',
  vaapi: 'Force VAAPI (Intel/AMD)',
};

function detectionLabel(detected: HardwareAccelStatusOut | null, key: 'nvenc' | 'qsv' | 'vaapi'): string {
  if (!detected) return '';
  const available = key === 'nvenc' ? detected.nvenc_available : key === 'qsv' ? detected.qsv_available : detected.vaapi_available;
  return available ? ' — detected on this host' : ' — not detected on this host';
}

function HardwareAccelerationSection({
  settings,
  onSaved,
}: {
  settings: SettingsOut;
  onSaved: (s: SettingsOut) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const detection = useApiQuery((signal) => getHardwareAccelerationDetection({ signal }));

  async function choose(policy: HardwareAccelPolicy) {
    if (policy === settings.hardware_acceleration) return;
    setBusy(true);
    try {
      onSaved(await updateHardwareAcceleration(policy));
      toast.showInfo('Hardware acceleration setting saved.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const detected = detection.data ?? null;

  return (
    <SettingsSection
      title="Hardware Acceleration"
      hint="Only relevant when a transcode is actually needed under 'Always transcode to MP4' above — Groovarr tries the selected encoder first and safely falls back to software if it fails, so this never turns a working download into a hard failure. 'Auto' uses whatever Groovarr detects as genuinely usable on this host right now; forcing a specific one is only useful if you know your setup works but auto-detection doesn't confirm it."
    >
      {detection.loading && <p className="subtle-note">Detecting available hardware encoders…</p>}
      {(['auto', 'disabled', 'nvenc', 'qsv', 'vaapi'] as HardwareAccelPolicy[]).map((policy) => (
        <label className="radio-field" key={policy} style={{ marginTop: policy === 'auto' ? 0 : 6 }}>
          <input
            type="radio"
            name="hardware-acceleration"
            checked={settings.hardware_acceleration === policy}
            disabled={busy}
            onChange={() => choose(policy)}
          />
          <span>
            <strong>{HW_ACCEL_LABELS[policy]}</strong>
            {policy !== 'auto' && policy !== 'disabled' && detected && (
              <span className="subtle-note">{detectionLabel(detected, policy)}</span>
            )}
            {policy === 'auto' && detected && (
              <span className="subtle-note">
                {' '}
                — best currently detected: {detected.best ? HW_ACCEL_LABELS[detected.best as HardwareAccelPolicy] : 'none (will use software)'}
              </span>
            )}
          </span>
        </label>
      ))}
      <p className="subtle-note" style={{ marginTop: 10 }}>
        Hardware acceleration inside Docker requires the container to actually see the host's GPU — see the
        commented-out examples in <code>docker/docker-compose.yml</code> (Intel/AMD via <code>/dev/dri</code>,
        Nvidia via the NVIDIA Container Toolkit). Groovarr correctly reports "not detected" until that's
        configured — that's expected, not a bug.
      </p>
    </SettingsSection>
  );
}

function LyricsSection({ settings, onSaved }: { settings: SettingsOut; onSaved: (s: SettingsOut) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function toggle(enabled: boolean) {
    setBusy(true);
    try {
      onSaved(await updateLyricsEnabled(enabled));
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection
      title="Lyrics"
      hint="Best-effort LRCLIB lookup, written as a .lrc sidecar next to each video. A miss or failure never blocks acquisition either way — this only controls whether the lookup happens at all."
    >
      <label className="checkbox-field">
        <input type="checkbox" checked={settings.lyrics_enabled} disabled={busy} onChange={(e) => toggle(e.target.checked)} />
        Fetch lyrics from LRCLIB
      </label>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Monitor for Better Versions — the warning must be prominent, per §36.
// ---------------------------------------------------------------------------
function MonitorBetterVersionsSection({
  settings,
  onSaved,
}: {
  settings: SettingsOut;
  onSaved: (s: SettingsOut) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function apply(enabled: boolean) {
    setBusy(true);
    try {
      onSaved(await updateMonitorBetterVersions(enabled));
      toast.showInfo(enabled ? 'Better-version monitoring enabled.' : 'Better-version monitoring disabled.');
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  function onToggle(checked: boolean) {
    if (checked) {
      setConfirming(true);
    } else {
      apply(false);
    }
  }

  return (
    <SettingsSection title="Monitor for Better Versions">
      <p className="settings-warning">{settings.monitor_better_versions_warning}</p>
      <label className="checkbox-field">
        <input
          type="checkbox"
          checked={settings.monitor_better_versions_enabled}
          disabled={busy}
          onChange={(e) => onToggle(e.target.checked)}
        />
        Enable better-version monitoring (default: off)
      </label>

      {confirming && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal">
            <h2>Enable better-version monitoring?</h2>
            <p>{settings.monitor_better_versions_warning}</p>
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setConfirming(false)} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn--primary" onClick={() => apply(true)} disabled={busy}>
                {busy ? 'Enabling…' : 'Enable Anyway'}
              </button>
            </div>
          </div>
        </div>
      )}
    </SettingsSection>
  );
}

export default function Settings() {
  const { data, loading, error, refetch } = useApiQuery((signal) => getSettings({ signal }));
  const [settings, setSettings] = useState<SettingsOut | null>(null);

  useEffect(() => {
    if (data) setSettings(data);
  }, [data]);

  if (loading && !settings) {
    return (
      <section>
        <h1>Settings</h1>
        <p className="subtle-note">Loading…</p>
      </section>
    );
  }

  if (error && !settings) {
    return (
      <section>
        <h1>Settings</h1>
        <ErrorBanner message={`Failed to load settings: ${error}`} onRetry={refetch} />
      </section>
    );
  }

  if (!settings) return null;

  return (
    <section>
      <div className="page-toolbar">
        <h1>Settings</h1>
      </div>

      <SpotifySection settings={settings} onSaved={setSettings} />
      <JellyfinSection settings={settings} onSaved={setSettings} />
      <PlexSection settings={settings} onSaved={setSettings} />
      <MatchingSection settings={settings} onSaved={setSettings} />
      <DownloadSection settings={settings} onSaved={setSettings} />
      <MediaManagementSection settings={settings} onSaved={setSettings} />
      <HardwareAccelerationSection settings={settings} onSaved={setSettings} />
      <LyricsSection settings={settings} onSaved={setSettings} />
      <MonitorBetterVersionsSection settings={settings} onSaved={setSettings} />

      <SettingsSection title="General">
        <p className="settings-section__hint">
          Logs and dependency versions live on the <Link to="/system">System / Status</Link> page.
        </p>
        <p className="settings-section__hint" style={{ marginBottom: 0 }}>
          New to Groovarr, or want to re-check a connection step by step? <Link to="/setup">Run the Setup Wizard</Link>{' '}
          — it walks through Spotify, Jellyfin and Plex using these same settings, then drops you back here.
        </p>
      </SettingsSection>
    </section>
  );
}
