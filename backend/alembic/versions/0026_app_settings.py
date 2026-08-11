"""add app_settings singleton table (admin-editable overrides for env tunables)

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alert_storm_window_sec", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
