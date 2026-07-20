"""add tls_fingerprint (SSL-change tracking) to probes

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("probes", sa.Column("tls_fingerprint", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("probes", "tls_fingerprint")
