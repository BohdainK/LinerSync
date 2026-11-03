"""sync memory kept with pre-write snapshots

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pre_write_snapshots", sa.Column("prior", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pre_write_snapshots") as batch:
        batch.drop_column("prior")
