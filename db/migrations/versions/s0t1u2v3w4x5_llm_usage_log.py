"""new table llm_usage_log: raw per-call token counts for admin cost dashboard (see core/llm_pricing.py)"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s0t1u2v3w4x5"
down_revision: Union[str, Sequence[str], None] = "r9s0t1u2v3w4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("task_name", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_llm_usage_log_user_id", "llm_usage_log", ["user_id"])
    op.create_index("ix_llm_usage_log_created_at", "llm_usage_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_log_created_at", table_name="llm_usage_log")
    op.drop_index("ix_llm_usage_log_user_id", table_name="llm_usage_log")
    op.drop_table("llm_usage_log")
