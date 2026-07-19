"""add full JD fingerprint for cross-id deduplication"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "l3m4n5o6p7q8"
down_revision: Union[str, Sequence[str], None] = "k2l3m4n5o6p7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    # The column/index may already exist in databases where the afternoon
    # change was applied before the migration revision collision was fixed.
    columns = {c["name"] for c in inspector.get_columns("jobs")}
    if "jd_fingerprint" not in columns:
        op.add_column("jobs", sa.Column("jd_fingerprint", sa.String(length=64), nullable=True))

    indexes = {i["name"] for i in inspector.get_indexes("jobs")}
    if "ix_jobs_jd_fingerprint" not in indexes:
        op.create_index("ix_jobs_jd_fingerprint", "jobs", ["jd_fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_jobs_jd_fingerprint", table_name="jobs")
    op.drop_column("jobs", "jd_fingerprint")
