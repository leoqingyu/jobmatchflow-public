"""user_profiles.generation_model: which LLM (gemini/claude) resume/cover-letter generation uses"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "o6p7q8r9s0t1"
down_revision: Union[str, Sequence[str], None] = "n5o6p7q8r9s0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("generation_model", sa.String(length=20), nullable=False, server_default="gemini"),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "generation_model")
