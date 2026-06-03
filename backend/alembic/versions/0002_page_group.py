"""add page group_name

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pages", sa.Column("group_name", sa.String(length=128), nullable=True))
    op.create_index("ix_pages_group_name", "pages", ["group_name"])


def downgrade() -> None:
    op.drop_index("ix_pages_group_name", table_name="pages")
    op.drop_column("pages", "group_name")
