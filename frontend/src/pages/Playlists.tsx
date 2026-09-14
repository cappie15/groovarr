import { useEffect, useMemo, useState } from 'react';
import {
  type ExternalPlaylistOut,
  type ExternalPlatform,
  type PlaylistOut,
  connectPlaylist,
  disconnectPlaylist,
  getExternalStatus,
  listPlaylists,
  resolveCollision,
  syncExternalPlaylist,
  syncPlaylistNow,
  updateExternalConfig,
} from '../api/playlists';
import { ApiError } from '../api/client';
import { DataTable, type DataTableColumn } from '../components/DataTable';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { useApiQuery } from '../hooks/useApiQuery';
import { useToast } from '../components/Toast';

function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Unexpected error — please try again.';
}

/** Per-platform Jellyfin/Plex row: enable toggle, current sync status once
 * known, and — critically — the §59 name-collision resolution UI when
 * `sync_state === 'needs_resolution'` rather than silently hiding it. */
function ExternalPlatformControl({
  spotifyId,
  platform,
  enabled,
  nameOverride,
  status,
  onChanged,
}: {
  spotifyId: string;
  platform: ExternalPlatform;
  enabled: boolean;
  nameOverride: string | null;
  status: ExternalPlaylistOut | undefined;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState(nameOverride ?? '');

  async function toggle() {
    setBusy(true);
    try {
      await updateExternalConfig(
        spotifyId,
        platform === 'jellyfin' ? { jellyfin_enabled: !enabled } : { plex_enabled: !enabled },
      );
      onChanged();
    } catch (err) {
      toast.showError(`Failed to update ${platform} setting: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  async function adopt() {
    setBusy(true);
    try {
      await resolveCollision(spotifyId, platform, 'adopt');
      toast.showInfo(`Adopted the existing ${platform} playlist — it will be rebuilt to match Spotify.`);
      onChanged();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function useNewName() {
    setBusy(true);
    try {
      await updateExternalConfig(
        spotifyId,
        platform === 'jellyfin'
          ? { jellyfin_name_override: newName || null }
          : { plex_name_override: newName || null },
      );
      await resolveCollision(spotifyId, platform, 'cancel');
      await syncExternalPlaylist(spotifyId);
      setRenaming(false);
      onChanged();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const platformLabel = platform === 'jellyfin' ? 'Jellyfin' : 'Plex';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 150 }}>
      <label className="checkbox-field">
        <input type="checkbox" checked={enabled} disabled={busy} onChange={toggle} />
        {platformLabel}
      </label>
      {enabled && status && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <StatusBadge status={status.sync_state} />
          {status.duplicates_collapsed_count != null && status.duplicates_collapsed_count > 0 && (
            <span className="subtle-note" title="Plex does not support duplicate entries in one playlist.">
              {status.duplicates_collapsed_count} duplicate(s) collapsed
            </span>
          )}
        </div>
      )}
      {enabled && status?.sync_state === 'needs_resolution' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 2 }}>
          <span className="subtle-note">
            A same-named playlist already exists on {platformLabel} and isn't managed by Groovarr.
          </span>
          {!renaming ? (
            <div className="btn-row">
              <button type="button" className="btn btn--small" disabled={busy} onClick={adopt}>
                Adopt existing
              </button>
              <button type="button" className="btn btn--small" disabled={busy} onClick={() => setRenaming(true)}>
                Use a different name
              </button>
            </div>
          ) : (
            <div className="inline-form">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={`${platformLabel} playlist name`}
              />
              <button type="button" className="btn btn--small btn--primary" disabled={busy} onClick={useNewName}>
                Save &amp; retry
              </button>
              <button type="button" className="btn btn--small" disabled={busy} onClick={() => setRenaming(false)}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConnectPlaylistForm({ onConnected }: { onConnected: () => void }) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await connectPlaylist(value.trim());
      setValue('');
      onConnected();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="inline-form" onSubmit={submit} style={{ marginBottom: error ? 8 : 20 }}>
      <div className="field">
        <label htmlFor="connect-playlist-input">Connect a Spotify playlist</label>
        <input
          id="connect-playlist-input"
          type="text"
          style={{ width: 380 }}
          placeholder="Playlist URL, URI, or ID (requires Spotify connected in Settings)"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={busy}
        />
      </div>
      <button type="submit" className="btn btn--primary" disabled={busy || !value.trim()}>
        {busy ? 'Connecting…' : 'Connect'}
      </button>
    </form>
  );
}

function DisconnectConfirmModal({
  playlist,
  onCancel,
  onConfirmed,
}: {
  playlist: PlaylistOut;
  onCancel: () => void;
  onConfirmed: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  async function confirm() {
    setBusy(true);
    try {
      await disconnectPlaylist(playlist.spotify_id);
      onConfirmed();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true">
      <div className="modal">
        <h2>Disconnect &amp; finalize “{playlist.name}”?</h2>
        <p>
          Groovarr will stop applying future Spotify changes to this playlist. This does{' '}
          <strong>not</strong> delete any downloaded media, and does not empty or remove the
          Jellyfin/Plex playlist — everything stays exactly as it is right now.
        </p>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn--primary" onClick={confirm} disabled={busy}>
            {busy ? 'Disconnecting…' : 'Disconnect & Finalize'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Playlists() {
  const { data, loading, error, refetch } = useApiQuery((signal) => listPlaylists({ limit: 200 }, { signal }));
  const toast = useToast();
  const [externalByPlaylist, setExternalByPlaylist] = useState<Record<string, ExternalPlaylistOut[]>>({});
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState<PlaylistOut | null>(null);

  const playlistsWithExternal = useMemo(
    () => (data ?? []).filter((p) => p.jellyfin_enabled || p.plex_enabled),
    [data],
  );

  useEffect(() => {
    let cancelled = false;
    async function loadExternalStatus() {
      const entries = await Promise.all(
        playlistsWithExternal.map(async (p) => {
          try {
            return [p.spotify_id, await getExternalStatus(p.spotify_id)] as const;
          } catch {
            return [p.spotify_id, []] as const;
          }
        }),
      );
      if (!cancelled) setExternalByPlaylist(Object.fromEntries(entries));
    }
    if (playlistsWithExternal.length > 0) void loadExternalStatus();
    return () => {
      cancelled = true;
    };
    // Re-run whenever the enabled-set/spotify_ids change (a full playlists refetch).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playlistsWithExternal.map((p) => p.spotify_id).join(',')]);

  async function handleSyncNow(spotifyId: string) {
    setSyncingId(spotifyId);
    try {
      await syncPlaylistNow(spotifyId);
      toast.showInfo('Playlist synced with Spotify.');
      refetch();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setSyncingId(null);
    }
  }

  const columns: DataTableColumn<PlaylistOut>[] = [
    {
      key: 'name',
      header: 'Playlist',
      sortAccessor: (p) => p.name.toLowerCase(),
      render: (p) => (
        <div>
          <div style={{ fontWeight: 600 }}>{p.name}</div>
          <div className="subtle-note">{p.track_count} track(s)</div>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (p) =>
        p.finalized ? (
          <StatusBadge status="finalized" label="Finalized" />
        ) : p.connected ? (
          <StatusBadge status="synced" label="Connected" />
        ) : (
          <StatusBadge status="not_configured" label="Disconnected" />
        ),
    },
    {
      key: 'sync',
      header: 'Sync',
      render: (p) => (
        <div className="subtle-note">
          Every {p.sync_interval_hours}h
          <br />
          Last: {formatDateTime(p.last_synced_at)}
          <br />
          Next: {p.finalized ? '—' : formatDateTime(p.next_sync_at)}
        </div>
      ),
    },
    {
      key: 'jellyfin',
      header: 'Jellyfin',
      render: (p) => (
        <ExternalPlatformControl
          spotifyId={p.spotify_id}
          platform="jellyfin"
          enabled={p.jellyfin_enabled}
          nameOverride={p.jellyfin_name_override}
          status={externalByPlaylist[p.spotify_id]?.find((s) => s.platform === 'jellyfin')}
          onChanged={refetch}
        />
      ),
    },
    {
      key: 'plex',
      header: 'Plex',
      render: (p) => (
        <ExternalPlatformControl
          spotifyId={p.spotify_id}
          platform="plex"
          enabled={p.plex_enabled}
          nameOverride={p.plex_name_override}
          status={externalByPlaylist[p.spotify_id]?.find((s) => s.platform === 'plex')}
          onChanged={refetch}
        />
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (p) => (
        <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
          {!p.finalized && (
            <>
              <button
                type="button"
                className="btn btn--small"
                disabled={syncingId === p.spotify_id}
                onClick={() => handleSyncNow(p.spotify_id)}
              >
                {syncingId === p.spotify_id ? 'Syncing…' : 'Sync Now'}
              </button>
              <button type="button" className="btn btn--small btn--danger" onClick={() => setConfirmDisconnect(p)}>
                Disconnect
              </button>
            </>
          )}
        </div>
      ),
    },
  ];

  return (
    <section>
      <div className="page-toolbar">
        <h1>Playlists</h1>
      </div>

      <ConnectPlaylistForm onConnected={refetch} />

      {error && <ErrorBanner message={`Failed to load playlists: ${error}`} onRetry={refetch} />}

      <DataTable
        columns={columns}
        rows={data ?? []}
        getRowKey={(p) => p.spotify_id}
        loading={loading}
        emptyMessage="No playlists connected yet — paste a public Spotify playlist link above to get started."
      />

      {confirmDisconnect && (
        <DisconnectConfirmModal
          playlist={confirmDisconnect}
          onCancel={() => setConfirmDisconnect(null)}
          onConfirmed={() => {
            setConfirmDisconnect(null);
            refetch();
          }}
        />
      )}
    </section>
  );
}
