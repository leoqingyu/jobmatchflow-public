"""resume material library: experience tier, job domain, preference events

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-07-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("user_experience_units", sa.Column("tier", sa.String(length=20), nullable=True))
    op.add_column("jobs", sa.Column("domain", sa.String(length=50), nullable=True))

    op.create_table(
        "user_experience_preference_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("job_context", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["user_experience_units.id"],
            name=op.f("fk_user_experience_preference_events_item_id_user_experience_units"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_user_experience_preference_events_job_id_jobs")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_experience_preference_events_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_experience_preference_events")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("user_experience_preference_events")
    op.drop_column("jobs", "domain")
    op.drop_column("user_experience_units", "tier")
