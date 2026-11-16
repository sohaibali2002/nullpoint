"""research rooms and sourced agent turns

Revision ID: d5e6f7a8b9c0
Revises: c4e5f6a7b8c9
Create Date: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4e5f6a7b8c9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "chat_thread",
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="solo"),
    )
    op.add_column(
        "chat_thread",
        sa.Column("participants", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "chat_message",
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="message"),
    )
    op.add_column(
        "chat_message",
        sa.Column("sources", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("chat_message", "sources")
    op.drop_column("chat_message", "kind")
    op.drop_column("chat_thread", "participants")
    op.drop_column("chat_thread", "mode")
