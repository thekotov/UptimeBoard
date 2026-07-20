"""add content-change hash and cert-expiry reminder tracking to probes

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("probes", sa.Column("last_content_hash", sa.String(length=64), nullable=True))
    op.add_column("probes", sa.Column("tls_reminder_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("probes", "tls_reminder_days")
    op.drop_column("probes", "last_content_hash")
