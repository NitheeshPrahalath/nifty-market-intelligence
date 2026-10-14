"""phase 9 AI explanation audit table

Revision ID: f9a2c4e6b8d0
Revises: e7b3c1a9f204
Create Date: 2026-10-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9a2c4e6b8d0'
down_revision: Union[str, Sequence[str], None] = 'e7b3c1a9f204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('explanations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recommendation_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=96), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('headline', sa.Text(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('bullets', sa.JSON(), nullable=False),
    sa.Column('risks', sa.JSON(), nullable=False),
    sa.Column('citations', sa.JSON(), nullable=False),
    sa.Column('fallback_reason', sa.Text(), nullable=True),
    sa.Column('valid', sa.Boolean(), nullable=False),
    sa.Column('validation_errors', sa.JSON(), nullable=False),
    sa.Column('raw_response', sa.Text(), nullable=True),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id']),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint(
        'recommendation_id',
        'kind',
        'source_hash',
        'provider',
        'model',
        'prompt_version',
        name='uq_explanation_identity',
    )
    )
    op.create_index(
        'ix_explanation_rec_kind', 'explanations', ['recommendation_id', 'kind'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_explanation_rec_kind', table_name='explanations')
    op.drop_table('explanations')
