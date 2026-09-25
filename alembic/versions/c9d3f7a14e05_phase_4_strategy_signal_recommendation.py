"""phase 4 strategy signal recommendation

Revision ID: c9d3f7a14e05
Revises: b7e21d4c8a90
Create Date: 2026-09-25 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d3f7a14e05'
down_revision: Union[str, Sequence[str], None] = 'b7e21d4c8a90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('strategies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=48), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('horizon', sa.Enum('SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM', name='horizontype', native_enum=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code', name='uq_strategy_code')
    )
    op.create_table('strategy_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('rules', sa.JSON(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('strategy_id', 'version', name='uq_strategy_version')
    )
    op.create_index('ix_strategy_version_current', 'strategy_versions', ['strategy_id', 'is_current'], unique=False)
    op.create_table('signals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('strategy_version_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('signal_type', sa.Enum('BUY_SETUP', 'WATCH', 'HOLD', 'REDUCE', 'EXIT_WARNING', 'EXIT', name='signaltype', native_enum=False), nullable=False),
    sa.Column('state', sa.Enum('WATCH', 'POTENTIAL_ENTRY', 'ENTRY', 'HOLD', 'THESIS_WEAKENING', 'EXIT_REVIEW', 'EXIT', 'CLOSED', name='recommendationstate', native_enum=False), nullable=False),
    sa.Column('horizon', sa.Enum('SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM', name='horizontype', native_enum=False), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('composite_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('risk_level', sa.Enum('LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', name='risklevel', native_enum=False), nullable=False),
    sa.Column('entry_low', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('entry_high', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_low', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_high', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('invalidation_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('expected_holding_days_min', sa.Integer(), nullable=False),
    sa.Column('expected_holding_days_max', sa.Integer(), nullable=False),
    sa.Column('rules_result', sa.JSON(), nullable=False),
    sa.Column('reasons', sa.JSON(), nullable=False),
    sa.Column('thesis', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instrument_id', 'strategy_version_id', 'as_of', 'calc_version', name='uq_signal_snapshot')
    )
    op.create_index('ix_signal_instrument_asof', 'signals', ['instrument_id', 'as_of'], unique=False)
    op.create_table('recommendations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('strategy_version_id', sa.Integer(), nullable=False),
    sa.Column('strategy_code', sa.String(length=48), nullable=False),
    sa.Column('state', sa.Enum('WATCH', 'POTENTIAL_ENTRY', 'ENTRY', 'HOLD', 'THESIS_WEAKENING', 'EXIT_REVIEW', 'EXIT', 'CLOSED', name='recommendationstate', native_enum=False), nullable=False),
    sa.Column('horizon', sa.Enum('SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM', name='horizontype', native_enum=False), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('current_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('entry_low', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('entry_high', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_low', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_high', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('invalidation_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('risk_level', sa.Enum('LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', name='risklevel', native_enum=False), nullable=False),
    sa.Column('expected_holding_days_min', sa.Integer(), nullable=False),
    sa.Column('expected_holding_days_max', sa.Integer(), nullable=False),
    sa.Column('thesis', sa.Text(), nullable=False),
    sa.Column('created_reason', sa.Text(), nullable=False),
    sa.Column('latest_version', sa.Integer(), nullable=False),
    sa.Column('first_as_of', sa.Date(), nullable=False),
    sa.Column('last_as_of', sa.Date(), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('exit_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_recommendation_instrument_state', 'recommendations', ['instrument_id', 'state'], unique=False)
    op.create_table('recommendation_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recommendation_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('state', sa.Enum('WATCH', 'POTENTIAL_ENTRY', 'ENTRY', 'HOLD', 'THESIS_WEAKENING', 'EXIT_REVIEW', 'EXIT', 'CLOSED', name='recommendationstate', native_enum=False), nullable=False),
    sa.Column('signal_type', sa.Enum('BUY_SETUP', 'WATCH', 'HOLD', 'REDUCE', 'EXIT_WARNING', 'EXIT', name='signaltype', native_enum=False), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('composite_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('risk_level', sa.Enum('LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', name='risklevel', native_enum=False), nullable=False),
    sa.Column('entry_low', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('entry_high', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_low', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_high', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('invalidation_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('rules_result', sa.JSON(), nullable=False),
    sa.Column('reasons', sa.JSON(), nullable=False),
    sa.Column('change_summary', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('recommendation_id', 'version', name='uq_recommendation_version')
    )
    op.create_index('ix_recommendation_version_rec', 'recommendation_versions', ['recommendation_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_recommendation_version_rec', table_name='recommendation_versions')
    op.drop_table('recommendation_versions')
    op.drop_index('ix_recommendation_instrument_state', table_name='recommendations')
    op.drop_table('recommendations')
    op.drop_index('ix_signal_instrument_asof', table_name='signals')
    op.drop_table('signals')
    op.drop_index('ix_strategy_version_current', table_name='strategy_versions')
    op.drop_table('strategy_versions')
    op.drop_table('strategies')
