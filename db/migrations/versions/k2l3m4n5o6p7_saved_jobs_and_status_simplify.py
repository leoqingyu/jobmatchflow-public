"""saved jobs (bookmark) table + collapse legacy tracking statuses into interview

Revision ID: k2l3m4n5o6p7
Revises: c1d2e3f4a5b6, j1k2l3m4n5o6
Create Date: 2026-07-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k2l3m4n5o6p7"
down_revision: Union[str, Sequence[str], None] = ("c1d2e3f4a5b6", "j1k2l3m4n5o6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_saved_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "job_id", name="uq_user_saved_jobs_user_job"),
    )

    # ApplicationStatus 从 10 档收窄为 applied/interview/offer/rejected（core/constants.py）；
    # 把旧的细分阶段并到 interview，"new/review/generated/archived" 现在不再产生
    # ApplicationTracking 记录（只在 mark-applied 时才建），当时也没有任何行落在这些值上。
    op.execute(
        "UPDATE application_tracking SET application_status = 'interview' "
        "WHERE application_status IN ('screening', 'interview_1', 'interview_2')"
    )


def downgrade() -> None:
    op.drop_table("user_saved_jobs")
