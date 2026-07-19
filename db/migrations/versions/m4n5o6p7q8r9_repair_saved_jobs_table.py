"""repair saved jobs table missing despite the migration being recorded"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m4n5o6p7q8r9"
down_revision: Union[str, Sequence[str], None] = "l3m4n5o6p7q8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_saved_jobs" not in inspector.get_table_names():
        op.create_table(
            "user_saved_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "job_id", name="uq_user_saved_jobs_user_job"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "user_saved_jobs" in sa.inspect(bind).get_table_names():
        op.drop_table("user_saved_jobs")
