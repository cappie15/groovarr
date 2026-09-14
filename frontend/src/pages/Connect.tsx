import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import {
  createNotificationConnection,
  deleteNotificationConnection,
  listNotificationConnections,
  listNotificationEventTypes,
  testNotificationConnection,
  updateNotificationConnection,
  type NotificationConnectionOut,
  type NotificationEventTypeOut,
  type NotificationProvider,
} from '../api/notifications';
import { DataTable, type DataTableColumn } from '../components/DataTable';
import { ErrorBanner } from '../components/ErrorBanner';
import { StatusBadge } from '../components/StatusBadge';
import { useToast } from '../components/Toast';
import { useApiQuery } from '../hooks/useApiQuery';
import { useModalA11y } from '../hooks/useModalA11y';

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Unexpected error — please try again.';
}

const PROVIDER_LABELS: Record<NotificationProvider, string> = {
  jellyfin: 'Jellyfin',
  plex: 'Plex',
};

// The two events a Jellyfin/Plex connection needs to preserve today's
// automatic post-import/-upgrade library refresh (see
// app.services.notifications.MEDIA_SERVER_REFRESH_EVENTS on the backend) —
// called out in the editor so a new connection isn't accidentally created
// with nothing meaningful subscribed.
const MEDIA_SERVER_REFRESH_EVENTS = ['acquisition.imported', 'replacement.replaced'];

interface ConnectionFormState {
  id: number | null;
  name: string;
  provider: NotificationProvider;
  enabled: boolean;
  eventTypes: Set<string>;
}

function emptyForm(): ConnectionFormState {
  return { id: null, name: '', provider: 'jellyfin', enabled: true, eventTypes: new Set(MEDIA_SERVER_REFRESH_EVENTS) };
}

function ConnectionEditor({
  form,
  eventTypes,
  onChange,
  onCancel,
  onSave,
  busy,
}: {
  form: ConnectionFormState;
  eventTypes: NotificationEventTypeOut[];
  onChange: (form: ConnectionFormState) => void;
  onCancel: () => void;
  onSave: () => void;
  busy: boolean;
}) {
  const modalRef = useModalA11y<HTMLDivElement>(true, onCancel);

  function toggleEvent(eventType: string) {
    const next = new Set(form.eventTypes);
    if (next.has(eventType)) next.delete(eventType);
    else next.add(eventType);
    onChange({ ...form, eventTypes: next });
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="connection-editor-title">
      <div className="modal" style={{ width: 540, maxWidth: '90vw' }} ref={modalRef} tabIndex={-1}>
        <h2 id="connection-editor-title">{form.id === null ? 'Add Connection' : `Edit ${form.name || 'Connection'}`}</h2>

        <div className="settings-section__row">
          <div className="field" style={{ flex: 1 }}>
            <label htmlFor="connection-name">Name</label>
            <input
              id="connection-name"
              type="text"
              placeholder="e.g. Jellyfin"
              value={form.name}
              onChange={(e) => onChange({ ...form, name: e.target.value })}
              autoFocus
            />
          </div>
          <div className="field">
            <label htmlFor="connection-provider">Provider</label>
            <select
              id="connection-provider"
              value={form.provider}
              disabled={form.id !== null}
              onChange={(e) => onChange({ ...form, provider: e.target.value as NotificationProvider })}
            >
              <option value="jellyfin">Jellyfin</option>
              <option value="plex">Plex</option>
            </select>
          </div>
        </div>

        <label className="checkbox-field" style={{ marginTop: 4 }}>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => onChange({ ...form, enabled: e.target.checked })}
          />
          Enabled
        </label>

        <p className="settings-section__hint" style={{ marginTop: 14, marginBottom: 6 }}>
          Trigger this connection on:
        </p>
        <div
          style={{
            maxHeight: 260,
            overflowY: 'auto',
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: '10px 12px',
          }}
        >
          {eventTypes.map((et) => (
            <label className="checkbox-field" key={et.event_type} style={{ marginBottom: 6 }}>
              <input
                type="checkbox"
                checked={form.eventTypes.has(et.event_type)}
                onChange={() => toggleEvent(et.event_type)}
              />
              {et.label}
              {MEDIA_SERVER_REFRESH_EVENTS.includes(et.event_type) && (
                <span className="field-hint"> (drives today's library refresh)</span>
              )}
            </label>
          ))}
          {eventTypes.length === 0 && <p className="subtle-note">Loading event types…</p>}
        </div>
        <p className="subtle-note" style={{ marginTop: 8 }}>
          For Jellyfin/Plex specifically, "notify" means triggering that server's library refresh — there's no
          separate message to configure. A connection with nothing selected is saved but will never fire.
        </p>

        <div className="modal-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn--primary" onClick={onSave} disabled={busy || !form.name.trim()}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Connect() {
  const toast = useToast();
  const connectionsQuery = useApiQuery((signal) => listNotificationConnections({ signal }));
  const eventTypesQuery = useApiQuery((signal) => listNotificationEventTypes({ signal }));

  const [editing, setEditing] = useState<ConnectionFormState | null>(null);
  const [busy, setBusy] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);

  const connections = connectionsQuery.data ?? [];
  const eventTypes = eventTypesQuery.data ?? [];

  function openEdit(row: NotificationConnectionOut) {
    setEditing({
      id: row.id,
      name: row.name,
      provider: row.provider,
      enabled: row.enabled,
      eventTypes: new Set(row.event_types),
    });
  }

  async function save() {
    if (!editing || !editing.name.trim()) return;
    setBusy(true);
    try {
      if (editing.id === null) {
        await createNotificationConnection({
          name: editing.name.trim(),
          provider: editing.provider,
          enabled: editing.enabled,
          event_types: Array.from(editing.eventTypes),
        });
        toast.showInfo('Connection created.');
      } else {
        await updateNotificationConnection(editing.id, {
          name: editing.name.trim(),
          enabled: editing.enabled,
          event_types: Array.from(editing.eventTypes),
        });
        toast.showInfo('Connection saved.');
      }
      setEditing(null);
      connectionsQuery.refetch();
    } catch (err) {
      toast.showError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(row: NotificationConnectionOut) {
    try {
      await updateNotificationConnection(row.id, { enabled: !row.enabled });
      connectionsQuery.refetch();
    } catch (err) {
      toast.showError(errorMessage(err));
    }
  }

  async function remove(row: NotificationConnectionOut) {
    if (!window.confirm(`Delete the connection "${row.name}"? This cannot be undone.`)) return;
    try {
      await deleteNotificationConnection(row.id);
      toast.showInfo('Connection deleted.');
      connectionsQuery.refetch();
    } catch (err) {
      toast.showError(errorMessage(err));
    }
  }

  async function test(row: NotificationConnectionOut) {
    setTestingId(row.id);
    try {
      await testNotificationConnection(row.id);
      toast.showInfo(`${row.name}: connection succeeded.`);
    } catch (err) {
      toast.showError(`${row.name}: ${errorMessage(err)}`);
    } finally {
      setTestingId(null);
    }
  }

  const columns: DataTableColumn<NotificationConnectionOut>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (row) => <strong>{row.name}</strong>,
      sortAccessor: (row) => row.name.toLowerCase(),
    },
    {
      key: 'provider',
      header: 'Provider',
      render: (row) => PROVIDER_LABELS[row.provider],
      sortAccessor: (row) => row.provider,
    },
    {
      key: 'enabled',
      header: 'Status',
      render: (row) => (
        <StatusBadge status={row.enabled ? 'synced' : 'not_configured'} label={row.enabled ? 'Enabled' : 'Disabled'} />
      ),
      sortAccessor: (row) => (row.enabled ? 0 : 1),
    },
    {
      key: 'events',
      header: 'Triggers on',
      render: (row) =>
        row.event_types.length === 0 ? (
          <span className="subtle-note">Nothing selected — will never fire</span>
        ) : (
          `${row.event_types.length} event${row.event_types.length === 1 ? '' : 's'}`
        ),
      sortAccessor: (row) => row.event_types.length,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (row) => (
        <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn btn--small" disabled={testingId === row.id} onClick={() => test(row)}>
            {testingId === row.id ? 'Testing…' : 'Test'}
          </button>
          <button type="button" className="btn btn--small" onClick={() => toggleEnabled(row)}>
            {row.enabled ? 'Disable' : 'Enable'}
          </button>
          <button type="button" className="btn btn--small" onClick={() => openEdit(row)}>
            Edit
          </button>
          <button type="button" className="btn btn--small btn--danger" onClick={() => remove(row)}>
            Delete
          </button>
        </div>
      ),
    },
  ];

  return (
    <section>
      <div className="page-toolbar">
        <h1>Connect</h1>
        <button type="button" className="btn btn--primary" onClick={() => setEditing(emptyForm())}>
          Add Connection
        </button>
      </div>

      <p className="settings-section__hint">
        Named connections to Jellyfin/Plex, each subscribed to the specific events that should trigger it —
        Sonarr/Radarr-style, in place of the previous always-on refresh. The server itself (URL, API key/token,
        library) is still configured on the <Link to="/settings">Settings</Link> page; a connection here only says
        which of those already-configured servers get notified, and on which events.
      </p>

      {connectionsQuery.error && !connectionsQuery.data && (
        <ErrorBanner
          message={`Failed to load connections: ${connectionsQuery.error}`}
          onRetry={connectionsQuery.refetch}
        />
      )}

      <DataTable
        columns={columns}
        rows={connections}
        getRowKey={(row) => row.id}
        loading={connectionsQuery.loading}
        emptyMessage="No connections yet — add one to notify Jellyfin or Plex on library events."
      />

      {editing && (
        <ConnectionEditor
          form={editing}
          eventTypes={eventTypes}
          onChange={setEditing}
          onCancel={() => setEditing(null)}
          onSave={save}
          busy={busy}
        />
      )}
    </section>
  );
}
