"""container policy setting

Revision ID: 9503776b8b06
Revises: 8bb8b7ff0aa5
Create Date: 2026-09-14 18:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9503776b8b06'
down_revision: Union[str, Sequence[str], None] = '8bb8b7ff0aa5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing rows must not break: server_default preserves the project
    # owner's original "always MP4, even if that means transcoding"
    # decision as the default for every already-deployed install, same
    # pattern used by automatic_match_threshold's migration.
    with op.batch_alter_table('app_settings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'container_policy',
                sa.String(length=32),
                nullable=False,
                server_default='always_mp4',
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('app_settings', schema=None) as batch_op:
        batch_op.drop_column('container_policy')
