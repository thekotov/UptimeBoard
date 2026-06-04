"""add probe recovery_threshold + consecutive_successes

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "probes",
        sa.Column("recovery_threshold", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "probes",
        sa.Column("consecutive_successes", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("probes", "consecutive_successes")
    op.drop_column("probes", "recovery_threshold")
