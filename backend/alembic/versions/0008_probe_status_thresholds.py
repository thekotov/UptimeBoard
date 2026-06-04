"""add probe degraded/down status thresholds

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "probes",
        sa.Column("degraded_threshold", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "probes",
        sa.Column("down_threshold", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("probes", "down_threshold")
    op.drop_column("probes", "degraded_threshold")
