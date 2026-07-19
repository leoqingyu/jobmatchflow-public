from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from core.logger import get_logger
from core.constants import PipelineStatus
from core.job_markets import sanitize_user_countries
from db.models import PipelineRun, User, UserSearchProfile
from services.ingestion_service import JobIngestionService
from services.pre_db_pipeline import jobs_ready_for_ingestion, run_pre_db_pipeline
from services.scoring_service import JobScoringService
from services.asset_service import AssetGenerationService
from services.notification_service import NotificationService
from services.user_cv_lookup import list_user_ids_ready_for_scoring

logger = get_logger(__name__)


class PipelineService:
    def __init__(self, db: Session):
        self.db = db

    def run_full_pipeline(self, user_id: int) -> dict:
        """
        完整流水线：抓取 → 全员评分 → 全员生成/通知。

        ``user_id`` 当前用于：(1) 写入 ``PipelineRun`` 归属；(2) 读取该用户的求职国家以决定**本轮抓取市场**
        （过渡设计：手动触发调试用）。产品化后应由定时任务或管理员入口触发，国家/市场改为系统侧配置，
        与用户前台无关；用户端只消费已打分的岗与已生成的资产。
        """
        run = PipelineRun(
            user_id=user_id,
            run_type="full",
            started_at=datetime.utcnow(),
            status=PipelineStatus.RUNNING.value,
        )
        self.db.add(run)
        self.db.flush()

        try:
            result = self._execute(user_id, run)
            run.status = PipelineStatus.DONE.value
            run.finished_at = datetime.utcnow()
            logger.info(f"Pipeline 完成: {result}")
            return result
        except Exception as e:
            run.status = PipelineStatus.FAILED.value
            run.error_message = str(e)
            run.finished_at = datetime.utcnow()
            logger.error(f"Pipeline 失败: {e}")
            raise

    def _execute(self, user_id: int, run: PipelineRun) -> dict:
        search_profile = (
            self.db.query(UserSearchProfile)
            .filter(UserSearchProfile.user_id == user_id, UserSearchProfile.is_active == True)  # noqa: E712
            .first()
        )
        countries: list[str] = []
        if search_profile and search_profile.countries:
            countries = sanitize_user_countries(list(search_profile.countries))
        if not countries:
            logger.warning(
                "user_id=%s 未配置求职国家（Settings 多选为空），跳过抓取，本次无新岗位入库",
                user_id,
            )

        # 1. 入库前流水线（抓取 + LLM 去重），再写入 jobs
        orch_result = run_pre_db_pipeline(countries=countries, skip_llm=False)
        merged = jobs_ready_for_ingestion(orch_result)
        jobspy_kept = len(orch_result.jobs)

        ingestion = JobIngestionService(self.db)
        fetch_result = ingestion.ingest_raw_jobs(
            merged,
            log_source="scrape_orchestrator",
            countries=countries,
            keyword_note=f"pre_db_{orch_result.meta.get('hours_old')}h",
        )

        meta = dict(orch_result.meta)
        meta["jobspy_after_dedup_count"] = jobspy_kept

        run.jobs_fetched = fetch_result["fetched"]
        run.jobs_inserted = fetch_result["inserted"]
        self.db.flush()

        logger.info(
            f"抓取入库: jobspy_deduped={jobspy_kept} inserted={fetch_result['inserted']}"
        )

        # 2–4. 评分 → 生成/通知 → 岗位过期标记（与独立下游任务共用）
        new_ids = list(fetch_result.get("new_job_ids") or [])
        downstream = self._run_downstream(run, new_job_ids=new_ids, trigger_user_id=user_id)
        meta["expired_jobs_marked"] = downstream["expired_jobs_marked"]

        return {
            "fetched": fetch_result["fetched"],
            "inserted": fetch_result["inserted"],
            "new_job_ids": new_ids,
            "scored": downstream["scored"],
            "scored_trigger_user": downstream["scored_trigger_user"],
            "scoring_by_user": downstream["scoring_by_user"],
            "generated": downstream["generated"],
            "generated_by_user": downstream["generated_by_user"],
            "generated_trigger_user": downstream["generated_trigger_user"],
            "notified": downstream["notified"],
            "notified_by_user": downstream["notified_by_user"],
            "notified_trigger_user": downstream["notified_trigger_user"],
            "orchestrator_meta": meta,
        }

    def _run_downstream(
        self,
        run: PipelineRun,
        *,
        new_job_ids: Optional[list[int]],
        trigger_user_id: Optional[int],
    ) -> dict:
        """
        全员评分 → 全员生成/通知 → 岗位过期标记。只被 `_execute`（手动全量触发，AdminPage 的
        "Run Pipeline" 按钮）调用——常规情况下这一整套现在由独立 24h 常驻进程
        （scripts/run_matching_worker.py，见 services/matching_worker_service.py）逐条处理，
        不用等这里。这个方法留着只为兼容那个手动调试入口；打分/生成本身"已处理过就跳过"，
        跟常驻进程重叠调用不会出错，只是大概率是空跑。
        """
        # 2. 全员评分：凡有 JD 且该用户"具备打分条件"（求职方向或经历库）尚未评分的岗都打分
        scoring = JobScoringService(self.db)
        score_result = scoring.score_new_jobs_for_all_users()
        run.jobs_scored = int(score_result.get("total_scored", 0))
        self.db.flush()

        # 3. 生成 + 通知：按每个"具备打分条件"的用户分别执行（同一 JD 在 user_job_scores 中
        # 本就有各行）；跟打分用同一个准入条件，不再用经历库之外的"有没有 cv_material_library_html"
        # 这条老门槛——那是给评分阶段之前另一套设计用的，实际生成（ResumeGenerationService）
        # 只要求经历库非空，跟它无关，用它当门槛会漏掉只填经历库、没走过 PDF 上传流程的用户
        asset_svc = AssetGenerationService(self.db)
        notifier = NotificationService(self.db)
        cv_user_ids = list_user_ids_ready_for_scoring(self.db)
        gen_by_user: dict[int, dict] = {}
        notify_by_user: dict[int, dict] = {}
        total_generated = 0
        total_notified = 0
        for uid in cv_user_ids:
            gr = asset_svc.generate_for_high_score_jobs(uid)
            gen_by_user[uid] = gr
            total_generated += int(gr.get("generated", 0))
            nr = notifier.notify_new_high_score_jobs(uid)
            notify_by_user[uid] = nr
            total_notified += int(nr.get("sent", 0))
        run.jobs_generated = total_generated
        run.jobs_notified = total_notified
        self.db.flush()

        # 4. 岗位过期标记（超过 JOB_EXPIRE_AFTER_DAYS 天的一律标 expired）
        expired_count = JobIngestionService(self.db).expire_stale_jobs()

        trigger_user_scores = (
            (score_result.get("by_user") or {}).get(trigger_user_id) or {}
            if trigger_user_id is not None
            else {}
        )
        trigger_user_gen = (
            gen_by_user.get(trigger_user_id) if trigger_user_id is not None else None
        ) or {"generated": 0, "failed": 0}
        trigger_user_notify = (
            notify_by_user.get(trigger_user_id) if trigger_user_id is not None else None
        ) or {"sent": 0}

        return {
            "new_job_ids": list(new_job_ids) if new_job_ids is not None else [],
            "scored": score_result.get("total_scored", 0),
            "scored_trigger_user": trigger_user_scores.get("scored", 0),
            "scoring_by_user": score_result.get("by_user", {}),
            "generated": total_generated,
            "generated_by_user": gen_by_user,
            "generated_trigger_user": trigger_user_gen.get("generated", 0),
            "notified": total_notified,
            "notified_by_user": notify_by_user,
            "notified_trigger_user": trigger_user_notify.get("sent", 0),
            "expired_jobs_marked": expired_count,
        }
