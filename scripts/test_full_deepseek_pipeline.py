"""
一次性调研脚本：对一小批（默认 15 个）已经有「纯 gemini」+「混合（Step2 用 gemini 缓存，
Step3/5 用 deepseek）」两份结果的岗位，额外跑一遍「纯 deepseek 全流程」——Step2 JD 原子要求
提取也真正用 deepseek 重新跑一次（不读、不写 Job.structured_requirements 这个全站共享缓存，
避免污染正在跑的对比批次和其它用户/模型的复用），再用这份 deepseek 自己提取的 requirements
接着跑 Step3/5，算出一个"从头到尾都是 deepseek"的最终分数。

三者放一起：
- gemini_score：纯 gemini（现有 UserJobScore，llm_model=gemini-3.1-flash-lite）
- hybrid_deepseek_score：混合（现有 UserJobScore，llm_model=deepseek-v4-flash，Step2 其实是
  gemini 提取的，只是 Step3/5 换成了 deepseek，见 services/scoring_service.py 的 job 级缓存）
- full_deepseek_score：本脚本现算的，Step2/3/5 全部 deepseek

输出：一份 JSON（scripts 不落库，纯手动 review 用），包含每个岗位三档分数 + gemini/deepseek
两份 Step2 提取结果（requirements 数量、job_domain、employment_type、job_seniority）方便对比
结构化提取质量本身有没有差异。

跑法：python3 scripts/test_full_deepseek_pipeline.py --user-id 1 --sample 15
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings
from core.logger import get_logger
from db.models import Job, UserJobScore
from db.session import SessionLocal
from ai.llm_factory import get_scoring_llm_client
from ai.scoring import extract_jd_requirements, match_requirements_to_candidate, compute_preference_bonus
from ai.scoring_rules import compute_final_score, apply_preference_bonus
from services.scoring_service import JobScoringService

logger = get_logger(__name__)

_OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"


def pick_sample(db, user_id: int, sample_size: int) -> list[int]:
    gem_ids = {
        r.job_id
        for r in db.query(UserJobScore.job_id)
        .filter(UserJobScore.user_id == user_id, UserJobScore.llm_model == "gemini-3.1-flash-lite")
        .all()
    }
    ds_ids = {
        r.job_id
        for r in db.query(UserJobScore.job_id)
        .filter(UserJobScore.user_id == user_id, UserJobScore.llm_model == settings.deepseek_model_name)
        .all()
    }
    both = sorted(gem_ids & ds_ids)
    if not both:
        return []
    step = max(1, len(both) // sample_size)
    return both[::step][:sample_size]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--sample", type=int, default=15)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        job_ids = pick_sample(db, args.user_id, args.sample)
        print(f"样本岗位数: {len(job_ids)} -> {job_ids}")
        if not job_ids:
            print("没有同时具备 gemini + deepseek 结果的岗位，退出")
            return

        svc = JobScoringService(db)
        candidate_context, candidate_total_years = svc._load_candidate_context(args.user_id)
        scoring_preferences_text, target_role_titles = svc._load_preference_context(args.user_id)

        ds_client = get_scoring_llm_client(settings.deepseek_model_name)

        results = []
        for jid in job_ids:
            job = db.query(Job).filter(Job.id == jid).first()
            gem = (
                db.query(UserJobScore)
                .filter(UserJobScore.user_id == args.user_id, UserJobScore.job_id == jid, UserJobScore.llm_model == "gemini-3.1-flash-lite")
                .first()
            )
            hybrid = (
                db.query(UserJobScore)
                .filter(UserJobScore.user_id == args.user_id, UserJobScore.job_id == jid, UserJobScore.llm_model == settings.deepseek_model_name)
                .first()
            )
            gem_structured = job.structured_requirements or {}

            print(f"[{jid}] {job.title!r} 跑纯 deepseek 全流程 Step2...")
            try:
                ds_structured = extract_jd_requirements(ds_client, job.description_clean)
            except Exception as e:
                logger.warning("job_id=%s deepseek Step2 提取失败: %s", jid, e)
                results.append({"job_id": jid, "title": job.title, "error": f"step2_failed: {e}"})
                continue

            ds_requirements = ds_structured.get("requirements") or []
            print(f"[{jid}] 跑纯 deepseek Step3（{len(ds_requirements)} 条要求）...")
            try:
                match_result = match_requirements_to_candidate(ds_client, ds_requirements, candidate_context)
            except Exception as e:
                logger.warning("job_id=%s deepseek Step3 失败: %s", jid, e)
                results.append({"job_id": jid, "title": job.title, "error": f"step3_failed: {e}"})
                continue

            breakdown = compute_final_score(
                ds_requirements,
                match_result.get("matches") or [],
                job_seniority=ds_structured.get("job_seniority"),
                candidate_total_years=candidate_total_years,
            )

            if scoring_preferences_text or target_role_titles:
                try:
                    bonus_result = compute_preference_bonus(
                        ds_client,
                        job_title=job.title,
                        job_company=job.company,
                        job_domain=ds_structured.get("job_domain"),
                        job_seniority=ds_structured.get("job_seniority"),
                        reason_summary=match_result.get("reason_summary"),
                        scoring_preferences_text=scoring_preferences_text,
                        target_role_titles=target_role_titles or [],
                    )
                    breakdown = apply_preference_bonus(breakdown, bonus_result["bonus"], bonus_result.get("reason"))
                except Exception as e:
                    logger.warning("job_id=%s deepseek Step5 失败，按0分处理: %s", jid, e)

            full_ds_score = breakdown["score"]
            full_ds_decision = breakdown["decision"]

            results.append(
                {
                    "job_id": jid,
                    "title": job.title,
                    "company": job.company,
                    "gemini_score": gem.score if gem else None,
                    "gemini_decision": gem.decision if gem else None,
                    "hybrid_deepseek_score": hybrid.score if hybrid else None,
                    "hybrid_deepseek_decision": hybrid.decision if hybrid else None,
                    "full_deepseek_score": full_ds_score,
                    "full_deepseek_decision": full_ds_decision,
                    "gemini_step2": {
                        "n_requirements": len(gem_structured.get("requirements") or []),
                        "job_domain": gem_structured.get("job_domain"),
                        "job_seniority": gem_structured.get("job_seniority"),
                        "employment_type": gem_structured.get("employment_type"),
                        "requirements": gem_structured.get("requirements"),
                    },
                    "deepseek_step2": {
                        "n_requirements": len(ds_requirements),
                        "job_domain": ds_structured.get("job_domain"),
                        "job_seniority": ds_structured.get("job_seniority"),
                        "employment_type": ds_structured.get("employment_type"),
                        "requirements": ds_requirements,
                    },
                    "full_deepseek_reason_summary": match_result.get("reason_summary"),
                    "full_deepseek_matches": breakdown["requirement_matches"],
                }
            )
            print(
                f"[{jid}] gemini={gem.score if gem else None} "
                f"hybrid_ds={hybrid.score if hybrid else None} full_ds={full_ds_score}"
            )
    finally:
        db.close()

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = _OUT_DIR / f"full_deepseek_pipeline_test_{ts}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"{'job_id':>7} {'gemini':>7} {'hybrid_ds':>10} {'full_ds':>8}")
    for r in results:
        if "error" in r:
            print(f"{r['job_id']:>7}  ERROR: {r['error']}")
            continue
        print(f"{r['job_id']:>7} {r['gemini_score']!s:>7} {r['hybrid_deepseek_score']!s:>10} {r['full_deepseek_score']!s:>8}")
    print(f"\n详细结果（含 Step2 提取对比）已写入: {out_path}")


if __name__ == "__main__":
    main()
