"""
Step 4 — 程序算分：LLM 完全不参与。调权重/阈值只改这个文件，不用碰 prompt。

公式：
    weighted_score = Σ(weight_i × MATCH_VALUES[match_level_i]) / Σ(weight_i) × 100
    final_score    = round(clamp(weighted_score, 0, 100))

硬性条件叠加（min() 封顶，不归零）：
    requirement.hard_constraint 非空（只可能是 language/work_authorization/certification）
    且该条 match_level 被判为 "none" → final_score = min(final_score, HARD_CONSTRAINT_CAP)。
硬性条件条目仍然正常参与上面的加权求和，封顶是叠加规则，不是替代（不从 Σ 里剔除）。
年限类 requirement（category=="experience"）永远不参与这个封顶——年限差距该体现在
match_level 本身（Step 3 由模型基于程序给的时间事实判断），不是程序再加一道惩罚。

类目权重乘数（叠加在 must/nice 权重之上，不是替代）：
    language ×1.5（语言项在加权平均里分量加大——"当地语言占比要高，要给惩罚分"）、
    skill ×0.6（单项技能对总分影响调小——候选人经历库不一定填全所有技术栈，交给 Step 3
    prompt 里新增的"可基于专业/经验推断"来兜底，而不是逐项硬扣分），其余类目 ×1.0。
    跟上面的硬性条件封顶是两套独立、叠加的机制：language 项既参与这里的权重乘数，
    命中封顶条件时又单独触发 70 分封顶，两者都算，不是二选一。

资历错配封顶（叠加，两档：SENIORITY_MISMATCH_CAP=50 硬封顶，比硬性条件的 70 更狠；
    SENIORITY_SOFT_MISMATCH_CAP=70 软封顶，跟硬性条件同一档）：
    job_seniority（Step 2 产出的岗位整体资历判断，junior/mid/senior，识别"许愿式"要求——
    不是机械取某条 requirement 里最大的年限数字，而是整体判断这个岗位实际是不是 senior）
    与候选人 total_years_experience 明显不在一个量级时触发：
    - junior 岗位：候选人 <=3 年完全没问题；4 年软封顶（还值得看看，只是别自动生成材料）；
      >=5 年硬封顶（经验远超这个岗位定位，大概率不会考虑，跟 REVIEW 都够不上）。
    - senior 岗位：候选人 <3 年硬封顶，没有软档——资历不够没有"差一点也行"这一说；
      经验只会越多对 senior 岗位越合适，所以候选人这边不设上限。
    - mid 永不触发（年限细粒度差异交给 Step 3 逐项 "experience" 类目的模型判断，不额外惩罚）。
    候选人年限未知（total_years_experience 为 None）时不判定，避免误伤缺数据的用户。
    命中多个封顶时用 min()，自然取更狠的那个，不需要判断谁优先。

Step 5（可选，叠加在最后）—— 主观偏好附加分：用户在 Experience 页填了 scoring_preferences_text
    和/或目标岗位标题时，才会调 LLM（ai/scoring.py::compute_preference_bonus）判断这个岗位
    跟这些主观偏好的契合度，最多 +10 分，见 apply_preference_bonus。这不是新的算分公式，
    是纯加法：分数已经被硬性条件/资历错配封顶过的岗位，附加分不能把它顶过封顶线——
    否则一个语言硬性条件没过、封顶到 70 的岗位，加 10 分变 80 就跨进 generate 了，
    附加分的初衷是锦上添花，不是绕过封顶。

Step 0（最前置，跟上面的分数公式无关）—— 实习/全职硬过滤：用户在 Experience 页选的
    employment_type_preference（core.constants.EmploymentTypePreference：internship_only /
    full_time_only / both）与岗位的 employment_type（Step 2 顺带产出，见 Job.employment_type）
    明显不符时，直接判定 mismatch——调用方（services/scoring_service.py）命中就把这条岗位
    落成 discard 并跳过 Step 3/4/5，连逐项匹配的 LLM 调用都不用跑，是全流程里最早生效的
    过滤，不是分数封顶。preference 为 "both"（默认）、employment_type 未知（岗位还没跑过
    Step 2，或跑过 Step 2 但那时还没有这个字段），或 employment_type == "graduate_program"
    （两种偏好都不硬拦，见 employment_type_mismatch）时都不判定，避免误伤。
"""

from __future__ import annotations

from core.config import settings
from core.constants import ScoringDecision

MATCH_VALUES: dict[str, float] = {
    "exceeds": 1.00,
    "full": 0.90,
    "strong": 0.75,
    "partial": 0.50,
    "weak": 0.25,
    "none": 0.00,
}

# must/nice 各自的固定权重常数——按 importance 分层取值，不让 LLM 决定权重
_MUST_WEIGHT = 3
_NICE_WEIGHT = 1

# 类目权重乘数：在 must/nice 基础权重之上再按类目微调，复用同一套加权平均公式，
# 不是新发明一套减分机制。未列出的类目按 1.0（不调整）。
_CATEGORY_WEIGHT_MULTIPLIER: dict[str, float] = {
    "language": 1.5,
    "skill": 0.6,
}
_DEFAULT_CATEGORY_MULTIPLIER = 1.0

_HARD_CONSTRAINT_CAP = 70

_SENIORITY_MISMATCH_CAP = 50
_SENIORITY_SOFT_MISMATCH_CAP = 70
_JUNIOR_CANDIDATE_FINE_MAX_YEARS = 3   # junior 岗位 + 候选人经验 <= 3 年 -> 完全没问题
_JUNIOR_CANDIDATE_SOFT_MAX_YEARS = 4   # junior 岗位 + 候选人经验 == 4 年 -> 软封顶 70
                                        # （> 4 年，即 >= 5 年 -> 硬封顶 50）
_SENIOR_CANDIDATE_MIN_YEARS = 3        # senior 岗位 + 候选人经验 < 3 年 -> 硬封顶 50（无软档）


def employment_type_mismatch(preference: str | None, job_employment_type: str | None) -> bool:
    """
    见文件头 docstring 的 Step 0。preference 未设置/"both"，job_employment_type 未知
    （None），或 job_employment_type == "graduate_program" 时都不判定——graduate program
    这类岗位本来就可能按实习走也可能是全职轨道，两边偏好都不该硬拦，见
    core.constants.EmploymentType 的说明。
    """
    if not preference or preference == "both":
        return False
    if job_employment_type is None or job_employment_type == "graduate_program":
        return False
    if preference == "internship_only":
        return job_employment_type != "internship"
    if preference == "full_time_only":
        return job_employment_type == "internship"
    return False


def _weight_for(importance: str | None, category: str | None) -> float:
    base = _MUST_WEIGHT if importance == "must" else _NICE_WEIGHT
    multiplier = _CATEGORY_WEIGHT_MULTIPLIER.get(category, _DEFAULT_CATEGORY_MULTIPLIER)
    return base * multiplier


def _seniority_mismatch_cap(job_seniority: str | None, candidate_total_years: float | None) -> float | None:
    """
    返回本次命中的封顶值（50 硬 / 70 软），没命中则 None。mid 永不触发；候选人年限未知
    （None）时也不判定，避免误伤缺数据的用户。见文件头 docstring 的具体档位说明。
    """
    if candidate_total_years is None:
        return None
    if job_seniority == "junior":
        if candidate_total_years > _JUNIOR_CANDIDATE_SOFT_MAX_YEARS:
            return _SENIORITY_MISMATCH_CAP
        if candidate_total_years > _JUNIOR_CANDIDATE_FINE_MAX_YEARS:
            return _SENIORITY_SOFT_MISMATCH_CAP
        return None
    if job_seniority == "senior" and candidate_total_years < _SENIOR_CANDIDATE_MIN_YEARS:
        return _SENIORITY_MISMATCH_CAP
    return None


def _decision_for_score(score: float) -> str:
    if score >= settings.score_threshold_generate:
        return ScoringDecision.GENERATE.value
    if score >= settings.score_threshold_review:
        return ScoringDecision.REVIEW.value
    return ScoringDecision.DISCARD.value


def compute_final_score(
    requirements: list[dict],
    matches: list[dict],
    *,
    job_seniority: str | None = None,
    candidate_total_years: float | None = None,
) -> dict:
    """
    requirements：Step 2 输出的原子要求列表（{id, category, text, importance, hard_constraint, ...}）。
    matches：Step 3 输出的逐条判断（{requirement_id, match_level, evidence_ids, reason, confidence}）。
    job_seniority / candidate_total_years：Step 2 产出的岗位整体资历判断 + 候选人
    total_years_experience，用于资历错配封顶（见文件头 docstring），跟 requirements/matches
    无关，只影响最后的封顶步骤。

    按 requirement_id 对齐，不要求 matches 顺序/数量与 requirements 一致——LLM 漏项时按
    match_level="none" 兜底，不让下游因为对不齐而崩。
    """
    match_by_id = {m.get("requirement_id"): m for m in (matches or [])}

    rows: list[dict] = []
    sum_weight = 0.0
    sum_weighted_value = 0.0
    hard_constraints_hit: list = []

    for req in requirements or []:
        rid = req.get("id")
        importance = req.get("importance") or "must"
        category = req.get("category")
        weight = _weight_for(importance, category)
        m = match_by_id.get(rid) or {}
        match_level = m.get("match_level") if m.get("match_level") in MATCH_VALUES else "none"
        match_value = MATCH_VALUES[match_level]

        sum_weight += weight
        sum_weighted_value += weight * match_value

        if req.get("hard_constraint") and match_level == "none":
            hard_constraints_hit.append(rid)

        rows.append(
            {
                "requirement_id": rid,
                "category": req.get("category"),
                "importance": importance,
                "weight": weight,
                "match_level": match_level,
                "match_value": match_value,
                "evidence_ids": m.get("evidence_ids") or [],
                "reason": m.get("reason"),
                "confidence": m.get("confidence"),
            }
        )

    weighted_score = (sum_weighted_value / sum_weight * 100) if sum_weight > 0 else 0.0
    final_score = round(max(0.0, min(100.0, weighted_score)))

    cap_applied = bool(hard_constraints_hit)
    if cap_applied:
        final_score = min(final_score, _HARD_CONSTRAINT_CAP)

    seniority_mismatch_cap = _seniority_mismatch_cap(job_seniority, candidate_total_years)
    if seniority_mismatch_cap is not None:
        final_score = min(final_score, seniority_mismatch_cap)

    return {
        "score": float(final_score),
        "decision": _decision_for_score(final_score),
        "requirement_matches": rows,
        "hard_constraints_hit": hard_constraints_hit,
        "score_breakdown": {
            "weighted_score": round(weighted_score, 2),
            "sum_weight": sum_weight,
            "cap_applied": cap_applied,
            "seniority_mismatch": seniority_mismatch_cap is not None,
            "seniority_mismatch_cap": seniority_mismatch_cap,
            "job_seniority": job_seniority,
            "final_score": float(final_score),
        },
    }


def apply_preference_bonus(breakdown: dict, bonus: int | None, reason: str | None) -> dict:
    """
    Step 5：在 compute_final_score 的输出上叠加主观偏好附加分（0-10，见文件头 docstring）。
    纯函数，不改 breakdown 本身，返回一份新 dict。已经被硬性条件/资历错配封顶过的分数，
    加成后仍然不能突破对应的封顶线——两个封顶各自是"这条岗位最高只能到这里"的天花板，
    附加分不改变这件事，只在天花板以内锦上添花。
    """
    bonus = max(0, min(10, int(bonus or 0)))
    score_breakdown = dict(breakdown["score_breakdown"] or {})
    score_before_bonus = breakdown["score"]
    new_score = min(100.0, score_before_bonus + bonus)

    if score_breakdown.get("cap_applied"):
        new_score = min(new_score, _HARD_CONSTRAINT_CAP)
    if score_breakdown.get("seniority_mismatch_cap") is not None:
        new_score = min(new_score, score_breakdown["seniority_mismatch_cap"])
    new_score = float(round(new_score))

    score_breakdown["preference_bonus"] = bonus
    score_breakdown["preference_bonus_reason"] = reason
    score_breakdown["score_before_bonus"] = score_before_bonus

    return {
        **breakdown,
        "score": new_score,
        "decision": _decision_for_score(new_score),
        "score_breakdown": score_breakdown,
    }
