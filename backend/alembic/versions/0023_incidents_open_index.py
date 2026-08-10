"""partial index for open incidents (probe_id where resolved_at is null)

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The probe runner checks "does probe X have an open incident?" on every
    # single check of every probe (probe_id == X AND resolved_at IS NULL), and
    # the admin stats endpoint counts all open incidents globally. Open
    # incidents are a small fraction of the table, so a partial index keyed on
    # probe_id — restricted to that subset — serves both queries without
    # scanning resolved rows.
    op.create_index(
        "ix_incidents_open_probe_id",
        "incidents",
        ["probe_id"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_open_probe_id", table_name="incidents")
