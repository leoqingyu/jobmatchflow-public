"""drop_master_cv_md_template

Revision ID: f2b3c4d5e6f0
Revises: f1a2b3c4d5e6
Create Date: 2026-03-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2b3c4d5e6f0"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("user_master_cvs", "cv_markdown")
    op.drop_column("user_master_cvs", "cv_html_template")


def downgrade() -> None:
    op.add_column(
        "user_master_cvs",
        sa.Column("cv_markdown", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_master_cvs",
        sa.Column("cv_html_template", sa.Text(), nullable=True),
    )
