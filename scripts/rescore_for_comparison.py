"""
打分模型对比脚本：对某用户「已经打过分」的岗位，用当前 SCORING_LLM_PROVIDER /
SCORING_MODEL_CHEAP/MID/MATCH 指向的模型重新跑一遍 Step 2-5，新增一行
UserJobScore(llm_model=<新模型>)。

不碰旧记录：UserJobScore 唯一约束是 (user_id, job_id, llm_model)，新模型名不同，
INSERT 不会跟 gemini 那行冲突，也不会覆盖——旧数据已经用
scripts/backup_job_scores.py 导出留档。

不触发生成：不管打分结果是不是 decision=generate，都不会顺带生成简历/求职信
（对比测试不需要这个副作用/花费），见 JobScoringService._score_single_job 的
trigger_generation=False。

不重新走 Step 0/1（用工类型硬过滤 / 方向向量初筛）——既然这批岗位已经通过了
Step 0/1 拿到过一次打分，说明它们不该被这两道硬性过滤拦下，重新跑一遍纯属浪费一次
（更便宜的）LLM 调用；只对比 Step 2（JD 结构化，job 级缓存，多数情况直接命中缓存不
重新调用）+ Step 3/5（逐项匹配 + 偏好加分，这才是真正要对比的模型能力）。

跑法：
  python3 scripts/rescore_for_comparison.py --user-id 3 --limit 50
  python3 scripts/rescore_for_comparison.py --user-id 3 --job-ids 101,102,103
  python3 scripts/rescore_for_comparison.py --user-id 3 --limit 50 --delay-sec 5  # 限流严重时调大
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings
from core.logger import get_logger
from db.models import Job, UserJobScore
from db.session import SessionLocal
from services.scoring_service import JobScoringService

logger = get_logger(__name__)


def _select_jobs(db, user_id: int, limit: int | None, job_ids: list[int] | None) -> list[Job]:
    if job_ids:
        return db.query(Job).filter(Job.id.in_(job_ids)).all()
    scored_job_ids_query = (
        db.query(UserJobScore.job_id, UserJobScore.created_at)
        .filter(UserJobScore.user_id == user_id)
        .order_by(UserJobScore.created_at.desc())
    )
    seen: list[int] = []
    for job_id, _ in scored_job_ids_query:
        if job_id not in seen:
            seen.append(job_id)
        if limit and len(seen) >= limit:
            break
    if not seen:
        return []
    jobs = db.query(Job).filter(Job.id.in_(seen)).all()
    order = {jid: i for i, jid in enumerate(seen)}
    jobs.sort(key=lambda j: order.get(j.id, 0))
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=50, help="按最近打分时间取最近 N 条岗位（--job-ids 未指定时生效）")
    parser.add_argument("--job-ids", type=str, default=None, help="逗号分隔的岗位 id，指定后忽略 --limit")
    parser.add_argument(
        "--delay-sec", type=float, default=2.0,
        help="每条岗位处理完之后的等待秒数，降低请求频率避免撞限流（429 本身在 provider 客户端里已有退避重试，这个是额外的主动降速）",
    )
    args = parser.parse_args()

    job_ids = (
        [int(x) for x in args.job_ids.split(",") if x.strip()] if args.job_ids else None
    )

    db = SessionLocal()
    try:
        jobs = _select_jobs(db, args.user_id, args.limit, job_ids)
        if not jobs:
            print(f"用户 {args.user_id} 没有找到待对比的岗位")
            return

        service = JobScoringService(db)
        print(
            f"cheap={settings.scoring_model_cheap} mid={settings.scoring_model_mid} "
            f"match={settings.scoring_model_match}"
        )
        print(f"待重新打分岗位数: {len(jobs)}")

        candidate_context, candidate_total_years = service._load_candidate_context(args.user_id)
        scoring_preferences_text, target_role_titles = service._load_preference_context(args.user_id)

        ok, failed = 0, 0
        for job in jobs:
            if not job.description_clean:
                logger.warning("job_id=%s 无 JD 正文，跳过", job.id)
                failed += 1
                continue
            try:
                record = service._score_single_job(
                    args.user_id,
                    job,
                    candidate_context,
                    scoring_cache_name=None,
                    candidate_total_years=candidate_total_years,
                    scoring_preferences_text=scoring_preferences_text,
                    target_role_titles=target_role_titles,
                    trigger_generation=False,
                )
                db.commit()
                print(f"job_id={job.id} score={record.score} decision={record.decision}")
                ok += 1
            except Exception as e:
                db.rollback()
                logger.warning("job_id=%s 重新打分失败: %s", job.id, e)
                failed += 1
            if args.delay_sec > 0:
                time.sleep(args.delay_sec)

        print(f"完成: 成功={ok} 失败={failed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
