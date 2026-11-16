"""agent chat threads and messages

Revision ID: c4e5f6a7b8c9
Revises: b1a2c3d4e5f6
Create Date: 2026-11-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4e5f6a7b8c9"
down_revision: str | None = "b1a2c3d4e5f6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "chat_thread",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("agent", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_thread_org_id", "chat_thread", ["org_id"], unique=False)
    op.create_index("ix_chat_thread_user_id", "chat_thread", ["user_id"], unique=False)
    op.create_index("ix_chat_thread_org_updated", "chat_thread", ["org_id", "updated_at"], unique=False)

    op.create_table(
        "chat_message",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("agent", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_thread.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_message_org_id", "chat_message", ["org_id"], unique=False)
    op.create_index("ix_chat_message_thread_id", "chat_message", ["thread_id"], unique=False)
    op.create_index(
        "ix_chat_message_thread_created", "chat_message", ["thread_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_chat_message_thread_created", table_name="chat_message")
    op.drop_index("ix_chat_message_thread_id", table_name="chat_message")
    op.drop_index("ix_chat_message_org_id", table_name="chat_message")
    op.drop_table("chat_message")
    op.drop_index("ix_chat_thread_org_updated", table_name="chat_thread")
    op.drop_index("ix_chat_thread_user_id", table_name="chat_thread")
    op.drop_index("ix_chat_thread_org_id", table_name="chat_thread")
    op.drop_table("chat_thread")
