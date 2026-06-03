"""alert escalation, incident escalation marker, heartbeat last ping

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "alert_channels",
        sa.Column("escalate_after_min", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("incidents", sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("probes", sa.Column("last_ping_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("probes", "last_ping_at")
    op.drop_column("incidents", "escalated_at")
    op.drop_column("alert_channels", "escalate_after_min")
