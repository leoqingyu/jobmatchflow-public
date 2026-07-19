from sqlalchemy.orm import Session

from core.config import settings
from core.constants import AssetType, ScoringDecision
from core.logger import get_logger
from db.models import GeneratedAsset, UserJobScore
from services.cover_letter_generation_service import CoverLetterGenerationService
from services.quota_service import check_generation_quota
from services.resume_generation_service import ResumeGenerationService

logger = get_logger(__name__)


class AssetGenerationService:
    """
    对 decision=generate 的岗位生成定制简历 + 求职信。简历走选材（相关性×含金量×历史偏好，
    代码算）+ 受约束改写 + DOCX 渲染（services/resume_generation_service.py）；求职信直接
    复用简历已经选好、改写好的 bullet + 岗位公司信息，一次 LLM 调用（services/
    cover_letter_generation_service.py）——所以简历必须先生成，求职信才有素材可用。
    """

    def __init__(self, db: Session):
        self.db = db

    def generate_for_high_score_jobs(self, user_id: int) -> dict:
        if not settings.auto_generate_assets:
            return {"resume_generated": 0, "resume_failed": 0, "generated": 0, "failed": 0}
        jobs_to_generate = self._get_jobs_to_generate(user_id)
        logger.info(f"待生成简历/求职信岗位数: {len(jobs_to_generate)}")

        resume_generated = 0
        resume_failed = 0
        letter_generated = 0
        letter_failed = 0

        for score_record in jobs_to_generate:
            if not check_generation_quota(self.db, user_id, "resume"):
                logger.info("user=%s 已达到 max_generated_resumes 额度，停止本轮生成", user_id)
                break
            try:
                ResumeGenerationService(self.db, user_id).generate_for_job(user_id, score_record.job_id)
                resume_generated += 1
            except Exception as e:
                logger.warning(f"简历生成失败 job_id={score_record.job_id}: {e}")
                resume_failed += 1
                continue

            if not check_generation_quota(self.db, user_id, "cover_letter"):
                logger.info("user=%s 已达到 max_generated_cover_letters 额度，跳过求职信生成", user_id)
                continue
            try:
                CoverLetterGenerationService(self.db, user_id).generate_for_job(user_id, score_record.job_id)
                letter_generated += 1
            except Exception as e:
                logger.warning(f"求职信生成失败 job_id={score_record.job_id}: {e}")
                letter_failed += 1

        return {
            "resume_generated": resume_generated,
            "resume_failed": resume_failed,
            "generated": letter_generated,
            "failed": letter_failed,
        }

    def generate_for_single_job(self, user_id: int, job_id: int) -> None:
        """生成/覆盖该岗位下的定制简历 + 求职信。"""
        if not settings.auto_generate_assets:
            logger.debug(
                "auto_generate_assets=False，跳过生成 user=%s job=%s", user_id, job_id
            )
            return
        if not check_generation_quota(self.db, user_id, "resume"):
            logger.info("user=%s 已达到 max_generated_resumes 额度，跳过 job=%s", user_id, job_id)
            return
        ResumeGenerationService(self.db, user_id).generate_for_job(user_id, job_id)

        if not check_generation_quota(self.db, user_id, "cover_letter"):
            logger.info("user=%s 已达到 max_generated_cover_letters 额度，跳过 job=%s 的求职信", user_id, job_id)
            return
        CoverLetterGenerationService(self.db, user_id).generate_for_job(user_id, job_id)

    def _get_jobs_to_generate(self, user_id: int) -> list[UserJobScore]:
        already_resume = (
            self.db.query(GeneratedAsset.job_id)
            .filter(
                GeneratedAsset.user_id == user_id,
                GeneratedAsset.asset_type == AssetType.RESUME_JSON.value,
            )
            .scalar_subquery()
        )
        return (
            self.db.query(UserJobScore)
            .filter(
                UserJobScore.user_id == user_id,
                UserJobScore.decision == ScoringDecision.GENERATE.value,
                UserJobScore.job_id.not_in(already_resume),
            )
            .all()
        )
