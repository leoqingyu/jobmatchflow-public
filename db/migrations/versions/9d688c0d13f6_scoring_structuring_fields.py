"""scoring structuring fields

Revision ID: 9d688c0d13f6
Revises: i2j3k4l5m6n7
Create Date: 2026-07-14 23:33:53.650259

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d688c0d13f6'
down_revision: Union[str, Sequence[str], None] = 'i2j3k4l5m6n7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("jobs", sa.Column("structured_requirements", sa.JSON(), nullable=True))
    op.add_column("user_profiles", sa.Column("structured_profile", sa.JSON(), nullable=True))
    op.add_column(
        "user_profiles",
        sa.Column("structured_profile_source_hash", sa.String(length=64), nullable=True),
    )
    op.add_column("user_job_scores", sa.Column("score_breakdown", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user_job_scores", "score_breakdown")
    op.drop_column("user_profiles", "structured_profile_source_hash")
    op.drop_column("user_profiles", "structured_profile")
    op.drop_column("jobs", "structured_requirements")
