"""liked songs pseudo-playlist flag

Revision ID: e83ea4a6c1ab
Revises: 63daa53d45e8
Create Date: 2026-09-14 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e83ea4a6c1ab'
down_revision: Union[str, Sequence[str], None] = '63daa53d45e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Every existing row is a real Spotify playlist, never Liked Songs —
    # server_default=false() preserves that for already-deployed installs,
    # same pattern used by monitor_better_versions_enabled's migration.
    with op.batch_alter_table('spotify_playlists', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_liked_songs', sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('spotify_playlists', schema=None) as batch_op:
        batch_op.drop_column('is_liked_songs')
