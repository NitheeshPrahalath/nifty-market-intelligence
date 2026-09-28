"""phase 5 tracking exits notifications

Revision ID: d5e1a04b7c39
Revises: c9d3f7a14e05
Create Date: 2026-09-28 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e1a04b7c39'
down_revision: Union[str, Sequence[str], None] = 'c9d3f7a14e05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('recommendations') as batch:
        batch.add_column(sa.Column('active_since', sa.Date(), nullable=True))
        batch.add_column(
            sa.Column('last_reviewed_at', sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table('thesis_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recommendation_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('state', sa.Enum('WATCH', 'POTENTIAL_ENTRY', 'ENTRY', 'HOLD', 'THESIS_WEAKENING', 'EXIT_REVIEW', 'EXIT', 'CLOSED', name='recommendationstate', native_enum=False), nullable=False),
    sa.Column('factors', sa.JSON(), nullable=False),
    sa.Column('composite_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('weakened_count', sa.Integer(), nullable=False),
    sa.Column('improved_count', sa.Integer(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('recommendation_id', 'as_of', name='uq_thesis_snapshot_day')
    )
    op.create_index('ix_thesis_snapshot_rec_asof', 'thesis_snapshots', ['recommendation_id', 'as_of'], unique=False)

    op.create_table('recommendation_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recommendation_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('event_type', sa.Enum('RECOMMENDATION_CREATED', 'ENTRY_ZONE_REACHED', 'RECOMMENDATION_UPGRADED', 'RECOMMENDATION_DOWNGRADED', 'THESIS_IMPROVED', 'THESIS_WEAKENED', 'TARGET_REACHED', 'RISK_TRIGGERED', 'EXIT_REVIEW_SIGNAL', 'EXIT_SIGNAL', 'RECOMMENDATION_CLOSED', name='eventtype', native_enum=False), nullable=False),
    sa.Column('mechanism', sa.Enum('TECHNICAL', 'FUNDAMENTAL', 'VALUATION', 'RISK', 'TARGET', 'TIME', 'EVENT', 'STRATEGY', name='exitmechanism', native_enum=False), nullable=True),
    sa.Column('previous_state', sa.Enum('WATCH', 'POTENTIAL_ENTRY', 'ENTRY', 'HOLD', 'THESIS_WEAKENING', 'EXIT_REVIEW', 'EXIT', 'CLOSED', name='recommendationstate', native_enum=False), nullable=True),
    sa.Column('new_state', sa.Enum('WATCH', 'POTENTIAL_ENTRY', 'ENTRY', 'HOLD', 'THESIS_WEAKENING', 'EXIT_REVIEW', 'EXIT', 'CLOSED', name='recommendationstate', native_enum=False), nullable=True),
    sa.Column('title', sa.String(length=128), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('recommendation_version', sa.Integer(), nullable=True),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('dedupe_key', sa.String(length=160), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key', name='uq_recommendation_event_key')
    )
    op.create_index('ix_recommendation_event_rec_asof', 'recommendation_events', ['recommendation_id', 'as_of'], unique=False)

    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('notification_type', sa.Enum('NEW_RECOMMENDATION', 'ENTRY_ZONE_REACHED', 'RECOMMENDATION_UPGRADED', 'RECOMMENDATION_DOWNGRADED', 'THESIS_WEAKENING', 'THESIS_IMPROVED', 'FUNDAMENTAL_CHANGE', 'TECHNICAL_CHANGE', 'TARGET_REVIEW_ZONE_REACHED', 'RISK_CONDITION_TRIGGERED', 'EXIT_CONDITION_TRIGGERED', 'RECOMMENDATION_CLOSED', 'MARKET_REGIME_CHANGE', name='notificationtype', native_enum=False), nullable=False),
    sa.Column('severity', sa.Enum('INFO', 'WARNING', 'ERROR', name='severity', native_enum=False), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('recommendation_id', sa.Integer(), nullable=True),
    sa.Column('recommendation_version', sa.Integer(), nullable=True),
    sa.Column('instrument_id', sa.Integer(), nullable=True),
    sa.Column('index_id', sa.Integer(), nullable=True),
    sa.Column('strategy_code', sa.String(length=48), nullable=True),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('dedupe_key', sa.String(length=200), nullable=False),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.ForeignKeyConstraint(['index_id'], ['indices.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key', name='uq_notification_dedupe_key')
    )
    op.create_index('ix_notification_asof_type', 'notifications', ['as_of', 'notification_type'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_notification_asof_type', table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('ix_recommendation_event_rec_asof', table_name='recommendation_events')
    op.drop_table('recommendation_events')
    op.drop_index('ix_thesis_snapshot_rec_asof', table_name='thesis_snapshots')
    op.drop_table('thesis_snapshots')
    with op.batch_alter_table('recommendations') as batch:
        batch.drop_column('last_reviewed_at')
        batch.drop_column('active_since')
