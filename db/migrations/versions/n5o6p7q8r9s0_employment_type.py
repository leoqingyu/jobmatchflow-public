"""employment type: jobs.employment_type + user_profiles.employment_type_preference"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n5o6p7q8r9s0"
down_revision: Union[str, Sequence[str], None] = "m4n5o6p7q8r9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("employment_type", sa.String(length=20), nullable=True))
    op.add_column(
        "user_profiles",
        sa.Column("employment_type_preference", sa.String(length=20), nullable=False, server_default="both"),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "employment_type_preference")
    op.drop_column("jobs", "employment_type")
