"""immutable application snapshots and richer tracking stages"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "j1k2l3m4n5o6"
down_revision: Union[str, None] = "g0h1i2j3k4l5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.drop_constraint("uq_generated_assets_user_job_asset", "generated_assets", type_="unique")
    for name, typ in (
        ("applied_resume_snapshot", sa.JSON()),
        ("applied_cover_letter_snapshot", sa.JSON()),
        ("applied_resume_file_path", sa.String(512)),
        ("applied_cover_letter_file_path", sa.String(512)),
        ("jd_snapshot_text", sa.Text()),
        ("score_snapshot", sa.JSON()),
        ("status_history", sa.JSON()),
    ):
        op.add_column("application_tracking", sa.Column(name, typ, nullable=True))

def downgrade() -> None:
    for name in ("status_history", "score_snapshot", "jd_snapshot_text", "applied_cover_letter_file_path", "applied_resume_file_path", "applied_cover_letter_snapshot", "applied_resume_snapshot"):
        op.drop_column("application_tracking", name)
    op.create_unique_constraint("uq_generated_assets_user_job_asset", "generated_assets", ["user_id", "job_id", "asset_type"])
