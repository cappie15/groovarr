import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  getSettings,
  listJellyfinUsers,
  listPlexLibrarySections,
  spotifyOAuthAuthorizeUrl,
  testJellyfinConnection,
  testPlexConnection,
  updateJellyfinConfig,
  updatePlexConfig,
  updateSpotifyCredentials,
  updateSpotifyUserOAuthEnabled,
  type JellyfinUserOut,
  type PlexLibrarySectionOut,
  type SettingsOut,
} from '../api/settings';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { useToast } from '../components/Toast';
import { useApiQuery } from '../hooks/useApiQuery';
import { dismissSetupWizard } from '../lib/onboarding';
import { errorMessage, SettingsSection } from './Settings';

/**
 * First-run Setup Wizard — a guided, step-by-step alternative to the flat
 * Settings page for a brand-new install (Spotify, then Jellyfin, then Plex,
 * then a short summary), each step skippable. It is deliberately *not* a
 * separate settings-saving path: every step calls the exact same
 * api/settings.ts functions (and reuses `SettingsSection`'s card look, plus
 * that page's hint copy) that the flat Settings page already uses, so
 * there is exactly one place that knows how to persist a setting. This page
 * is purely a guided front door onto it — auto-triggered from App.tsx on a
 * fresh install, reachable again any time from Settings → General, and
 * always dismissible without configuring anything.
 */

type StepKey = 'spotify' | 'jellyfin' | 'plex' | 'done';

const STEPS: { key: StepKey; label: string }[] = [
  { key: 'spotify', label: 'Spotify' },
  { key: 'jellyfin', label: 'Jellyfin' },
  { key: 'plex', label: 'Plex' },
  { key: 'done', label: 'Done' },
];

function WizardSteps({ activeIndex }: { activeIndex: number }) {
  return (
    <ol className="wizard-steps">
      {STEPS.map((step, i) => (
        <li
          key={step.key}
          className={
            'wizard-steps__item' +
            (i === activeIndex ? ' wizard-steps__item--active' : i < activeIndex ? ' wizard-steps__item--done' : '')
          }
        >
          <span className="wizard-steps__dot">{i < activeIndex ? '✓' : i + 1}</span>
          <span className="wizard-steps__label">{step.label}</span>
        </li>
      ))}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Step 1: Spotify — copy adapted from Settings.tsx's SpotifySection, trimmed
// to what a first-run operator needs to decide right now.
// ---------------------------------------------------------------------------
function SpotifyStep({
  settings,
  onSaved,
  onNext,
}: {
  settings: SettingsOut;
  onSaved: (s: SettingsOut) => void;
  onNext: () => void;
}) {
  const toast = useToast();
  const [clientId, setClientId] = useState(settings.spotify_client_id ?? '');
  const [clientSecret, setClientSecret] = useState('');
  const [busy, setBusy] = useState(false);

  const hasNewCreds = clientId.trim().length > 0 && clientSecret.trim().length > 0;

  async function saveAndContinue() {
    if (!hasNewCreds) {
      onNext();
      return;
    }
    setBusy(true);
    try {
      const updated = await updateSpotifyCredentials({ client_id: clientId.trim(), client_secret: clientSecret });
      setClientSecret('');
      onSaved(updated);
      toast.showInfo('Spotify credentials saved.');
      onNext();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleUserOAuth(enabled: boolean) {
    setBusy(true);
    try {
      onSaved(await updateSpotifyUserOAuthEnabled(enabled));
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection
      title="Spotify"
      hint="Client ID/Secret from a free Spotify Developer Dashboard app let Groovarr read a playlist's name, artwork and change status. To actually read a playlist's tracks, Spotify now requires the “Connect your Spotify account” step below for every playlist — public ones included, not just private/collaborative ones. Without any of this, Groovarr has nothing to sync yet."
    >
      <div className="settings-section__row">
        <StatusBadge
          status={settings.spotify_client_configured ? 'synced' : 'not_configured'}
          label={settings.spotify_client_configured ? 'Client Credentials configured' : 'Not configured yet'}
        />
      </div>
      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="wizard-spotify-client-id">Client ID</label>
          <input
            id="wizard-spotify-client-id"
            type="text"
            style={{ width: 280 }}
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="wizard-spotify-client-secret">Client Secret</label>
          <input
            id="wizard-spotify-client-secret"
            type="password"
            style={{ width: 220 }}
            placeholder={settings.spotify_client_configured ? '••••••••  (leave blank to keep)' : ''}
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
          />
        </div>
      </div>

      <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '14px 0' }} />

      <label className="checkbox-field">
        <input
          type="checkbox"
          checked={settings.spotify_user_oauth_enabled}
          disabled={busy}
          onChange={(e) => toggleUserOAuth(e.target.checked)}
        />
        Connect your Spotify account (required to read any playlist's tracks)
      </label>

      {settings.spotify_user_oauth_enabled && (
        <div className="settings-section__row" style={{ marginTop: 10 }}>
          <a className="btn btn--small" href={spotifyOAuthAuthorizeUrl()}>
            {settings.spotify_needs_reauth ? 'Reconnect your Spotify account' : 'Connect / Reconnect'}
          </a>
          <span className="subtle-note">Opens Spotify in this tab — come back to this wizard afterward.</span>
        </div>
      )}

      <div className="wizard-nav">
        <span />
        <div className="btn-row">
          <button type="button" className="btn btn--small" disabled={busy} onClick={onNext}>
            Skip this step
          </button>
          <button type="button" className="btn btn--primary btn--small" disabled={busy} onClick={saveAndContinue}>
            {busy ? 'Saving…' : hasNewCreds ? 'Save & Continue' : 'Continue'}
          </button>
        </div>
      </div>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Step 2: Jellyfin — URL / API key / user only (library ID and the media
// path remap are advanced knobs left to the flat Settings page, per the
// wizard's scope).
// ---------------------------------------------------------------------------
function JellyfinStep({
  settings,
  onSaved,
  onBack,
  onNext,
}: {
  settings: SettingsOut;
  onSaved: (s: SettingsOut) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const toast = useToast();
  const [enabled, setEnabled] = useState(settings.jellyfin_enabled);
  const [url, setUrl] = useState(settings.jellyfin_url ?? '');
  const [apiKey, setApiKey] = useState('');
  const [userId, setUserId] = useState(settings.jellyfin_user_id ?? '');
  const [users, setUsers] = useState<JellyfinUserOut[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<'ok' | 'failed' | null>(null);

  async function saveAndContinue() {
    setBusy(true);
    setTestResult(null);
    try {
      const updated = await updateJellyfinConfig({
        enabled,
        url: url.trim() || null,
        api_key: apiKey.trim() || null,
        user_id: userId.trim() || null,
        library_id: settings.jellyfin_library_id,
        media_path: settings.jellyfin_media_path,
      });
      setApiKey('');
      onSaved(updated);
      toast.showInfo('Jellyfin settings saved.');
      onNext();
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
      hint="Optional — skip this step if you don't run Jellyfin. Playlist mutations require a specific Jellyfin user account, not just the API key: save the URL and API key first, then load the user list below. Library ID and path-remap overrides are advanced options available later on the flat Settings page."
    >
      <label className="checkbox-field" style={{ marginBottom: 12 }}>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enable Jellyfin integration
      </label>

      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="wizard-jellyfin-url">Server URL</label>
          <input
            id="wizard-jellyfin-url"
            type="url"
            style={{ width: 260 }}
            placeholder="http://jellyfin.local:8096"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="wizard-jellyfin-api-key">API Key</label>
          <input
            id="wizard-jellyfin-api-key"
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
          <label htmlFor="wizard-jellyfin-user">Jellyfin user (required for playlists)</label>
          {users ? (
            <select id="wizard-jellyfin-user" value={userId} onChange={(e) => setUserId(e.target.value)}>
              <option value="">— select a user —</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
          ) : (
            <input
              id="wizard-jellyfin-user"
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

      <div className="wizard-nav">
        <button type="button" className="btn btn--small" disabled={busy} onClick={onBack}>
          Back
        </button>
        <div className="btn-row">
          <button type="button" className="btn btn--small" disabled={busy} onClick={onNext}>
            Skip this step
          </button>
          <button type="button" className="btn btn--primary btn--small" disabled={busy} onClick={saveAndContinue}>
            {busy ? 'Saving…' : 'Save & Continue'}
          </button>
        </div>
      </div>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Step 3: Plex — URL / token / library section only, same scope trim as
// the Jellyfin step.
// ---------------------------------------------------------------------------
function PlexStep({
  settings,
  onSaved,
  onBack,
  onNext,
}: {
  settings: SettingsOut;
  onSaved: (s: SettingsOut) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const toast = useToast();
  const [enabled, setEnabled] = useState(settings.plex_enabled);
  const [url, setUrl] = useState(settings.plex_url ?? '');
  const [token, setToken] = useState('');
  const [librarySectionId, setLibrarySectionId] = useState(settings.plex_library_section_id ?? '');
  const [sections, setSections] = useState<PlexLibrarySectionOut[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<'ok' | 'failed' | null>(null);

  async function saveAndContinue() {
    setBusy(true);
    setTestResult(null);
    try {
      const updated = await updatePlexConfig({
        enabled,
        url: url.trim() || null,
        token: token.trim() || null,
        library_section_id: librarySectionId.trim() || null,
        media_path: settings.plex_media_path,
      });
      setToken('');
      onSaved(updated);
      toast.showInfo('Plex settings saved.');
      onNext();
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

  // Plex has no dedicated "Music Videos" library type — only offer sections
  // that could plausibly hold them (same filter as the flat Settings page).
  const eligibleSections = (sections ?? []).filter((s) => s.type === 'artist' || s.type === 'movie');

  return (
    <SettingsSection
      title="Plex"
      hint="Optional — skip this step if you don't run Plex. Plex has no dedicated “Music Videos” library type — point this at the Music library (or an “Other Videos” library) that actually holds your music videos. Path-remap overrides are an advanced option available later on the flat Settings page."
    >
      <label className="checkbox-field" style={{ marginBottom: 12 }}>
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enable Plex integration
      </label>

      <div className="settings-section__row">
        <div className="field">
          <label htmlFor="wizard-plex-url">Server URL</label>
          <input
            id="wizard-plex-url"
            type="url"
            style={{ width: 260 }}
            placeholder="http://plex.local:32400"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="wizard-plex-token">Token</label>
          <input
            id="wizard-plex-token"
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
          <label htmlFor="wizard-plex-section">Library section (Music / Other Videos)</label>
          {sections ? (
            <select
              id="wizard-plex-section"
              value={librarySectionId}
              onChange={(e) => setLibrarySectionId(e.target.value)}
            >
              <option value="">— select a library —</option>
              {eligibleSections.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.title} ({s.type})
                </option>
              ))}
            </select>
          ) : (
            <input
              id="wizard-plex-section"
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

      <div className="wizard-nav">
        <button type="button" className="btn btn--small" disabled={busy} onClick={onBack}>
          Back
        </button>
        <div className="btn-row">
          <button type="button" className="btn btn--small" disabled={busy} onClick={onNext}>
            Skip this step
          </button>
          <button type="button" className="btn btn--primary btn--small" disabled={busy} onClick={saveAndContinue}>
            {busy ? 'Saving…' : 'Save & Continue'}
          </button>
        </div>
      </div>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Step 4: Done — a short summary, not another settings form.
// ---------------------------------------------------------------------------
function DoneStep({
  settings,
  onBack,
  onFinish,
}: {
  settings: SettingsOut;
  onBack: () => void;
  onFinish: () => void;
}) {
  return (
    <SettingsSection title="You're all set">
      <p className="settings-section__hint">
        You can change any of this later from <Link to="/settings">Settings</Link>, or run this wizard again from
        its General section.
      </p>

      <ul className="wizard-done-list">
        <li className="wizard-done-list__row">
          <span className="wizard-done-list__label">Spotify</span>
          <StatusBadge
            status={settings.spotify_client_configured ? 'synced' : 'not_configured'}
            label={settings.spotify_client_configured ? 'Client Credentials configured' : 'Not configured'}
          />
          {settings.spotify_client_configured && (
            <StatusBadge
              status={settings.spotify_user_oauth_enabled && !settings.spotify_needs_reauth ? 'synced' : 'not_configured'}
              label={
                settings.spotify_user_oauth_enabled
                  ? settings.spotify_needs_reauth
                    ? 'Account needs reconnect'
                    : 'Account connected'
                  : 'Account not connected'
              }
            />
          )}
        </li>
        <li className="wizard-done-list__row">
          <span className="wizard-done-list__label">Jellyfin</span>
          <StatusBadge
            status={settings.jellyfin_enabled && settings.jellyfin_configured ? 'synced' : 'not_configured'}
            label={settings.jellyfin_enabled && settings.jellyfin_configured ? 'Configured' : 'Skipped'}
          />
        </li>
        <li className="wizard-done-list__row">
          <span className="wizard-done-list__label">Plex</span>
          <StatusBadge
            status={settings.plex_enabled && settings.plex_configured ? 'synced' : 'not_configured'}
            label={settings.plex_enabled && settings.plex_configured ? 'Configured' : 'Skipped'}
          />
        </li>
      </ul>

      <p className="settings-section__hint">
        Next: connect a real Spotify playlist (or your Liked Songs) from the <Link to="/playlists">Playlists</Link>{' '}
        page — that's what actually gives Groovarr something to sync and acquire.
      </p>

      <div className="wizard-nav">
        <button type="button" className="btn btn--small" onClick={onBack}>
          Back
        </button>
        <button type="button" className="btn btn--primary btn--small" onClick={onFinish}>
          Finish
        </button>
      </div>
    </SettingsSection>
  );
}

export default function SetupWizard() {
  const navigate = useNavigate();
  const { data, loading, error, refetch } = useApiQuery((signal) => getSettings({ signal }));
  const [settings, setSettings] = useState<SettingsOut | null>(null);
  const [stepIndex, setStepIndex] = useState(0);

  // Mirrors Settings.tsx's own pattern: seed local state from the query
  // result once, then let each step's `onSaved` update it optimistically
  // from here on rather than re-deriving from `data` on every render.
  useEffect(() => {
    if (data) setSettings(data);
  }, [data]);

  function exitWizard(path: string) {
    dismissSetupWizard();
    navigate(path);
  }

  if (loading && !settings) {
    return (
      <section className="wizard-page">
        <h1>Setup Wizard</h1>
        <p className="subtle-note">Loading…</p>
      </section>
    );
  }

  if (error && !settings) {
    return (
      <section className="wizard-page">
        <h1>Setup Wizard</h1>
        <ErrorBanner message={`Failed to load settings: ${error}`} onRetry={refetch} />
      </section>
    );
  }

  if (!settings) return null;

  const activeKey = STEPS[stepIndex].key;

  return (
    <section className="wizard-page">
      <div className="wizard-topbar">
        <div>
          <h1>Setup Wizard</h1>
          <p className="wizard-topbar__intro subtle-note">
            Connect Spotify, then optionally Jellyfin and/or Plex. Everything here lives on the regular Settings
            page too — this is just a guided way through it the first time.
          </p>
        </div>
        <button type="button" className="btn btn--small" onClick={() => exitWizard('/')}>
          Skip setup
        </button>
      </div>

      <WizardSteps activeIndex={stepIndex} />

      {activeKey === 'spotify' && (
        <SpotifyStep settings={settings} onSaved={setSettings} onNext={() => setStepIndex(1)} />
      )}
      {activeKey === 'jellyfin' && (
        <JellyfinStep
          settings={settings}
          onSaved={setSettings}
          onBack={() => setStepIndex(0)}
          onNext={() => setStepIndex(2)}
        />
      )}
      {activeKey === 'plex' && (
        <PlexStep
          settings={settings}
          onSaved={setSettings}
          onBack={() => setStepIndex(1)}
          onNext={() => setStepIndex(3)}
        />
      )}
      {activeKey === 'done' && (
        <DoneStep settings={settings} onBack={() => setStepIndex(2)} onFinish={() => exitWizard('/')} />
      )}
    </section>
  );
}
