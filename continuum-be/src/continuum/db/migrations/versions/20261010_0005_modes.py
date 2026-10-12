"""modes: personal and workspace

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing accounts and keys are Workspace: their memories stay where they are.
    op.add_column(
        "users",
        sa.Column("default_mode", sa.String(length=16), server_default="workspace", nullable=False),
    )
    op.add_column(
        "api_keys",
        sa.Column("mode", sa.String(length=16), server_default="workspace", nullable=False),
    )
    op.add_column(
        "extraction_feedback", sa.Column("graph_owner", sa.String(length=80), nullable=True)
    )
    op.add_column("answer_feedback", sa.Column("graph_owner", sa.String(length=80), nullable=True))
    # A 64-character account id plus ":personal" no longer fits in 64.
    with op.batch_alter_table("resolution_labels") as batch:
        batch.alter_column(
            "graph_owner", type_=sa.String(length=80), existing_type=sa.String(length=64)
        )


def downgrade() -> None:
    with op.batch_alter_table("resolution_labels") as batch:
        batch.alter_column(
            "graph_owner", type_=sa.String(length=64), existing_type=sa.String(length=80)
        )
    op.drop_column("answer_feedback", "graph_owner")
    op.drop_column("extraction_feedback", "graph_owner")
    op.drop_column("api_keys", "mode")
    op.drop_column("users", "default_mode")
