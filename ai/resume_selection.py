"""
简历选材：纯代码算优先级、排序、卡预算，LLM 完全不参与（跟 ai/scoring_rules.py 一个道理）。

优先级 = 相关性（从已有打分结果 UserJobScore.requirement_matches 推算，不新增 LLM 调用）
        × 含金量权重（用户标注的 tier 常量）
        × 历史偏好修正（见 services/preference_service.py）
排序/预算/超额取舍全在这个文件里决定，改写阶段（ai/resume_rewrite.py）只管把选中的
经历改写成 bullet，不参与"选不选""留几条"这类决策。
"""

from __future__ import annotations

from core.skill_aliases import normalize_skill_label

# 简历 Experience 条数目标（内容要求，不是渲染排版预算，故意不放在 renderer/docx_render.py）：
# 3-4 条——太少显得单薄，太多稀释重点。MAX_EXPERIENCE_ITEMS 是 select_experience_units 的硬
# 上限；MIN_EXPERIENCE_ITEMS 只是目标下限，经历库本身不够 3 条时没法强凑，调用方按最大努力处理。
MIN_EXPERIENCE_ITEMS = 3
MAX_EXPERIENCE_ITEMS = 4

TIER_WEIGHT: dict[str, float] = {"flagship": 3.0, "solid": 1.5, "filler": 0.5}
_DEFAULT_TIER_WEIGHT = TIER_WEIGHT["solid"]

# 旗舰经历即使零证据引用也不清零——"只要沾边就必须放进去"不是靠调这个数值去猜排名猜出来的，
# 是靠 select_experience_units 里的预留席位机制字面实现；这个下限只是防止乘法链路里出现
# 硬 0 把它彻底排除出候选池，跟 ai/scoring_rules.py 硬性条件"min() 封顶不归零"是同一种手法。
RELEVANCE_FLOOR: dict[str, float] = {"flagship": 0.15, "solid": 0.05, "filler": 0.0}
_DEFAULT_RELEVANCE_FLOOR = RELEVANCE_FLOOR["solid"]

# 历史偏好修正夹在这个区间内，防止一次误操作（手滑删了一条经历）把它彻底打死
PREFERENCE_ADJUSTMENT_MIN, PREFERENCE_ADJUSTMENT_MAX = 0.5, 1.5


def _evidence_unit_ids(evidence_ids: list | None) -> set[int]:
    """evidence_ids 里形如 'exp_<id>' 的项解析出经历单元 id；引用 fact atom 的项忽略。"""
    out: set[int] = set()
    for eid in evidence_ids or []:
        if isinstance(eid, str) and eid.startswith("exp_"):
            try:
                out.add(int(eid[len("exp_"):]))
            except ValueError:
                continue
    return out


def compute_relevance(unit_id: int, requirement_matches: list[dict]) -> float:
    """
    该经历对这个 JD 的相关性：遍历打分 Step 3 已经产出的 requirement_matches，把引用了这条
    经历为证据的每条 requirement 的 weight×match_value 加总，除以全部 requirement 的 Σweight
    （跟 ai.scoring_rules.compute_final_score 用同一个分母，量纲可比）。不新增 LLM 调用——
    直接复用打分阶段已经算好的信号。
    """
    sum_weight = 0.0
    sum_weighted_value = 0.0
    for m in requirement_matches or []:
        weight = float(m.get("weight") or 0)
        sum_weight += weight
        if unit_id in _evidence_unit_ids(m.get("evidence_ids")):
            sum_weighted_value += weight * float(m.get("match_value") or 0)
    if sum_weight <= 0:
        return 0.0
    return sum_weighted_value / sum_weight


def priority(
    unit_id: int,
    tier: str | None,
    requirement_matches: list[dict],
    preference_adjustment: float = 1.0,
) -> float:
    relevance = compute_relevance(unit_id, requirement_matches)
    floor = RELEVANCE_FLOOR.get(tier, _DEFAULT_RELEVANCE_FLOOR)
    effective_relevance = max(relevance, floor)
    tier_weight = TIER_WEIGHT.get(tier, _DEFAULT_TIER_WEIGHT)
    adj = min(max(preference_adjustment, PREFERENCE_ADJUSTMENT_MIN), PREFERENCE_ADJUSTMENT_MAX)
    return effective_relevance * tier_weight * adj


def select_experience_units(
    units: list[dict],
    requirement_matches: list[dict],
    preference_adjustments: dict[int, float],
    max_items: int,
) -> list[dict]:
    """
    units: [{"id": int, "tier": "flagship"|"solid"|"filler"|None, ...其余字段透传}, ...]
    preference_adjustments: {unit_id: 乘数}，缺失的按 1.0（无历史信号，不调整）。
    返回选中的经历（原字段 + 附加 "priority"，供前端展示"为什么选了这几段"），按 priority 降序。

    旗舰预留席位：全部 tier=="flagship" 的经历里按 priority 取前 min(flagship 数量, max_items)
    条直接预定席位，不用跟其它经历竞争排名——这是"只要沾边就必须放进去"的字面实现。
    超预算时砍优先级最低的整条经历，不在这里压缩单条经历的内容（压缩是改写阶段的字数预算的事，
    "放不放得下"是选材阶段的事，两者不要混在一起）。
    """
    if max_items <= 0 or not units:
        return []

    scored = []
    for u in units:
        uid = u["id"]
        adj = preference_adjustments.get(uid, 1.0)
        p = priority(uid, u.get("tier"), requirement_matches, adj)
        scored.append({**u, "priority": p})
    scored.sort(key=lambda x: x["priority"], reverse=True)

    flagship = [u for u in scored if u.get("tier") == "flagship"]
    reserved = flagship[: min(len(flagship), max_items)]
    reserved_ids = {u["id"] for u in reserved}

    remaining_slots = max(max_items - len(reserved), 0)
    fill = [u for u in scored if u["id"] not in reserved_ids][:remaining_slots]

    selected = reserved + fill
    selected.sort(key=lambda x: x["priority"], reverse=True)
    return selected


def order_for_display(selected_units: list[dict]) -> list[dict]:
    """
    简历展示顺序跟"按优先级选材"是两回事——选完之后按简历惯例重排成倒序时间线（最近的在前），
    没有 start_date 的经历（如课设/竞赛，日期不明确）排在最后。
    """
    dated = [u for u in selected_units if u.get("start_date") is not None]
    undated = [u for u in selected_units if u.get("start_date") is None]
    dated.sort(key=lambda u: u["start_date"], reverse=True)
    return dated + undated


def select_skills(
    selected_units: list[dict],
    atoms: list[dict],
    requirement_matches: list[dict],
    max_skills: int,
) -> list[str]:
    """
    技能区 = 已选中经历里出现过的 technologies（过 skill_aliases 归一化去重）∪ 被某条
    importance=="must" 的 requirement 引用为证据的 candidate_facts 技能 atom（即使来源经历
    没被选中，must-have 相关的技能也不能丢）。不新增 LLM 调用，技能天然跟着经历选中结果走，
    不单独给技能过一遍含金量标注。
    """
    seen: dict[str, int] = {}
    order: list[str] = []

    def _add(label: str) -> None:
        norm = normalize_skill_label(label)
        if not norm:
            return
        if norm not in seen:
            seen[norm] = 0
            order.append(norm)
        seen[norm] += 1

    for u in selected_units:
        for t in u.get("technologies") or []:
            _add(t)

    must_atom_ids: set[str] = set()
    for m in requirement_matches or []:
        if m.get("importance") != "must":
            continue
        for eid in m.get("evidence_ids") or []:
            if isinstance(eid, str) and not eid.startswith("exp_"):
                must_atom_ids.add(eid)

    atoms_by_id = {a.get("id"): a for a in atoms or []}
    for aid in must_atom_ids:
        a = atoms_by_id.get(aid)
        if a and a.get("type") == "skill":
            _add(a.get("label") or "")

    order.sort(key=lambda label: seen[label], reverse=True)
    return order[:max_skills] if max_skills > 0 else order
