"""notification_connections

Revision ID: 8f7970353c4e
Revises: a8642b6713c1
Create Date: 2026-09-14 21:30:00.000000

"""
import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f7970353c4e'
down_revision: Union[str, Sequence[str], None] = 'a8642b6713c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The exact two events that trigger a Jellyfin/Plex library refresh today
# (see the two `sync_media_servers_for_asset` call sites: right after
# "acquisition.imported" is recorded in app/services/acquisition.py, and
# right after "replacement.replaced" is recorded in
# app/services/replacement.py). Kept as literal strings here — matching
# every other migration's self-contained style, no app imports — rather than
# importing app.services.notifications.MEDIA_SERVER_REFRESH_EVENTS, which
# documents the same two strings for the running application.
_REFRESH_EVENTS = ("acquisition.imported", "replacement.replaced")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'notification_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column(
            'provider',
            sa.Enum('JELLYFIN', 'PLEX', name='notificationprovider', native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'notification_connection_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('connection_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(['connection_id'], ['notification_connections.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('connection_id', 'event_type', name='uq_notification_connection_event'),
    )
    with op.batch_alter_table('notification_connection_events', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_notification_connection_events_connection_id'), ['connection_id'], unique=False
        )

    # --- Data migration: preserve today's Jellyfin/Plex refresh behavior
    # exactly (project owner's explicit requirement) ------------------------
    # Before this migration, importing/replacing a track's media
    # unconditionally triggered a Jellyfin/Plex library refresh whenever
    # AppSettings.jellyfin_enabled/plex_enabled was true — see the (now
    # removed) gate this replaced in
    # app/services/external_playlists.py:sync_media_servers_for_asset. Seed
    # one enabled connection per already-`*_enabled` server, subscribed to
    # exactly the two events that used to trigger that refresh, so an
    # existing install (like the live one this was built against, with both
    # Jellyfin and Plex enabled) keeps behaving identically the moment this
    # migration runs — no operator action required.
    bind = op.get_bind()
    app_settings_table = sa.table(
        'app_settings',
        sa.column('id', sa.Integer),
        sa.column('jellyfin_enabled', sa.Boolean),
        sa.column('plex_enabled', sa.Boolean),
    )
    row = bind.execute(
        sa.select(app_settings_table.c.jellyfin_enabled, app_settings_table.c.plex_enabled)
    ).first()

    if row is not None:
        connections_table = sa.table(
            'notification_connections',
            sa.column('id', sa.Integer),
            sa.column('name', sa.String),
            sa.column('provider', sa.String),
            sa.column('enabled', sa.Boolean),
            sa.column('config', sa.JSON),
            sa.column('created_at', sa.DateTime),
            sa.column('updated_at', sa.DateTime),
        )
        events_table = sa.table(
            'notification_connection_events',
            sa.column('id', sa.Integer),
            sa.column('connection_id', sa.Integer),
            sa.column('event_type', sa.String),
        )
        now = datetime.datetime.now(datetime.UTC)

        def _seed_default_connection(name: str, provider: str, was_enabled: object) -> None:
            if not was_enabled:
                return
            result = bind.execute(
                connections_table.insert().values(
                    name=name, provider=provider, enabled=True, config={}, created_at=now, updated_at=now
                )
            )
            # `sa.table(...)` (lightweight, no real PrimaryKeyConstraint) means
            # `result.inserted_primary_key` can't be resolved — use the raw
            # DBAPI `lastrowid` instead, which is exactly SQLite's rowid for
            # this INTEGER PRIMARY KEY column.
            connection_id = result.lastrowid
            bind.execute(
                events_table.insert(),
                [{'connection_id': connection_id, 'event_type': event_type} for event_type in _REFRESH_EVENTS],
            )

        _seed_default_connection('Jellyfin', 'jellyfin', row.jellyfin_enabled)
        _seed_default_connection('Plex', 'plex', row.plex_enabled)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notification_connection_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_connection_events_connection_id'))
    op.drop_table('notification_connection_events')
    op.drop_table('notification_connections')
