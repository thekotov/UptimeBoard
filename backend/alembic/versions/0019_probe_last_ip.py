"""add last_ip (resolved-IP tracking) to probes

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("probes", sa.Column("last_ip", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("probes", "last_ip")
