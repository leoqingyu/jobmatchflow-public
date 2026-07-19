"""user_linkedin_prescreen

Revision ID: e7f1a2b3c4d5
Revises: cac936bdd0b1
Create Date: 2026-03-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "cac936bdd0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_linkedin_prescreens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=True),
        sa.Column("jd_backfill_status", sa.String(length=50), nullable=False),
        sa.Column("llm_model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_user_linkedin_prescreens_job_id_jobs")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_linkedin_prescreens_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_linkedin_prescreens")),
        sa.UniqueConstraint("user_id", "job_id", "prompt_version", name="uq_user_linkedin_prescreen_version"),
    )


def downgrade() -> None:
    op.drop_table("user_linkedin_prescreens")
