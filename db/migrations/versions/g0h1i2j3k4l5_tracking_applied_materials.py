"""application_tracking: snapshot resume/letter at mark-applied

Revision ID: g0h1i2j3k4l5
Revises: d8e9f0a1b2c3
Create Date: 2026-03-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g0h1i2j3k4l5"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "application_tracking",
        sa.Column("applied_resume_master_cv_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "application_tracking",
        sa.Column("applied_resume_asset_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "application_tracking",
        sa.Column("applied_cover_letter_asset_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_application_tracking_applied_resume_master_cv_id_user_master_cvs"),
        "application_tracking",
        "user_master_cvs",
        ["applied_resume_master_cv_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_application_tracking_applied_resume_asset_id_generated_assets"),
        "application_tracking",
        "generated_assets",
        ["applied_resume_asset_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_application_tracking_applied_cover_letter_asset_id_generated_assets"),
        "application_tracking",
        "generated_assets",
        ["applied_cover_letter_asset_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_application_tracking_applied_cover_letter_asset_id_generated_assets"),
        "application_tracking",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_application_tracking_applied_resume_asset_id_generated_assets"),
        "application_tracking",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_application_tracking_applied_resume_master_cv_id_user_master_cvs"),
        "application_tracking",
        type_="foreignkey",
    )
    op.drop_column("application_tracking", "applied_cover_letter_asset_id")
    op.drop_column("application_tracking", "applied_resume_asset_id")
    op.drop_column("application_tracking", "applied_resume_master_cv_id")
