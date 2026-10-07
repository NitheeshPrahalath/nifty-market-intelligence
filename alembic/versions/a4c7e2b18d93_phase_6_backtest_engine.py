"""phase 6 backtest engine

Revision ID: a4c7e2b18d93
Revises: d5e1a04b7c39
Create Date: 2026-09-28 11:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4c7e2b18d93'
down_revision: Union[str, Sequence[str], None] = 'd5e1a04b7c39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('backtest_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.Enum('SINGLE', 'WALK_FORWARD', name='backtestkind', native_enum=False), nullable=False),
    sa.Column('parent_run_id', sa.Integer(), nullable=True),
    sa.Column('window_index', sa.Integer(), nullable=True),
    sa.Column('strategy_codes', sa.JSON(), nullable=False),
    sa.Column('index_codes', sa.JSON(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('initial_capital', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('trades_count', sa.Integer(), nullable=False),
    sa.Column('calc_version', sa.String(length=32), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', name='runstatus', native_enum=False), nullable=False),
    sa.Column('error_summary', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['parent_run_id'], ['backtest_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_backtest_run_parent', 'backtest_runs', ['parent_run_id'], unique=False)
    op.create_index('ix_backtest_run_window', 'backtest_runs', ['window_index'], unique=False)

    op.create_table('backtest_trades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('strategy_code', sa.String(length=48), nullable=False),
    sa.Column('entry_signal_date', sa.Date(), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('entry_price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('exit_date', sa.Date(), nullable=False),
    sa.Column('exit_price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('gross_pnl', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('cost', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('net_pnl', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('return_pct', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('holding_days', sa.Integer(), nullable=False),
    sa.Column('stop_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('target_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('exit_reason', sa.Enum('STOP_LOSS', 'TARGET', 'TIME_STOP', 'STRATEGY_EXIT', 'RISK_EXIT', 'TECHNICAL_EXIT', 'FUNDAMENTAL_EXIT', 'VALUATION_EXIT', 'EVENT_EXIT', 'END_OF_TEST', 'LIQUIDATION', name='backtestexitreason', native_enum=False), nullable=False),
    sa.Column('exit_detail', sa.Text(), nullable=False),
    sa.Column('entry_reasons', sa.JSON(), nullable=False),
    sa.Column('exit_reasons', sa.JSON(), nullable=False),
    sa.Column('mae_pct', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('mfe_pct', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['backtest_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_backtest_trade_run', 'backtest_trades', ['run_id'], unique=False)
    op.create_index('ix_backtest_trade_instrument', 'backtest_trades', ['instrument_id'], unique=False)

    op.create_table('backtest_equity_points',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('equity', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('cash', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('positions', sa.Integer(), nullable=False),
    sa.Column('exposure_pct', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('drawdown_pct', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['backtest_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'as_of', name='uq_backtest_equity_point')
    )
    op.create_index('ix_backtest_equity_run', 'backtest_equity_points', ['run_id'], unique=False)

    op.create_table('backtest_rejections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('strategy_code', sa.String(length=48), nullable=False),
    sa.Column('side', sa.String(length=8), nullable=False),
    sa.Column('reason', sa.Enum('NO_BAR', 'NOT_A_MEMBER', 'NO_CASH', 'BELOW_LOT', 'LIMIT_PRICE_MISSED', 'ALREADY_HELD', 'NO_FREE_SLOT', name='backtestrejectreason', native_enum=False), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['backtest_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_backtest_rejection_run', 'backtest_rejections', ['run_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_backtest_rejection_run', table_name='backtest_rejections')
    op.drop_table('backtest_rejections')
    op.drop_index('ix_backtest_equity_run', table_name='backtest_equity_points')
    op.drop_table('backtest_equity_points')
    op.drop_index('ix_backtest_trade_instrument', table_name='backtest_trades')
    op.drop_index('ix_backtest_trade_run', table_name='backtest_trades')
    op.drop_table('backtest_trades')
    op.drop_index('ix_backtest_run_window', table_name='backtest_runs')
    op.drop_index('ix_backtest_run_parent', table_name='backtest_runs')
    op.drop_table('backtest_runs')
