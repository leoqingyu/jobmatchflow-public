"""
简历选材偏好日志：写入 + 聚合。只增不改的事件流（UserExperiencePreferenceEvent），聚合逻辑
在读的时候现算——ai/resume_selection.py::priority 拿聚合结果做 preference_adjustment。

job_context 是"这类岗位"的粗桶（Job.domain 快照），不是具体 job_id——偏好是有语境的（同一条
经历，投这类岗位想突出，投那类岗位不想提），按粗桶聚合信号才能跨岗位复用。
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.constants import PreferenceAction
from db.models import UserExperiencePreferenceEvent

# 每次 remove/add 事件对乘数的影响幅度；最终生效值会被 ai.resume_selection 夹在
# [PREFERENCE_ADJUSTMENT_MIN, PREFERENCE_ADJUSTMENT_MAX] 区间内，这里只算原始净信号，
# 不在这里设上下限（上下限是选材阶段的职责，避免两处各设一遍导致数值语义不一致）
_ADJUSTMENT_STEP = 0.15


def log_preference_event(
    db: Session,
    *,
    user_id: int,
    item_id: int,
    job_id: int,
    action: str,
    job_context: str | None,
) -> UserExperiencePreferenceEvent:
    event = UserExperiencePreferenceEvent(
        user_id=user_id,
        item_id=item_id,
        job_id=job_id,
        action=action,
        job_context=job_context or "unknown",
    )
    db.add(event)
    db.flush()
    return event


def compute_preference_adjustments(
    db: Session, user_id: int, job_context: str | None
) -> dict[int, float]:
    """返回 {item_id: 原始乘数}（未夹区间）：净信号 = added_by_user 次数 - removed_by_user 次数。"""
    rows = (
        db.query(
            UserExperiencePreferenceEvent.item_id,
            UserExperiencePreferenceEvent.action,
            func.count(UserExperiencePreferenceEvent.id),
        )
        .filter(
            UserExperiencePreferenceEvent.user_id == user_id,
            UserExperiencePreferenceEvent.job_context == (job_context or "unknown"),
            UserExperiencePreferenceEvent.action.in_(
                [PreferenceAction.REMOVED_BY_USER.value, PreferenceAction.ADDED_BY_USER.value]
            ),
        )
        .group_by(UserExperiencePreferenceEvent.item_id, UserExperiencePreferenceEvent.action)
        .all()
    )

    net_counts: dict[int, int] = {}
    for item_id, action, count in rows:
        delta = count if action == PreferenceAction.ADDED_BY_USER.value else -count
        net_counts[item_id] = net_counts.get(item_id, 0) + delta

    return {item_id: 1.0 + net * _ADJUSTMENT_STEP for item_id, net in net_counts.items()}
