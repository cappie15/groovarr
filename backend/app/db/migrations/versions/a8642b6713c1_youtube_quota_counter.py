"""youtube_quota_counter

Revision ID: a8642b6713c1
Revises: e83ea4a6c1ab
Create Date: 2026-09-14 20:44:58.957942

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8642b6713c1'
down_revision: Union[str, Sequence[str], None] = 'e83ea4a6c1ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default so an existing settings row is backfilled cleanly
    # (SQLite's batch-mode table rebuild needs a value for the new NOT NULL
    # column on any pre-existing row) — mirrors the pattern used by the
    # hardware_acceleration_setting migration.
    with op.batch_alter_table('app_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('youtube_quota_date', sa.String(length=10), nullable=True))
        batch_op.add_column(
            sa.Column('youtube_quota_search_calls', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('app_settings', schema=None) as batch_op:
        batch_op.drop_column('youtube_quota_search_calls')
        batch_op.drop_column('youtube_quota_date')
