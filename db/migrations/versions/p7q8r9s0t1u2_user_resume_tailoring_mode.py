"""user_profiles.resume_tailoring_mode: how much inference bullet rewriting is allowed (honest/jd_aligned)"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p7q8r9s0t1u2"
down_revision: Union[str, Sequence[str], None] = "o6p7q8r9s0t1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("resume_tailoring_mode", sa.String(length=20), nullable=False, server_default="honest"),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "resume_tailoring_mode")
