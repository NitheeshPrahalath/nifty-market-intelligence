"""phase 7 API request audit log

Revision ID: e7b3c1a9f204
Revises: a4c7e2b18d93
Create Date: 2026-10-14 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7b3c1a9f204'
down_revision: Union[str, Sequence[str], None] = 'a4c7e2b18d93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('api_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('principal', sa.String(length=64), nullable=False),
    sa.Column('method', sa.String(length=8), nullable=False),
    sa.Column('path', sa.String(length=256), nullable=False),
    sa.Column('query', sa.String(length=512), nullable=True),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('cache_hit', sa.Boolean(), nullable=False),
    sa.Column('client_ip', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_api_request_time', 'api_requests', ['requested_at'], unique=False)
    op.create_index('ix_api_request_principal', 'api_requests', ['principal'], unique=False)
    op.create_index('ix_api_request_path', 'api_requests', ['path'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_api_request_path', table_name='api_requests')
    op.drop_index('ix_api_request_principal', table_name='api_requests')
    op.drop_index('ix_api_request_time', table_name='api_requests')
    op.drop_table('api_requests')
