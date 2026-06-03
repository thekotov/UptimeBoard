"""denormalized probe status, alert thresholds, maintenance windows, channel routing

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # probes: thresholds + denormalised latest result
    op.add_column("probes", sa.Column("order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("probes", sa.Column("failure_threshold", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("probes", sa.Column("latency_degraded_ms", sa.Integer(), nullable=True))
    op.add_column("probes", sa.Column("last_status", sa.String(length=16), nullable=False, server_default="unknown"))
    op.add_column("probes", sa.Column("last_latency_ms", sa.Float(), nullable=True))
    op.add_column("probes", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("probes", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("probes", sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"))

    # incidents: repeat-reminder bookkeeping
    op.add_column("incidents", sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True))

    # alert channels: optional per-page routing
    op.add_column("alert_channels", sa.Column("page_id", sa.Integer(), nullable=True))
    op.create_index("ix_alert_channels_page_id", "alert_channels", ["page_id"])
    op.create_foreign_key(
        "fk_alert_channels_page", "alert_channels", "pages", ["page_id"], ["id"], ondelete="CASCADE"
    )

    # maintenance windows
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_maintenance_windows_page_id", "maintenance_windows", ["page_id"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_windows_page_id", table_name="maintenance_windows")
    op.drop_table("maintenance_windows")
    op.drop_constraint("fk_alert_channels_page", "alert_channels", type_="foreignkey")
    op.drop_index("ix_alert_channels_page_id", table_name="alert_channels")
    op.drop_column("alert_channels", "page_id")
    op.drop_column("incidents", "last_notified_at")
    for col in (
        "consecutive_failures",
        "order",
        "last_error",
        "last_checked_at",
        "last_latency_ms",
        "last_status",
        "latency_degraded_ms",
        "failure_threshold",
    ):
        op.drop_column("probes", col)
