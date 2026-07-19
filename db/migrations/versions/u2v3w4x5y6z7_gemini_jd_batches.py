"""new table gemini_jd_batches + jobs.jd_extraction_queued_at/jd_extraction_batch_id (Gemini Batch API JD-extraction queue)"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "u2v3w4x5y6z7"
down_revision: Union[str, Sequence[str], None] = "t1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gemini_jd_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="submitted"),
        sa.Column("job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_unique_constraint("uq_gemini_jd_batches_batch_name", "gemini_jd_batches", ["batch_name"])

    op.add_column("jobs", sa.Column("jd_extraction_queued_at", sa.DateTime(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("jd_extraction_batch_id", sa.Integer(), sa.ForeignKey("gemini_jd_batches.id"), nullable=True),
    )
    op.create_index("ix_jobs_jd_extraction_batch_id", "jobs", ["jd_extraction_batch_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_jd_extraction_batch_id", table_name="jobs")
    op.drop_column("jobs", "jd_extraction_batch_id")
    op.drop_column("jobs", "jd_extraction_queued_at")
    op.drop_constraint("uq_gemini_jd_batches_batch_name", "gemini_jd_batches", type_="unique")
    op.drop_table("gemini_jd_batches")
