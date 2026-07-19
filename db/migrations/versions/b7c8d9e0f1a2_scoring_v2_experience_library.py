"""scoring v2: experience library + atomic requirement matching

Revision ID: b7c8d9e0f1a2
Revises: 9d688c0d13f6
Create Date: 2026-07-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "9d688c0d13f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_job_directions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("expanded_text", sa.Text(), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embed_model", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_job_directions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_job_directions")),
    )

    op.create_table(
        "user_candidate_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("atoms", sa.JSON(), nullable=False),
        sa.Column("total_years_experience", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_candidate_facts_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_candidate_facts")),
        sa.UniqueConstraint("user_id", name=op.f("uq_user_candidate_facts_user_id")),
    )

    op.create_table(
        "user_experience_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("employer", sa.String(length=255), nullable=True),
        sa.Column("background", sa.Text(), nullable=True),
        sa.Column("actions", sa.Text(), nullable=True),
        sa.Column("technologies", sa.JSON(), nullable=False),
        sa.Column("ownership", sa.String(length=20), nullable=True),
        sa.Column("results", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("raw_date_text", sa.String(length=100), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_experience_units_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_experience_units")),
    )

    op.add_column("jobs", sa.Column("title_embedding", sa.JSON(), nullable=True))

    op.add_column(
        "user_job_scores",
        sa.Column(
            "requirement_matches",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "user_job_scores",
        sa.Column(
            "hard_constraints_hit",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.drop_column("user_job_scores", "matched_skills")
    op.drop_column("user_job_scores", "missing_skills")
    op.drop_column("user_job_scores", "recommendation")

    op.drop_column("user_profiles", "structured_profile")
    op.drop_column("user_profiles", "structured_profile_source_hash")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "user_profiles", sa.Column("structured_profile_source_hash", sa.String(length=64), nullable=True)
    )
    op.add_column("user_profiles", sa.Column("structured_profile", sa.JSON(), nullable=True))

    op.add_column(
        "user_job_scores",
        sa.Column("recommendation", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_job_scores",
        sa.Column("missing_skills", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "user_job_scores",
        sa.Column("matched_skills", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.drop_column("user_job_scores", "hard_constraints_hit")
    op.drop_column("user_job_scores", "requirement_matches")

    op.drop_column("jobs", "title_embedding")

    op.drop_table("user_experience_units")
    op.drop_table("user_candidate_facts")
    op.drop_table("user_job_directions")
