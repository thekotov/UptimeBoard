"""add probe_rollups (pre-aggregated metrics)

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "probe_rollups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "probe_id",
            sa.Integer(),
            sa.ForeignKey("probes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("up_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("degraded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("down_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uptime_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_avg", sa.Float(), nullable=True),
        sa.Column("latency_p50", sa.Float(), nullable=True),
        sa.Column("latency_p95", sa.Float(), nullable=True),
        sa.Column("latency_p99", sa.Float(), nullable=True),
        sa.Column("latency_min", sa.Float(), nullable=True),
        sa.Column("latency_max", sa.Float(), nullable=True),
        sa.UniqueConstraint(
            "probe_id", "period", "bucket", name="uq_rollup_probe_period_bucket"
        ),
    )
    op.create_index(
        "ix_rollup_lookup", "probe_rollups", ["probe_id", "period", "bucket"]
    )


def downgrade() -> None:
    op.drop_index("ix_rollup_lookup", table_name="probe_rollups")
    op.drop_table("probe_rollups")
