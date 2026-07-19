"""
打分主流程编排：Step 1 向量初筛 -> Step 2 JD 原子要求提取(缓存) -> Step 3 逐项匹配
-> Step 4 程序算分 -> Step 5（可选）主观偏好附加分（见 ai/scoring_rules.py::apply_preference_bonus，
只在用户填了 scoring preferences / 目标岗位标题时才调用）-> Step 6 推荐 Master CV（纯代码兜底，
不调 LLM，见 _pick_recommended_cv）。

三档模型分别用不同的 GeminiModelClient 实例（cheap/mid/match，见 core/config.py），
不是"用某个最新模型"一把梭。

Step 1 的 reject 现在也会落库（decision=discard, llm_model="vector_prefilter"）——不是像
早期设计那样"免费不落库、下次重跑"：一旦切到常驻 worker 逐条处理（见
services/matching_worker_service.py），"下次"可能就是几秒后，不落库会导致同一条被拒的岗位
永远排在队首、后面的岗位永远轮不到。落库让"这个 (user, job) 处理过了"这件事对两条路径
（一次性批量 / 常驻逐条）都成立，向量初筛本身够快，多存一行的成本可以忽略。

打分决定 decision=generate 后立刻在同一条岗位上生成简历+求职信（AssetGenerationService.
generate_for_single_job），不再是"先打完这批全部分，再回头扫一遍生成"的两阶段批处理。
"""

import time
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from core.config import settings
from core.constants import EMPLOYMENT_TYPE_REJECT_MODEL, PREFILTER_REJECT_MODEL, ScoringDecision
from core.job_markets import sanitize_user_countries
from core.llm_usage_tracking import llm_usage_context
from core.logger import get_logger
from core.cv_plain import master_cv_plain_text
from db.models import Job, UserCandidateFacts, UserExperienceUnit, UserJobScore, UserSearchProfile
from ai.direction_matching import load_active_direction_vectors, prefilter_job_with_similarity
from ai.scoring import (
    build_candidate_context_plain,
    compute_preference_bonus,
    load_match_system_prompt,
    match_requirements_to_candidate,
    match_requirements_to_candidate_with_cache,
)
from ai.scoring_rules import apply_preference_bonus, compute_final_score, employment_type_mismatch
from ai.llm_factory import get_scoring_llm_client
from services.experience_library_service import ExperienceLibraryService
from services.profile_service import ProfileService
from services.quota_service import match_quota_remaining
from services.user_cv_lookup import list_user_ids_ready_for_scoring

logger = get_logger(__name__)

_SCORING_BATCH_EVERY = 10
_SCORING_BATCH_SLEEP_SEC = 0.5


class JobScoringService:
    def __init__(self, db: Session):
        self.db = db
        self.llm_cheap = get_scoring_llm_client(settings.scoring_model_cheap)
        self.llm_mid = get_scoring_llm_client(settings.scoring_model_mid)
        self.llm_match = get_scoring_llm_client(settings.scoring_model_match)

    # ------------------------------------------------------------------
    # Step 2：JD 原子要求提取，job 级缓存——现在走 Gemini Batch API（见
    # services/gemini_jd_batch_service.py），不再在这里同步调 LLM。
    # ------------------------------------------------------------------
    def _ensure_structured_requirements(self, job: Job) -> dict | None:
        """None 表示还没抽取完：要么刚发现需要抽取、这次顺便标记排队，要么已经排队等
        Gemini batch 出结果。调用方应把这条岗位当"这次先跳过"处理——不算 reject，不落库，
        这个用户下次跑（下次点击 / 下次自动批次）会重新看一眼，Gemini batch 出结果之后自然
        会在某一次重新命中缓存分支。"""
        if job.structured_requirements and job.structured_requirements.get("requirements"):
            return job.structured_requirements
        if job.jd_extraction_queued_at is None:
            job.jd_extraction_queued_at = datetime.utcnow()
            self.db.flush()
        return None

    # ------------------------------------------------------------------
    # 候选人上下文：facts atoms 全量 + experience units 全量
    # ------------------------------------------------------------------
    def _load_candidate_context(self, user_id: int) -> tuple[str, float | None]:
        facts = (
            self.db.query(UserCandidateFacts)
            .filter(UserCandidateFacts.user_id == user_id)
            .first()
        )
        atoms = facts.atoms if facts else []
        total_years = facts.total_years_experience if facts else None

        profile = ProfileService(self.db).get_profile(user_id)
        education_entries = (
            (profile.master_cv_json or {}).get("education") or []
            if profile and profile.master_cv_json
            else []
        )

        units = (
            self.db.query(UserExperienceUnit)
            .filter(UserExperienceUnit.user_id == user_id)
            .order_by(UserExperienceUnit.order_index.asc(), UserExperienceUnit.id.asc())
            .all()
        )
        unit_dicts = [
            {
                "id": u.id,
                "title": u.title,
                "employer": u.employer,
                "background": u.background,
                "actions": u.actions,
                "technologies": u.technologies,
                "ownership": u.ownership,
                "results": u.results,
                "domain": u.domain,
                "start_date": u.start_date,
                "end_date": u.end_date,
                "raw_date_text": u.raw_date_text,
            }
            for u in units
        ]
        return build_candidate_context_plain(atoms, total_years, unit_dicts, education_entries), total_years

    # ------------------------------------------------------------------
    # Step 5（可选）：主观偏好上下文 —— scoring_preferences_text + 目标岗位标题，
    # 每个用户一批/一次调用只读一次（不是每个 job 读一次），见 _score_single_job。
    # ------------------------------------------------------------------
    def _load_preference_context(self, user_id: int) -> tuple[str | None, list[str]]:
        profile = ProfileService(self.db).get_profile(user_id)
        scoring_preferences_text = (profile.scoring_preferences_text or "").strip() if profile else ""
        directions = ExperienceLibraryService.list_directions(self.db, user_id)
        target_role_titles = [d.title for d in directions if d.is_active]
        return (scoring_preferences_text or None), target_role_titles

    # ------------------------------------------------------------------
    # Step 0：实习/全职偏好，见 ai/scoring_rules.py::employment_type_mismatch
    # ------------------------------------------------------------------
    def _load_employment_type_preference(self, user_id: int) -> str:
        profile = ProfileService(self.db).get_profile(user_id)
        return (profile.employment_type_preference if profile else None) or "both"

    # ------------------------------------------------------------------
    # Step 1 方向扩写用：候选人真实技能/过往岗位的紧凑摘要（不是 Step 3 那份完整
    # candidate_context_plain——那份是给逐项匹配用的详细版，这里只需要几行给 LLM
    # "接地"用，见 ai.scoring.expand_direction 的 candidate_background 参数）。
    # ------------------------------------------------------------------
    def _load_candidate_background_summary(self, user_id: int) -> str | None:
        facts = (
            self.db.query(UserCandidateFacts)
            .filter(UserCandidateFacts.user_id == user_id)
            .first()
        )
        skill_labels = [a.get("label") for a in (facts.atoms if facts else []) if a.get("type") == "skill"]
        units = (
            self.db.query(UserExperienceUnit)
            .filter(UserExperienceUnit.user_id == user_id)
            .order_by(UserExperienceUnit.order_index.asc(), UserExperienceUnit.id.asc())
            .all()
        )
        unit_lines = [
            f"{u.title or 'Untitled role'}" + (f" ({u.domain})" if u.domain else "")
            for u in units
        ]
        parts = []
        if skill_labels:
            parts.append("Skills: " + ", ".join(skill_labels))
        if unit_lines:
            parts.append("Past roles: " + "; ".join(unit_lines))
        return "\n".join(parts) if parts else None

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def score_new_jobs_for_user(self, user_id: int, *, max_jobs: int | None = None) -> dict:
        """max_jobs：可选的"这次最多打这么多条"上限（比如新用户第一次手动点击"开始匹配"
        时限流到 500 条，见 api/experience_routes.py::api_start_job_search）；不传就是不限，
        走到 admin 配额（max_matched_jobs，见 services/quota_service.py）为止。"""
        with llm_usage_context(user_id):
            candidate_context, candidate_total_years = self._load_candidate_context(user_id)
            scoring_preferences_text, target_role_titles = self._load_preference_context(user_id)
            employment_type_preference = self._load_employment_type_preference(user_id)
            direction_vectors = load_active_direction_vectors(
                self.db, user_id, llm=self.llm_mid,
                candidate_background=self._load_candidate_background_summary(user_id),
            )

            unscored_jobs = self._get_unscored_jobs(user_id)
            logger.info(f"待评分岗位数: {len(unscored_jobs)}")

            scored = 0
            skipped = 0
            rejected = 0
            pending_extraction = 0

            cache_name: str | None = None
            use_explicit_cache = (
                settings.gemini_scoring_explicit_cache
                and self.llm_match.supports_explicit_cache
                and any(j.description_clean for j in unscored_jobs)
            )
            if use_explicit_cache:
                try:
                    cache_name = self.llm_match.create_job_scoring_cache(
                        system_instruction=load_match_system_prompt(),
                        candidate_context_plain=candidate_context,
                        user_id=user_id,
                    )
                    logger.info("用户 %s 评分使用 Gemini 显式缓存 cache=%s", user_id, cache_name)
                except Exception as e:
                    logger.warning(
                        "用户 %s 评分显式缓存创建失败，本批次改为非缓存: %s", user_id, e
                    )
                    cache_name = None

            try:
                score_attempts = 0
                for job in unscored_jobs:
                    if max_jobs is not None and scored >= max_jobs:
                        break
                    # admin 配额（见 services/quota_service.py）：不只是"谁能被排进待打分名单"
                    # （list_user_ids_ready_for_scoring）那一层，这里也查一遍——手动点击
                    # （api_start_job_search）不走那个名单，直接指定 user_id 调这个方法，
                    # 不在这里兜底的话配额形同虚设。
                    if not match_quota_remaining(self.db, user_id):
                        logger.info("user=%s 已达到 max_matched_jobs 额度，停止本轮打分", user_id)
                        break

                    # 每条 job 处理完立刻 commit：这个循环跟常驻 worker（matching_worker_service.py，
                    # 每个 job 独立 session）可能并发处理同一批用户，撞了 UserJobScore 唯一约束时
                    # IntegrityError 只应回滚"这一条"。之前整批共用一个未提交事务，rollback() 会把
                    # 循环里前面已经打完分、还没来得及 commit 的 job 一起吃掉——分数计数正常、日志
                    # 干净，实际数据静默丢失。commit 边界收紧到单条 job，rollback 的影响面也跟着收紧。
                    # Step 1：免费向量初筛，reject 落库为 discard（见文件头注释：不再是"不落库
                    # 免费重跑"，这样常驻 worker 逐条处理时才不会卡在同一条被拒的岗位上）
                    label, vector_similarity = prefilter_job_with_similarity(
                        self.db, job, direction_vectors
                    )
                    if label == "reject":
                        self._persist_prefilter_reject(user_id, job, vector_similarity)
                        self.db.commit()
                        rejected += 1
                        continue

                    # Step 0：实习/全职硬过滤，跟 Step 1 一样在花钱做逐项匹配之前拦掉——命中就
                    # 落成 discard，直接跳过下面的 Step 3（见 ai/scoring_rules.py 文件头 docstring）。
                    # 需要先跑一次 Step 2（job 级缓存，命中就是缓存返回；没命中现在会排队交给
                    # Gemini Batch API，见 services/gemini_jd_batch_service.py，这次先跳过，
                    # 不算 reject）才知道这条岗位的 employment_type。
                    structured = self._ensure_structured_requirements(job)
                    if structured is None:
                        pending_extraction += 1
                        continue
                    if employment_type_mismatch(employment_type_preference, job.employment_type):
                        self._persist_employment_type_reject(
                            user_id, job, job.employment_type, employment_type_preference
                        )
                        self.db.commit()
                        rejected += 1
                        continue

                    try:
                        self._score_single_job(
                            user_id,
                            job,
                            candidate_context,
                            scoring_cache_name=cache_name,
                            vector_similarity=vector_similarity,
                            candidate_total_years=candidate_total_years,
                            scoring_preferences_text=scoring_preferences_text,
                            target_role_titles=target_role_titles,
                        )
                        self.db.commit()
                        scored += 1
                    except Exception as e:
                        logger.warning(f"评分失败 job_id={job.id}: {e}")
                        self.db.rollback()
                        skipped += 1
                    score_attempts += 1
                    if score_attempts % _SCORING_BATCH_EVERY == 0:
                        time.sleep(_SCORING_BATCH_SLEEP_SEC)
            finally:
                if cache_name:
                    self.llm_match.delete_scoring_cache(cache_name)

            return {
                "scored": scored,
                "skipped": skipped,
                "rejected": rejected,
                "pending_extraction": pending_extraction,
            }

    def score_and_generate_next_job_for_user(self, user_id: int) -> dict | None:
        """
        常驻 worker 用（services/matching_worker_service.py）：只处理"下一条"，不是一次性
        扫完这个用户所有待评分岗位——每次调用处理 0 或 1 条，处理完立刻返回，方便 worker
        在多个用户之间轮转、随时能被中断。不用显式 Gemini 缓存（那是为"同一用户一口气打很多
        条"省钱设计的，单条场景没有意义）。

        返回 None 表示这个用户当前没有待处理的岗位；否则返回 {"job_id", "outcome"}
        （outcome: "rejected" / "scored" / "error"）。
        """
        with llm_usage_context(user_id):
            job = self._get_next_unscored_job(user_id)
            if job is None:
                return None

            direction_vectors = load_active_direction_vectors(
                self.db, user_id, llm=self.llm_mid,
                candidate_background=self._load_candidate_background_summary(user_id),
            )
            label, vector_similarity = prefilter_job_with_similarity(
                self.db, job, direction_vectors
            )
            if label == "reject":
                self._persist_prefilter_reject(user_id, job, vector_similarity)
                return {"job_id": job.id, "outcome": "rejected"}

            # Step 0：实习/全职硬过滤，跟批量入口（score_new_jobs_for_user）同一套逻辑；
            # None 表示还没抽取完（已排队等 Gemini batch），这次先跳过，不算 reject。
            structured = self._ensure_structured_requirements(job)
            if structured is None:
                return {"job_id": job.id, "outcome": "pending_extraction"}
            employment_type_preference = self._load_employment_type_preference(user_id)
            if employment_type_mismatch(employment_type_preference, job.employment_type):
                self._persist_employment_type_reject(
                    user_id, job, job.employment_type, employment_type_preference
                )
                return {"job_id": job.id, "outcome": "rejected"}

            candidate_context, candidate_total_years = self._load_candidate_context(user_id)
            scoring_preferences_text, target_role_titles = self._load_preference_context(user_id)
            try:
                self._score_single_job(
                    user_id,
                    job,
                    candidate_context,
                    scoring_cache_name=None,
                    vector_similarity=vector_similarity,
                    candidate_total_years=candidate_total_years,
                    scoring_preferences_text=scoring_preferences_text,
                    target_role_titles=target_role_titles,
                )
                return {"job_id": job.id, "outcome": "scored"}
            except Exception as e:
                logger.warning(f"评分失败 job_id={job.id}: {e}")
                return {"job_id": job.id, "outcome": "error"}

    def _persist_prefilter_reject(
        self, user_id: int, job: Job, vector_similarity: float | None
    ) -> None:
        record = UserJobScore(
            user_id=user_id,
            job_id=job.id,
            score=0.0,
            decision=ScoringDecision.DISCARD.value,
            requirement_matches=[],
            hard_constraints_hit=[],
            score_breakdown={
                "reason": "direction_vector_prefilter_reject",
                "vector_similarity": vector_similarity,
            },
            llm_model=PREFILTER_REJECT_MODEL,
        )
        try:
            self.db.add(record)
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            logger.debug(f"打分记录已存在 user={user_id} job={job.id}，跳过")

    def _persist_employment_type_reject(
        self, user_id: int, job: Job, job_employment_type: str | None, preference: str
    ) -> None:
        record = UserJobScore(
            user_id=user_id,
            job_id=job.id,
            score=0.0,
            decision=ScoringDecision.DISCARD.value,
            requirement_matches=[],
            hard_constraints_hit=[],
            score_breakdown={
                "reason": "employment_type_mismatch",
                "job_employment_type": job_employment_type,
                "employment_type_preference": preference,
            },
            llm_model=EMPLOYMENT_TYPE_REJECT_MODEL,
        )
        try:
            self.db.add(record)
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            logger.debug(f"打分记录已存在 user={user_id} job={job.id}，跳过")

    def score_new_jobs_for_all_users(self) -> dict:
        user_ids = list_user_ids_ready_for_scoring(self.db)
        by_user: dict[int, dict[str, int]] = {}
        total_scored = 0
        total_skipped = 0
        for uid in user_ids:
            r = self.score_new_jobs_for_user(uid)
            by_user[uid] = r
            total_scored += r["scored"]
            total_skipped += r["skipped"]
        logger.info(
            "全员评分: 用户数=%s 合计 scored=%s skipped=%s",
            len(user_ids),
            total_scored,
            total_skipped,
        )
        return {
            "users": len(user_ids),
            "total_scored": total_scored,
            "total_skipped": total_skipped,
            "by_user": by_user,
        }

    def _score_single_job(
        self,
        user_id: int,
        job: Job,
        candidate_context_plain: str,
        *,
        scoring_cache_name: str | None = None,
        vector_similarity: float | None = None,
        candidate_total_years: float | None = None,
        scoring_preferences_text: str | None = None,
        target_role_titles: list[str] | None = None,
        trigger_generation: bool = True,
    ) -> UserJobScore:
        if not job.description_clean:
            raise ValueError("岗位无 JD 正文")

        structured_requirements = self._ensure_structured_requirements(job)
        requirements = structured_requirements.get("requirements") or []
        job_seniority = structured_requirements.get("job_seniority")

        match_result = None
        if scoring_cache_name:
            try:
                match_result = match_requirements_to_candidate_with_cache(
                    self.llm_match, requirements, scoring_cache_name
                )
            except Exception as e:
                # 缓存+schema 组合这台机器没法验证过是否被 Gemini 支持，出错只对这一条岗位
                # 回退非缓存重试，不整批放弃缓存
                logger.warning(
                    "job_id=%s 缓存+schema 匹配失败，回退非缓存重试: %s", job.id, e
                )
        if match_result is None:
            match_result = match_requirements_to_candidate(
                self.llm_match, requirements, candidate_context_plain
            )

        breakdown = compute_final_score(
            requirements,
            match_result.get("matches") or [],
            job_seniority=job_seniority,
            candidate_total_years=candidate_total_years,
        )

        # Step 5（可选）：只在用户填了 scoring preferences 或目标岗位标题时才调 AI，
        # 零填写=零额外调用/零额外成本，见 ai/scoring_rules.py::apply_preference_bonus
        if scoring_preferences_text or target_role_titles:
            try:
                bonus_result = compute_preference_bonus(
                    self.llm_cheap,
                    job_title=job.title,
                    job_company=job.company,
                    job_domain=structured_requirements.get("job_domain"),
                    job_seniority=job_seniority,
                    reason_summary=match_result.get("reason_summary"),
                    scoring_preferences_text=scoring_preferences_text,
                    target_role_titles=target_role_titles or [],
                )
                breakdown = apply_preference_bonus(
                    breakdown, bonus_result["bonus"], bonus_result.get("reason")
                )
            except Exception as e:
                logger.warning("job_id=%s 偏好附加分计算失败，按 0 分处理: %s", job.id, e)

        rec_id, master_id = self._pick_recommended_cv(user_id)

        score_breakdown = dict(breakdown["score_breakdown"] or {})
        score_breakdown["vector_similarity"] = vector_similarity
        score_record = UserJobScore(
            user_id=user_id,
            job_id=job.id,
            master_cv_id=master_id,
            recommended_cv_id=rec_id,
            score=breakdown["score"],
            decision=breakdown["decision"],
            reason_summary=match_result.get("reason_summary"),
            requirement_matches=breakdown["requirement_matches"],
            hard_constraints_hit=breakdown["hard_constraints_hit"],
            score_breakdown=score_breakdown,
            llm_model=self.llm_match.model_name,
        )
        try:
            self.db.add(score_record)
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            logger.debug(f"评分记录已存在 user={user_id} job={job.id}，跳过")
            return score_record

        if trigger_generation and score_record.decision == ScoringDecision.GENERATE.value:
            self._generate_now(user_id, job.id)
        return score_record

    def _generate_now(self, user_id: int, job_id: int) -> None:
        """
        decision=generate 落库后立刻在同一条岗位上生成简历+求职信，不再攒一批之后再回头扫。
        生成失败（比如经历库暂时为空）不影响打分本身已经落库这件事，只记警告。
        """
        from services.asset_service import AssetGenerationService

        try:
            AssetGenerationService(self.db).generate_for_single_job(user_id, job_id)
        except Exception as e:
            logger.warning("job_id=%s 打分后立即生成简历/求职信失败: %s", job_id, e)

    # ------------------------------------------------------------------
    # Step 6：推荐 Master CV —— 纯代码兜底，不调 LLM
    # ------------------------------------------------------------------
    def _pick_recommended_cv(self, user_id: int) -> tuple[int | None, int | None]:
        """
        生成阶段完全由经历库驱动，不读这两个字段决定生成什么内容——它们只是给 jobs 列表/
        投递记录展示用的"关联简历"标签，以及 tracking 标记已投递时的兜底默认值（见
        services/tracking_service.py）。不值得为了一个纯展示用的标签调 LLM：0 份可用 CV
        就是 (None, None)，否则固定选第一份（按 id 顺序），不再"智能挑一份最合适的"。
        """
        cvs = ProfileService.list_master_cvs(self.db, user_id)
        usable = [c for c in cvs if master_cv_plain_text(c).strip()]
        if not usable:
            return None, None
        cid = usable[0].id
        return cid, cid

    def _user_countries(self, user_id: int) -> list[str]:
        """用户当前生效的求职国家（UserSearchProfile.countries，已按允许列表清洗）。"""
        profile = (
            self.db.query(UserSearchProfile)
            .filter(UserSearchProfile.user_id == user_id, UserSearchProfile.is_active == True)  # noqa: E712
            .first()
        )
        if not profile or not profile.countries:
            return []
        return sanitize_user_countries(list(profile.countries))

    def _unscored_jobs_query(self, user_id: int):
        """
        待评分岗位的共用筛选：active + 有 JD 正文 + 该用户尚未处理过（含 reject，见
        _persist_prefilter_reject）+ 国家匹配。JD 是否存在直接在 SQL 里过滤——不用等进了
        Python 循环才发现没 JD 再跳过，常驻 worker 逐条取"下一条"时尤其重要，否则同一条
        没 JD 的岗位会一直被当成"下一条"，排在它后面、已经有 JD 的岗位永远轮不到。
        """
        scored_job_ids = (
            self.db.query(UserJobScore.job_id)
            .filter(UserJobScore.user_id == user_id)
            .distinct()
            .scalar_subquery()
        )
        query = self.db.query(Job).filter(
            Job.status == "active",
            Job.id.not_in(scored_job_ids),
            Job.description_clean.isnot(None),
            Job.description_clean != "",
        )

        # 第一道硬性排除：岗位国家必须在用户选择的求职国家里，不匹配的压根不进打分流程
        # （不是打低分，是根本不看）。用户没配置求职国家时视为未设限制，不过滤。
        countries = self._user_countries(user_id)
        if countries:
            query = query.filter(Job.country.in_(countries))
        return query

    def _get_unscored_jobs(self, user_id: int) -> list[Job]:
        return self._unscored_jobs_query(user_id).all()

    def _get_next_unscored_job(self, user_id: int) -> Job | None:
        """常驻 worker 用：只取一条，按 id 倒序（新岗优先），跟领英 JD worker 的取用顺序一致。"""
        return self._unscored_jobs_query(user_id).order_by(Job.id.desc()).first()
