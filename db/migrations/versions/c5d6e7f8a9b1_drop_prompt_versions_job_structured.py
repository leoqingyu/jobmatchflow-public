"""drop prompt_version columns and job_structured_data

Revision ID: c5d6e7f8a9b1
Revises: a3b4c5d6e7f9
Create Date: 2026-03-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d6e7f8a9b1"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM generated_assets AS a
        USING generated_assets AS b
        WHERE a.id < b.id
          AND a.user_id = b.user_id
          AND a.job_id = b.job_id
          AND a.asset_type = b.asset_type;
        """
    )
    op.drop_constraint(
        "uq_generated_assets_version", "generated_assets", type_="unique"
    )
    op.drop_column("generated_assets", "prompt_version")
    op.create_unique_constraint(
        "uq_generated_assets_user_job_asset",
        "generated_assets",
        ["user_id", "job_id", "asset_type"],
    )

    op.execute(
        """
        DELETE FROM user_job_scores AS a
        USING user_job_scores AS b
        WHERE a.id < b.id
          AND a.user_id = b.user_id
          AND a.job_id = b.job_id
          AND a.llm_model = b.llm_model;
        """
    )
    op.drop_constraint(
        "uq_user_job_scores_version", "user_job_scores", type_="unique"
    )
    op.drop_column("user_job_scores", "prompt_version")
    op.create_unique_constraint(
        "uq_user_job_scores_user_job_model",
        "user_job_scores",
        ["user_id", "job_id", "llm_model"],
    )

    op.execute(
        """
        DELETE FROM user_linkedin_prescreens AS a
        USING user_linkedin_prescreens AS b
        WHERE a.id < b.id
          AND a.user_id = b.user_id
          AND a.job_id = b.job_id;
        """
    )
    op.drop_constraint(
        "uq_user_linkedin_prescreen_version",
        "user_linkedin_prescreens",
        type_="unique",
    )
    op.drop_column("user_linkedin_prescreens", "prompt_version")
    op.create_unique_constraint(
        "uq_user_linkedin_prescreen_user_job",
        "user_linkedin_prescreens",
        ["user_id", "job_id"],
    )

    op.drop_table("job_structured_data")


def downgrade() -> None:
    raise NotImplementedError("downgrade 未实现：请从备份恢复库")
