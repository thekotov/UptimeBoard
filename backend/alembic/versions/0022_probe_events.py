"""add probe_events table (admin activity feed)

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "probe_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "probe_id", sa.Integer(),
            sa.ForeignKey("probes.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_probe_events_probe_id", "probe_events", ["probe_id"])
    op.create_index("ix_probe_events_type", "probe_events", ["type"])
    op.create_index("ix_probe_events_created_at", "probe_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_probe_events_created_at", table_name="probe_events")
    op.drop_index("ix_probe_events_type", table_name="probe_events")
    op.drop_index("ix_probe_events_probe_id", table_name="probe_events")
    op.drop_table("probe_events")
