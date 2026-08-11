"""composite (probe_id, checked_at) index on probe_results, drop redundant probe_id-only index

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-10

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # History/timeline queries filter probe_id (equality or IN) and range/order
    # on checked_at — a composite index serves both, and (as the leading
    # column) also covers plain probe_id-only lookups, making the old
    # standalone probe_id index redundant write overhead.
    op.create_index(
        "ix_probe_results_probe_checked_at", "probe_results", ["probe_id", "checked_at"]
    )
    op.drop_index("ix_probe_results_probe_id", table_name="probe_results")


def downgrade() -> None:
    op.create_index("ix_probe_results_probe_id", "probe_results", ["probe_id"])
    op.drop_index("ix_probe_results_probe_checked_at", table_name="probe_results")
