"""打分相关的定时任务入口（tasks/scheduler.py 挂载）。

两条完全独立的节奏：
- Gemini JD 提取（Step 2，job 级，走 Batch API）：不分高峰非高峰，按自己的节奏
  （建议每 30 分钟）提交/轮询，见 services/gemini_jd_batch_service.py。
- DeepSeek 逐项匹配（Step 3/5，见 services/scoring_service.py）：DeepSeek 没有 batch
  接口，唯一的省钱手段是避开北京时间高峰（9-12、14-18，全项目双倍价）；这里在非高峰
  时段跑，每个用户一次性连续处理完，命中 DeepSeek 的上下文缓存（见 run_deepseek_matching_batch
  文档字符串）。
"""

from __future__ import annotations

from core.logger import get_logger

logger = get_logger(__name__)


def run_gemini_jd_batch_cycle() -> None:
    """先查在途 batch 有没有出结果，再把新排队的岗位打包提交——同一个 tick 做两件事，
    不用两个独立 cron 分别管提交和轮询。"""
    from db.session import get_db
    from services.gemini_jd_batch_service import poll_and_apply_batches, submit_pending_batch

    with get_db() as db:
        poll_result = poll_and_apply_batches(db)
        if poll_result["applied"] or poll_result["requeued"]:
            logger.info(
                "Gemini JD batch 轮询: applied=%s requeued=%s still_running=%s",
                poll_result["applied"], poll_result["requeued"], poll_result["still_running"],
            )
        batch_name = submit_pending_batch(db)
        if batch_name:
            logger.info("Gemini JD batch 已提交: %s", batch_name)


def run_deepseek_matching_batch() -> None:
    """非高峰批次：对每个满足打分条件、且未达到 admin 配额的用户（见
    services/user_cv_lookup.py::list_user_ids_ready_for_scoring），一次性打完这个用户
    当前能打的全部岗位（score_new_jobs_for_user 本身就是"打到没有为止"，不是取一条）。

    连续处理同一用户、不与其他用户穿插，是刻意的：DeepSeek 的逐项匹配 prompt 把候选人
    上下文放在前缀、JD 放在后面，前缀不变时才能命中 DeepSeek 的上下文缓存（缓存命中价格
    比未命中低约 50 倍）；穿插处理会让每次请求的前缀都变，缓存永远命中不了。
    """
    from db.session import get_db
    from services.scoring_service import JobScoringService
    from services.user_cv_lookup import list_user_ids_ready_for_scoring

    with get_db() as db:
        user_ids = list_user_ids_ready_for_scoring(db)

    logger.info("DeepSeek 非高峰批次启动，用户数=%s", len(user_ids))
    for uid in user_ids:
        with get_db() as db:
            try:
                result = JobScoringService(db).score_new_jobs_for_user(uid)
                logger.info("user=%s 打分结果: %s", uid, result)
            except Exception as e:
                logger.exception("user=%s DeepSeek 批次打分失败: %s", uid, e)
