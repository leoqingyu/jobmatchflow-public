"""add_cv_markdown_back

Revision ID: a3b4c5d6e7f9
Revises: f2b3c4d5e6f0
Create Date: 2026-03-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f9"
down_revision: Union[str, Sequence[str], None] = "f2b3c4d5e6f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_master_cvs", sa.Column("cv_markdown", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_master_cvs", "cv_markdown")
