"""哪些用户具备打分/生成条件——打分、生成、通知等多处共用。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from db.models import UserCandidateFacts, UserExperienceUnit, UserJobDirection
from services.quota_service import match_quota_remaining


def list_user_ids_ready_for_scoring(db: Session) -> list[int]:
    """
    打分整体（Step 1-4）的准入条件：有 >=1 个 active 求职方向，或经历库有内容
    （candidate_facts.atoms 非空，或 >=1 条 experience_units）；且未达到 admin 设置的
    max_matched_jobs 额度（见 services/quota_service.py）——这一处过滤覆盖所有调用方
    （常驻 worker、批量打分、admin Run Pipeline），不用在每个调用方各自判断一遍。

    不要求"必须有可用 Master CV"——一个还没传过简历、只填了求职方向+手动录入经历的用户
    也能被打分，只是 recommended_cv_id 会是 NULL（见 UserJobScore 模型注释）。
    """
    direction_uids = {
        row[0]
        for row in db.query(UserJobDirection.user_id)
        .filter(UserJobDirection.is_active.is_(True))
        .distinct()
        .all()
    }
    experience_unit_uids = {row[0] for row in db.query(UserExperienceUnit.user_id).distinct().all()}
    facts_uids = {f.user_id for f in db.query(UserCandidateFacts).all() if f.atoms}
    candidate_uids = direction_uids | experience_unit_uids | facts_uids
    return sorted(uid for uid in candidate_uids if match_quota_remaining(db, uid))
