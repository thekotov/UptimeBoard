"""add alert channel last-delivery status

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("alert_channels", sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alert_channels", sa.Column("last_ok", sa.Boolean(), nullable=True))
    op.add_column("alert_channels", sa.Column("last_error", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("alert_channels", "last_error")
    op.drop_column("alert_channels", "last_ok")
    op.drop_column("alert_channels", "last_sent_at")
