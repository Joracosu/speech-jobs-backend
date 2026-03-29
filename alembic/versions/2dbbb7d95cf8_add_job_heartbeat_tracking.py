"""add job heartbeat tracking

Revision ID: 2dbbb7d95cf8
Revises: be857cdb2cd2
Create Date: 2026-03-29 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2dbbb7d95cf8"
down_revision: Union[str, Sequence[str], None] = "be857cdb2cd2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "jobs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("jobs", "last_heartbeat_at")
