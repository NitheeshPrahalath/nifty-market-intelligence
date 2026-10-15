"""phase 10 observability, RBAC audit fields and data-quality reconciliation

Revision ID: b1d2e3f4a5c6
Revises: f9a2c4e6b8d0
Create Date: 2026-10-15 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1d2e3f4a5c6'
down_revision: Union[str, Sequence[str], None] = 'f9a2c4e6b8d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # RBAC + correlation fields on the API audit log. Batched so the change is
    # applied on SQLite (which cannot ALTER ... DROP COLUMN) as well as Postgres.
    with op.batch_alter_table('api_requests') as batch:
        batch.add_column(sa.Column('role', sa.String(length=16), nullable=True))
        batch.add_column(sa.Column('request_id', sa.String(length=16), nullable=True))

    # Data-quality reconciliation audit.
    op.create_table(
        'data_quality_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_name', sa.String(length=128), nullable=False),
        sa.Column(
            'status',
            sa.Enum('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', name='runstatus', native_enum=False),
            nullable=False,
        ),
        sa.Column('as_of', sa.Date(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('checks_run', sa.Integer(), nullable=False),
        sa.Column('checks_failed', sa.Integer(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_data_quality_run_as_of', 'data_quality_runs', ['as_of'], unique=False
    )
    op.create_index(
        'ix_data_quality_run_job', 'data_quality_runs', ['job_name'], unique=False
    )

    op.create_table(
        'data_quality_issues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('check_code', sa.String(length=64), nullable=False),
        sa.Column(
            'severity',
            sa.Enum('INFO', 'WARNING', 'ERROR', name='severity', native_enum=False),
            nullable=False,
        ),
        sa.Column('scope', sa.String(length=64), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['data_quality_runs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_data_quality_issue_run', 'data_quality_issues', ['run_id'], unique=False
    )
    op.create_index(
        'ix_data_quality_issue_code', 'data_quality_issues', ['check_code'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_data_quality_issue_code', table_name='data_quality_issues')
    op.drop_index('ix_data_quality_issue_run', table_name='data_quality_issues')
    op.drop_table('data_quality_issues')
    op.drop_index('ix_data_quality_run_job', table_name='data_quality_runs')
    op.drop_index('ix_data_quality_run_as_of', table_name='data_quality_runs')
    op.drop_table('data_quality_runs')
    with op.batch_alter_table('api_requests') as batch:
        batch.drop_column('request_id')
        batch.drop_column('role')
