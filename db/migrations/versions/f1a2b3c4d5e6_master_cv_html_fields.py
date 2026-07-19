"""master_cv_html_fields

Revision ID: f1a2b3c4d5e6
Revises: e7f1a2b3c4d5
Create Date: 2026-03-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e7f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_master_cvs", sa.Column("cv_master_html", sa.Text(), nullable=True))
    op.add_column("user_master_cvs", sa.Column("cv_html_template", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_master_cvs", "cv_html_template")
    op.drop_column("user_master_cvs", "cv_master_html")
