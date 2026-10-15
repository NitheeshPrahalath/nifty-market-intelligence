"""widen recommendation_events.title to text

The lifecycle engine stores the full state-transition summary (state change,
thesis deltas and every fired exit reason) in ``title``; with thirteen tracked
factors that routinely exceeds the original ``varchar(128)``. SQLite ignored the
limit, so this only surfaced on Postgres.

Revision ID: c2d3e4f5a6b7
Revises: b1d2e3f4a5c6
Create Date: 2026-10-15 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b1d2e3f4a5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('recommendation_events') as batch:
        batch.alter_column(
            'title',
            existing_type=sa.String(length=128),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('recommendation_events') as batch:
        batch.alter_column(
            'title',
            existing_type=sa.Text(),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
