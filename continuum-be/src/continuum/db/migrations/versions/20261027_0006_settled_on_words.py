"""settled on the person's words: what they said, and through what

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Null on every decision made before: all of them were a person's button.
    op.add_column("resolution_labels", sa.Column("said", sa.Text(), nullable=True))
    op.add_column(
        "resolution_labels", sa.Column("settled_via", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("resolution_labels") as batch:
        batch.drop_column("settled_via")
        batch.drop_column("said")
