"""add TLS certificate metadata to probes

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("probes", sa.Column("tls_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("probes", sa.Column("tls_not_before", sa.DateTime(timezone=True), nullable=True))
    op.add_column("probes", sa.Column("tls_issuer", sa.String(length=255), nullable=True))
    op.add_column("probes", sa.Column("tls_subject", sa.String(length=255), nullable=True))
    op.add_column("probes", sa.Column("tls_sans", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("probes", "tls_sans")
    op.drop_column("probes", "tls_subject")
    op.drop_column("probes", "tls_issuer")
    op.drop_column("probes", "tls_not_before")
    op.drop_column("probes", "tls_expires_at")
