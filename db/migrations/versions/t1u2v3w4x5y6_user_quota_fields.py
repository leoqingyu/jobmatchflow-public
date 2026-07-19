"""user_profiles: admin-set quota fields (max_matched_jobs, max_generated_resumes/cover_letters, allowed_countries)"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "t1u2v3w4x5y6"
down_revision: Union[str, Sequence[str], None] = "s0t1u2v3w4x5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("max_matched_jobs", sa.Integer(), nullable=True))
    op.add_column("user_profiles", sa.Column("max_generated_resumes", sa.Integer(), nullable=True))
    op.add_column("user_profiles", sa.Column("max_generated_cover_letters", sa.Integer(), nullable=True))
    op.add_column(
        "user_profiles",
        sa.Column(
            "allowed_countries",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[\"Switzerland\", \"Luxembourg\"]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "allowed_countries")
    op.drop_column("user_profiles", "max_generated_cover_letters")
    op.drop_column("user_profiles", "max_generated_resumes")
    op.drop_column("user_profiles", "max_matched_jobs")
