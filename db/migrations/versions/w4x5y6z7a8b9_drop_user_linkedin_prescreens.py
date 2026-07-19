"""drop unused user_linkedin_prescreens table

Revision ID: w4x5y6z7a8b9
Revises: v3w4x5y6z7a8
Create Date: 2026-07-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "w4x5y6z7a8b9"
down_revision: Union[str, Sequence[str], None] = "v3w4x5y6z7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("user_linkedin_prescreens")


def downgrade() -> None:
    op.create_table(
        "user_linkedin_prescreens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=True),
        sa.Column("jd_backfill_status", sa.String(length=50), nullable=False),
        sa.Column("llm_model", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_user_linkedin_prescreens_job_id_jobs")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_linkedin_prescreens_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_linkedin_prescreens")),
        sa.UniqueConstraint("user_id", "job_id", name="uq_user_linkedin_prescreen_user_job"),
    )
