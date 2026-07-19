"""user_profiles: scoring_preferences_text for JD scoring bonus

Revision ID: h1i2j3k4l5m6
Revises: g0h1i2j3k4l5
Create Date: 2026-03-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h1i2j3k4l5m6"
down_revision: Union[str, None] = "g0h1i2j3k4l5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("scoring_preferences_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "scoring_preferences_text")
