"""phase 3 regime sector horizon scoring

Revision ID: b7e21d4c8a90
Revises: 168d9152ae2e
Create Date: 2026-09-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e21d4c8a90'
down_revision: Union[str, Sequence[str], None] = '168d9152ae2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('strategy_parameters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('parameter_set', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('parameter_set', 'name', name='uq_strategy_parameter_name')
    )
    op.create_table('market_regimes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('index_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('member_count', sa.Integer(), nullable=False),
    sa.Column('members_above_sma20', sa.Integer(), nullable=False),
    sa.Column('members_above_sma50', sa.Integer(), nullable=False),
    sa.Column('members_above_sma200', sa.Integer(), nullable=False),
    sa.Column('breadth_above_sma20_pct', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('breadth_above_sma50_pct', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('breadth_above_sma200_pct', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('index_return_1m', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('index_return_3m', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('index_return_6m', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('index_hist_vol_20', sa.Numeric(precision=12, scale=6), nullable=True),
    sa.Column('index_hist_vol_60', sa.Numeric(precision=12, scale=6), nullable=True),
    sa.Column('index_drawdown_pct', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('sector_participation_pct', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('regime_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('regime_label', sa.Enum('RISK_ON', 'CAUTIOUS', 'RISK_OFF', 'STRESSED', name='marketregimelabel', native_enum=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['index_id'], ['indices.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('index_id', 'as_of', 'calc_version', name='uq_market_regime_snapshot')
    )
    op.create_index('ix_market_regime_index_asof', 'market_regimes', ['index_id', 'as_of'], unique=False)
    op.create_table('sector_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('index_id', sa.Integer(), nullable=False),
    sa.Column('sector_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('member_count', sa.Integer(), nullable=False),
    sa.Column('breadth_above_sma50_pct', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('breadth_above_sma200_pct', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('avg_return_1m', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('avg_return_3m', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('avg_return_6m', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('avg_rs_3m', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('avg_rsi14', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('relative_to_index_pct', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('sector_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('sector_state', sa.Enum('LEADING', 'NEUTRAL', 'LAGGING', 'DEFENSIVE', name='sectorstate', native_enum=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['index_id'], ['indices.id'], ),
    sa.ForeignKeyConstraint(['sector_id'], ['sectors.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('index_id', 'sector_id', 'as_of', 'calc_version', name='uq_sector_metric_snapshot')
    )
    op.create_index('ix_sector_metric_index_asof', 'sector_metrics', ['index_id', 'as_of'], unique=False)
    op.create_table('horizon_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('short_term_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('medium_term_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('long_term_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('preferred_horizon', sa.Enum('SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM', name='horizontype', native_enum=False), nullable=False),
    sa.Column('horizon_confidence', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instrument_id', 'as_of', 'calc_version', name='uq_horizon_snapshot')
    )
    op.create_table('scoring_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('parameter_set', sa.String(length=32), nullable=False),
    sa.Column('preferred_horizon', sa.Enum('SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM', name='horizontype', native_enum=False), nullable=False),
    sa.Column('trend_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('momentum_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('relative_strength_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('quality_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('growth_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('valuation_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('risk_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('liquidity_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('horizon_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('sector_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('composite_score', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('score_label', sa.Enum('STRONG', 'GOOD', 'NEUTRAL', 'WEAK', 'POOR', name='scorelabel', native_enum=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instrument_id', 'as_of', 'calc_version', name='uq_scoring_snapshot')
    )
    op.create_index('ix_scoring_instrument_asof', 'scoring_snapshots', ['instrument_id', 'as_of'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_scoring_instrument_asof', table_name='scoring_snapshots')
    op.drop_table('scoring_snapshots')
    op.drop_table('horizon_metrics')
    op.drop_index('ix_sector_metric_index_asof', table_name='sector_metrics')
    op.drop_table('sector_metrics')
    op.drop_index('ix_market_regime_index_asof', table_name='market_regimes')
    op.drop_table('market_regimes')
    op.drop_table('strategy_parameters')
