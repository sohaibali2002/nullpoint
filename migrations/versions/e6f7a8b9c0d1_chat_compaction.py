"""chat context compaction

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "chat_thread",
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "chat_thread",
        sa.Column("summary_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("chat_thread", "summary_count")
    op.drop_column("chat_thread", "summary")
