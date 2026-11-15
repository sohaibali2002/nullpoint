"""Bridge a pre-Alembic development database to the migration-owned schema.

Fresh databases already receive these objects in the initial revision, so this migration is a
no-op there.  A database created before Alembic can be adopted with:

    alembic stamp 9697d791d47d
    alembic upgrade head

The conditional checks make that path safe; the downgrade intentionally does not drop objects that
the initial revision owns.

Revision ID: b1a2c3d4e5f6
Revises: 9697d791d47d
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b1a2c3d4e5f6"
down_revision: str | None = "9697d791d47d"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "sprint" in tables:
        columns = {column["name"] for column in inspector.get_columns("sprint")}
        if "last_heartbeat_at" not in columns:
            op.add_column(
                "sprint",
                sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            )

    if "job_attempt" not in tables:
        op.create_table(
            "job_attempt",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("org_id", sa.String(length=32), nullable=False),
            sa.Column("sprint_id", sa.String(length=32), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("transient", sa.Boolean(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["sprint_id"], ["sprint.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("sprint_id", "attempt", name="uq_job_attempt_sprint_attempt"),
        )
        op.create_index("ix_job_attempt_org_id", "job_attempt", ["org_id"], unique=False)
        op.create_index("ix_job_attempt_sprint_id", "job_attempt", ["sprint_id"], unique=False)

    if "holdout_evaluation" not in tables:
        op.create_table(
            "holdout_evaluation",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("org_id", sa.String(length=32), nullable=False),
            sa.Column("strategy_id", sa.String(length=32), nullable=False),
            sa.Column("strategy_version", sa.Integer(), nullable=False),
            sa.Column("opened_by", sa.String(length=120), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("report_hash", sa.String(length=64), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["strategy_id"], ["strategy.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("strategy_id", "strategy_version", name="uq_holdout_strategy_version"),
        )
        op.create_index(
            "ix_holdout_evaluation_org_id", "holdout_evaluation", ["org_id"], unique=False
        )


def downgrade() -> None:
    # Objects may belong to the initial revision; never destroy them from a bridge migration.
    pass
