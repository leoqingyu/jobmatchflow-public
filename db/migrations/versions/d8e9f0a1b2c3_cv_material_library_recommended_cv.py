"""cv material library on user_profiles; recommended_cv_id on scores

Revision ID: d8e9f0a1b2c3
Revises: c5d6e7f8a9b1
Create Date: 2026-03-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c5d6e7f8a9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("cv_material_library_html", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("cv_material_library_updated_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "user_job_scores",
        sa.Column("recommended_cv_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_user_job_scores_recommended_cv_id_user_master_cvs"),
        "user_job_scores",
        "user_master_cvs",
        ["recommended_cv_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_user_job_scores_recommended_cv_id_user_master_cvs"),
        "user_job_scores",
        type_="foreignkey",
    )
    op.drop_column("user_job_scores", "recommended_cv_id")
    op.drop_column("user_profiles", "cv_material_library_updated_at")
    op.drop_column("user_profiles", "cv_material_library_html")
