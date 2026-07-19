"""
简历改写：结构化输入 -> 结构化输出，只对选材阶段（ai/resume_selection.py）已经选中的经历
改写成 bullet，不整份素材库/JD 全文塞给模型，也不让模型决定选不选、留几条——那是选材阶段
已经做完的事。

bullet 数量、STAR 结构、同一经历内动词不要重复这些都是内容质量要求，直接写进给 LLM 的
prompt（见 ai/prompts/resume_bullet_rewrite_v1.txt / resume_bullet_polish_v1.txt），不是
代码层面的硬校验+重试+截断——这些约束是"对 LLM 的要求"，不是渲染排版的事，之前把
MAX_EXPERIENCE_ITEMS/MAX_BULLETS_PER_EXPERIENCE 定义在 renderer/docx_render.py 里、又靠
字数校验失败就重试/硬截断来兜底，会把 bullet 从"完整的 STAR 句子"腰斩成半句话，内容质量
反而更差，改成这个模块自己管这些内容层面的常量。

代码只负责两件事，不靠隐式约定：
- 要求覆盖率补漏：JD must-have 有没被任何 bullet 认领的，定向补一条（这是"检查是否遗漏"，
  不是"逐句校验对不对"）。
- 结构完整性兜底：LLM 偶尔会漏掉某条经历完全不产出 bullet，降级成不经 LLM 改写、直接拼
  原始字段的模板句，保证每条选中经历在最终简历里不会凭空消失——这不是内容质量校验，只是
  "不能空"的最后防线。
一遍写完之后再调 polish_bullets 让 LLM 自己复查一遍（完整句子、同一经历里动词别撞、可选
加粗），这是"用 LLM 检查 LLM"，不是代码校验。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.logger import get_logger
from core.skill_aliases import normalize_skill_label
from ai.llm_client import LLMClient

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
# bullet 改写用哪个 prompt 由 core.constants.ResumeTailoringMode 决定，见 _load_prompt——
# "honest"（默认）这份完全没动，"jd_aligned" 是新加的独立文件，两者除了这一处 mode 分发，
# 互不影响。
_REWRITE_PROMPT_HONEST = "resume_bullet_rewrite_v1.txt"
_REWRITE_PROMPT_JD_ALIGNED = "resume_bullet_rewrite_jd_aligned_v1.txt"
_SKILLS_CATEGORIZE_PROMPT = "resume_skills_categorize_v1.txt"
_BULLET_POLISH_PROMPT = "resume_bullet_polish_v1.txt"

# 内容质量要求（不是渲染排版预算，故意不放在 renderer/docx_render.py）：每条经历 2-3 条
# bullet；按 priority 排序后最靠前的 FULL_BULLET_TOP_N 条经历必须写满上限（这两条对这个 JD
# 最相关，值得多花笔墨）；经历条数本身的 3-4 条范围见 ai/resume_selection.py。
MIN_BULLETS_PER_EXPERIENCE = 2
MAX_BULLETS_PER_EXPERIENCE = 3
FULL_BULLET_TOP_N = 2

_BULLET_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "unit_id": {"type": "integer"},
                    "text": {"type": "string"},
                    "source_fact_ref": {"type": "string"},
                    "covered_requirements": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["unit_id", "text", "source_fact_ref", "covered_requirements"],
            },
        },
    },
    "required": ["bullets"],
}

_NUMBER_RE = re.compile(r"\d[\d,.]*%?")


def _load_prompt(tailoring_mode: str = "honest") -> str:
    filename = _REWRITE_PROMPT_JD_ALIGNED if tailoring_mode == "jd_aligned" else _REWRITE_PROMPT_HONEST
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _load_skills_prompt() -> str:
    path = _PROMPTS_DIR / _SKILLS_CATEGORIZE_PROMPT
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _load_polish_prompt() -> str:
    path = _PROMPTS_DIR / _BULLET_POLISH_PROMPT
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _fallback_template_bullet(unit: dict, requirement_ids: list[str]) -> dict:
    """不经 LLM 改写，直接用原始字段拼一句——宁可不精致也不编。"""
    action = (unit.get("actions") or "").strip()
    result = (unit.get("results") or "").strip()
    text = action or (unit.get("background") or "").strip()
    if result:
        text = f"{text} ({result})" if text else result
    if not text:
        text = (unit.get("title") or "Experience").strip()
    return {
        "unit_id": unit["id"],
        "text": text,
        "source_fact_ref": "raw_text",
        "covered_requirements": requirement_ids,
    }


def _finalize(bullet: dict) -> dict:
    text = bullet.get("text") or ""
    return {
        "unit_id": bullet["unit_id"],
        "text": text,
        "char_count": len(text),
        "source_fact_ref": bullet.get("source_fact_ref") or "",
        "covered_requirements": bullet.get("covered_requirements") or [],
    }


def _unit_block(u: dict, *, bullet_count_note: str) -> str:
    return (
        f'### unit_id={u["id"]}: "{u.get("title") or ""}" at {u.get("employer") or "(unspecified)"} '
        f"— {bullet_count_note}\n"
        f'Background: {u.get("background") or ""}\n'
        f'Actions: {u.get("actions") or ""}\n'
        f'Technologies: {", ".join(u.get("technologies") or [])}\n'
        f'Results: {u.get("results") or ""}\n'
    )


def _call_rewrite(
    llm: LLMClient,
    units: list[dict],
    must_have_requirements: list[dict],
    *,
    min_bullets_per_unit: int,
    max_bullets_per_unit: int,
    max_chars_per_bullet: int,
    full_bullet_unit_ids: frozenset[int] = frozenset(),
    full_bullet_count: int = MAX_BULLETS_PER_EXPERIENCE,
    tailoring_mode: str = "honest",
    nice_to_have_requirements: list[dict] | None = None,
    job_title: str | None = None,
    job_domain: str | None = None,
    extra_instruction: str | None = None,
) -> dict:
    system_prompt = _load_prompt(tailoring_mode)

    def _count_note(uid: int) -> str:
        if uid in full_bullet_unit_ids:
            return f"this is a top-priority unit for this job — write EXACTLY {full_bullet_count} bullets"
        return f"write between {min_bullets_per_unit} and {max_bullets_per_unit} bullets"

    units_block = "\n\n".join(_unit_block(u, bullet_count_note=_count_note(u["id"])) for u in units)
    must_block = "\n".join(f'{r.get("id")}. {r.get("text", "")}' for r in must_have_requirements) or "(none)"
    nice_block = "\n".join(f'- {r.get("text", "")}' for r in (nice_to_have_requirements or [])) or "(none)"
    user_prompt = f"""## Target job
Title: {job_title or "(unspecified)"}
Domain: {job_domain or "(unspecified)"}

## Selected experience units (rewrite ONLY these, do not add/drop/merge units — required bullet count for each is noted after its title)
{units_block}

## Job's must-have requirements (try to have some bullet cover each, only if genuinely supported)
{must_block}

## Job's nice-to-have requirements (context for tone/emphasis, not required coverage)
{nice_block}

## Length
Aim for roughly {max_chars_per_bullet} characters per bullet. A complete, well-formed STAR
sentence matters more than hitting this exactly — never cut a bullet off mid-thought to fit a
count; write it tighter instead.
{extra_instruction or ""}

Return your answer via the structured output format you've been given."""
    return llm.generate_structured(
        task_name="resume_bullet_rewrite",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        schema=_BULLET_SCHEMA,
    )


def _sanitize_bullets(raw: dict, valid_unit_ids) -> list[dict]:
    valid_ids = set(valid_unit_ids)
    out = []
    for b in raw.get("bullets") or []:
        if not isinstance(b, dict):
            continue
        try:
            uid = int(b.get("unit_id"))
        except (TypeError, ValueError):
            continue
        text = str(b.get("text") or "").strip()
        if uid not in valid_ids or not text:
            continue
        out.append(
            {
                "unit_id": uid,
                "text": text,
                "source_fact_ref": b.get("source_fact_ref") or "",
                "covered_requirements": [str(r) for r in (b.get("covered_requirements") or [])],
            }
        )
    return out


def _cap_per_unit(bullets: list[dict], cap_for: dict[int, int] | int) -> list[dict]:
    """结构性上限，不是内容质量校验：LLM 没照 prompt 里的数量要求执行时，只丢多余的
    bullet（不改文字、不重试），防止一条经历意外堆出一大串——跟"每条经历该有几条 bullet"
    这类内容要求本身应该靠 prompt 达成，这里只是防炸的最后一道栏杆。cap_for 是单个整数时
    对所有经历统一上限；是 {unit_id: 上限} 字典时按经历分别取上限——封顶写满的经历（如
    FULL_BULLET_TOP_N 那几条）跟只给 2 条的经历上限不一样，不能用同一个数字。"""
    out = []
    counts: dict[int, int] = {}
    for b in bullets:
        uid = b["unit_id"]
        limit = cap_for.get(uid, 0) if isinstance(cap_for, dict) else cap_for
        n = counts.get(uid, 0)
        if n >= limit:
            continue
        counts[uid] = n + 1
        out.append(b)
    return out


def _fill_missing_requirement_coverage(
    llm: LLMClient,
    bullets: list[dict],
    units_by_id: dict[int, dict],
    must_have_requirements: list[dict],
    max_chars: int,
    per_unit_cap: dict[int, int],
    tailoring_mode: str = "honest",
) -> list[dict]:
    covered = {rid for b in bullets for rid in (b.get("covered_requirements") or [])}
    missing = [r for r in must_have_requirements if r.get("id") not in covered]
    if not missing or not units_by_id:
        return []

    # 只考虑还有余量的经历——已经封顶的经历不能为了补一条覆盖率又多塞一条，"每条经历最多
    # 几条 bullet" 是更硬的内容要求，覆盖率补漏让位于它。
    counts: dict[int, int] = {}
    for b in bullets:
        counts[b["unit_id"]] = counts.get(b["unit_id"], 0) + 1
    units = [u for uid, u in units_by_id.items() if counts.get(uid, 0) < per_unit_cap.get(uid, 0)]
    if not units:
        return []
    instruction = (
        "The following must-have requirements are not yet covered by any bullet: "
        + ", ".join(f'{r.get("id")} ({r.get("text", "")})' for r in missing)
        + ". Produce ONE additional bullet (for whichever unit_id genuinely has evidence) that "
        "covers one or more of these. If none of the given units has real evidence for a "
        "requirement, omit it rather than inventing evidence — do not force it."
    )
    raw = _call_rewrite(
        llm, units, missing,
        min_bullets_per_unit=1, max_bullets_per_unit=1, max_chars_per_bullet=max_chars,
        tailoring_mode=tailoring_mode,
        extra_instruction=instruction,
    )
    candidates = _sanitize_bullets(raw, {u["id"] for u in units})
    return [_finalize(c) for c in candidates]


def rewrite_experience_bullets(
    llm: LLMClient,
    selected_units: list[dict],
    must_have_requirements: list[dict],
    *,
    min_bullets_per_unit: int = MIN_BULLETS_PER_EXPERIENCE,
    max_bullets_per_unit: int = MAX_BULLETS_PER_EXPERIENCE,
    max_chars_per_bullet: int,
    full_bullet_unit_ids: frozenset[int] = frozenset(),
    full_bullet_count: int = MAX_BULLETS_PER_EXPERIENCE,
    tailoring_mode: str = "honest",
    nice_to_have_requirements: list[dict] | None = None,
    job_title: str | None = None,
    job_domain: str | None = None,
) -> dict:
    """
    selected_units: ai.resume_selection.select_experience_units 选出的经历（原始字段：
    id/title/employer/background/actions/technologies/results/raw_text）。
    must_have_requirements: 该 JD 的 must-have 原子要求（复用 Job.structured_requirements）。
    full_bullet_unit_ids: 必须写满 full_bullet_count 条的经历 id（调用方传 priority 最高的
    FULL_BULLET_TOP_N 条）——其余经历只要求落在 [min_bullets_per_unit, max_bullets_per_unit]
    区间。full_bullet_count 跟 max_bullets_per_unit 是两个独立的数字（不是同一个值复用两次）：
    比如"前两条经历必须写满 3 条、其余经历固定 2 条"这种确定性形状，就是
    full_bullet_count=3、min=max_bullets_per_unit=2 同时生效，见
    services/resume_generation_service.py 的 3-3-2-2 / 3-3-3 形状判断。
    tailoring_mode（core.constants.ResumeTailoringMode）：默认 "honest"，跟改这个函数之前
    完全一样，行为不变；"jd_aligned" 走独立的 prompt 文件（见 _load_prompt），允许更大胆的
    专业推断，数字/公司名这类硬事实两种模式下都一样不能编。
    返回 {"bullets": [{"unit_id","text","char_count","source_fact_ref","covered_requirements"}, ...]}。
    数量/STAR 结构/字数这些内容要求全部写进 prompt 交给 LLM 自己达成（见 _call_rewrite），
    不在这里做字数/编造硬校验+重试+截断——那样只会把完整句子腰斩成半句话。
    """
    if not selected_units:
        return {"bullets": []}

    units_by_id = {u["id"]: u for u in selected_units}
    raw = _call_rewrite(
        llm, selected_units, must_have_requirements,
        min_bullets_per_unit=min_bullets_per_unit,
        max_bullets_per_unit=max_bullets_per_unit,
        max_chars_per_bullet=max_chars_per_bullet,
        full_bullet_unit_ids=full_bullet_unit_ids,
        full_bullet_count=full_bullet_count,
        tailoring_mode=tailoring_mode,
        nice_to_have_requirements=nice_to_have_requirements,
        job_title=job_title, job_domain=job_domain,
    )
    per_unit_cap = {
        uid: (full_bullet_count if uid in full_bullet_unit_ids else max_bullets_per_unit)
        for uid in units_by_id
    }
    bullets = _sanitize_bullets(raw, units_by_id.keys())
    resolved = _cap_per_unit([_finalize(b) for b in bullets], per_unit_cap)
    resolved += _fill_missing_requirement_coverage(
        llm, resolved, units_by_id, must_have_requirements, max_chars_per_bullet, per_unit_cap,
        tailoring_mode=tailoring_mode,
    )
    # 补覆盖率那一步理论上不会再超额（只挑还有余量的经历），这里再兜底裁一次，双保险
    resolved = _cap_per_unit(resolved, per_unit_cap)

    # 保证每条选中经历至少有一条 bullet——LLM 偶尔会漏掉某条经历完全不产出，降级成模板句兜底，
    # 不能让一条经历在最终简历里凭空消失（结构完整性兜底，不是内容质量校验）
    covered_units = {b["unit_id"] for b in resolved}
    for uid, unit in units_by_id.items():
        if uid not in covered_units:
            logger.warning("unit_id=%s 改写结果里完全缺失，降级为模板句兜底", uid)
            resolved.append(_finalize(_fallback_template_bullet(unit, [])))

    return {"bullets": resolved}


# ---------------------------------------------------------------------------
# 4 条经历时的固定形状二选一：3-3-2-2（留全部 4 条，第三、四条各 2 个 bullet）还是
# 3-3-3（砍掉优先级最低的第四条，第三条写满 3 个）。只有这两种形状——不是"渲染完数页数、
# 超了再调整"那种反复试探，两种形状按当前字号/页边距的经验值预算都稳妥落在一页内，选完
# 直接改写+渲染一次就完事，见 services/resume_generation_service.py 的调用处。
# ---------------------------------------------------------------------------

_SHAPE_PROMPT = "resume_experience_shape_v1.txt"

_SHAPE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "keep_fourth": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["keep_fourth", "reason"],
}


def _load_shape_prompt() -> str:
    path = _PROMPTS_DIR / _SHAPE_PROMPT
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _shape_unit_block(u: dict) -> str:
    return (
        f'"{u.get("title") or ""}" at {u.get("employer") or "(unspecified)"}\n'
        f'Background: {u.get("background") or ""}\n'
        f'Actions: {u.get("actions") or ""}\n'
        f'Technologies: {", ".join(u.get("technologies") or [])}\n'
        f'Results: {u.get("results") or ""}\n'
    )


def decide_keep_fourth_experience(
    llm: LLMClient,
    third_unit: dict,
    fourth_unit: dict,
    must_have_requirements: list[dict],
    *,
    nice_to_have_requirements: list[dict] | None = None,
    job_title: str | None = None,
    job_domain: str | None = None,
) -> bool:
    """
    判断第四条经历对这个 JD 是否有独特、值得保留的价值，决定 3-3-2-2（True，留）还是
    3-3-3（False，砍掉第四条、第三条写满）。不产出任何简历文字内容，不涉及事实编造，
    所以不需要防编造校验；调用失败就保守地返回 True（不砍经历），跟原有"能保留就保留"
    的兜底方向一致。
    """
    system_prompt = _load_shape_prompt()
    must_block = "\n".join(f'{r.get("id")}. {r.get("text", "")}' for r in must_have_requirements) or "(none)"
    nice_block = "\n".join(f'- {r.get("text", "")}' for r in (nice_to_have_requirements or [])) or "(none)"
    user_prompt = f"""## Target job
Title: {job_title or "(unspecified)"}
Domain: {job_domain or "(unspecified)"}

## Candidate #3 (third most relevant experience — always kept, bullet count depends on your decision)
{_shape_unit_block(third_unit)}

## Candidate #4 (fourth most relevant experience — the one in question)
{_shape_unit_block(fourth_unit)}

## Job's must-have requirements
{must_block}

## Job's nice-to-have requirements
{nice_block}

Return your answer via the structured output format you've been given."""
    try:
        raw = llm.generate_structured(
            task_name="resume_experience_shape",
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            schema=_SHAPE_SCHEMA,
        )
        return bool(raw.get("keep_fourth", True))
    except Exception as e:
        logger.warning("经历形状（3-3-2-2 vs 3-3-3）判断调用失败，默认保留第四条经历: %s", e)
        return True


# ---------------------------------------------------------------------------
# Skills 分类：把 ai.resume_selection.select_skills 选出的扁平技能池分组打上类目标签，
# 对应简历模板 Skills 区块里 "**Category**: skill, skill, ..." 的多行样式，固定 2-3 组——
# 加上渲染时另外单独加的 Languages 一行，Skills 区块总共 3-4 行（算上语言）。
# 每个分类目标 4-6 个技能（够填满一行、又不会溢出换行）。三层优先级，见
# ai/prompts/resume_skills_categorize_v1.txt 里给 LLM 的具体说明：
#   1) 候选人真实拥有（在 candidate_skills 池里）且跟 JD 相关的——来者不拒，原样保留；
#   2) JD 里明确提到、候选人大概率也会（跟真实技能同域、强关联）但没被显式标注的——LLM 可以补；
#   3) JD 相关但候选人未必直接会的可迁移/相邻技能——只在 1)+2) 还凑不够 4 个时才用。
# _sanitize_skill_categories 严格执行这个边界：真实技能一律保留（超过上限时溢出到 Other，
# 不丢弃），非池内的技能只在这一组不够 min 个时才按需截取到 max 为止，多余的一律丢弃。
# ---------------------------------------------------------------------------

_SKILLS_PER_CATEGORY_MIN = 4
_SKILLS_PER_CATEGORY_MAX = 6

_SKILLS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "skills": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "skills"],
            },
        },
    },
    "required": ["categories"],
}


def _sanitize_skill_categories(raw: dict, candidate_skills: list[str]) -> list[dict]:
    """
    真实技能（出现在 candidate_skills 里，别名归一化后比对）优先保留，但每个分类封顶
    _SKILLS_PER_CATEGORY_MAX——超出的部分不丢弃，退回 leftover 池，最终进 "Other" 分类
    （跟"LLM 分类时漏掉的真实技能"走同一个兜底路径，不能让技能凭空从简历里消失）。
    非池内的技能（LLM 按 JD 补的 tier2/3 候选，见模块顶部常量注释）只有在真实技能数
    < _SKILLS_PER_CATEGORY_MIN 时才按需截取，最多补到刚好 _SKILLS_PER_CATEGORY_MAX 个，
    多出来的直接丢弃——真实技能已经够 4 个的分类完全不碰 LLM 的补充建议。
    """
    valid = {normalize_skill_label(s).lower(): s for s in candidate_skills}
    out: list[dict] = []
    seen_real: set[str] = set()
    for c in raw.get("categories") or []:
        if not isinstance(c, dict):
            continue
        label = str(c.get("label") or "").strip()
        if not label:
            continue
        real_kept: list[str] = []
        padding_candidates: list[str] = []
        for s in c.get("skills") or []:
            s_str = str(s).strip()
            if not s_str:
                continue
            norm = normalize_skill_label(s_str).lower()
            original = valid.get(norm)
            if original:
                if original not in seen_real:
                    real_kept.append(original)
                    seen_real.add(original)
            else:
                padding_candidates.append(s_str)

        if len(real_kept) > _SKILLS_PER_CATEGORY_MAX:
            for overflow in real_kept[_SKILLS_PER_CATEGORY_MAX:]:
                seen_real.discard(overflow)
            real_kept = real_kept[:_SKILLS_PER_CATEGORY_MAX]

        pad_needed = max(0, _SKILLS_PER_CATEGORY_MIN - len(real_kept))
        pad_room = _SKILLS_PER_CATEGORY_MAX - len(real_kept)
        skills = real_kept + padding_candidates[: min(pad_needed, pad_room)]
        if skills:
            out.append({"label": label, "skills": skills})

    leftover = [s for s in candidate_skills if s not in seen_real]
    if leftover:
        out.append({"label": "Other", "skills": leftover})
    return out


def categorize_skills(
    llm: LLMClient,
    candidate_skills: list[str],
    must_have_requirements: list[dict],
    *,
    nice_to_have_requirements: list[dict] | None = None,
    job_title: str | None = None,
    job_domain: str | None = None,
) -> list[dict]:
    """
    candidate_skills：ai.resume_selection.select_skills 已经选好的去重扁平技能池（tier 1：
    候选人真实拥有）。返回 2-3 个 [{"label": str, "skills": [str, ...]}, ...]（加上调用方另外
    单独渲染的 Languages 一行，Skills 区块总共 3-4 行），每个分类目标
    4-6 个技能——真实技能不够时按 tier2（JD 明确提到、候选人大概率也会）> tier3（JD 相关但
    未必直接会的可迁移技能）顺序补足，两者都来自这里传入的 must/nice-to-have requirements，
    不是天马行空瞎编，见 _sanitize_skill_categories 的硬边界。技能池为空时直接返回空列表，
    不调 LLM。
    """
    if not candidate_skills:
        return []
    system_prompt = _load_skills_prompt()
    skills_block = ", ".join(candidate_skills)
    must_block = "\n".join(f'- {r.get("text", "")}' for r in (must_have_requirements or [])) or "(none)"
    nice_block = "\n".join(f'- {r.get("text", "")}' for r in (nice_to_have_requirements or [])) or "(none)"
    user_prompt = f"""## Candidate's real skill pool (tier 1 — their own, use freely)
{skills_block}

## Job context
Title: {job_title or "(unspecified)"}
Domain: {job_domain or "(unspecified)"}
Must-have requirements:
{must_block}
Nice-to-have requirements:
{nice_block}

Return your answer via the structured output format you've been given."""
    raw = llm.generate_structured(
        task_name="resume_skills_categorize",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        schema=_SKILLS_SCHEMA,
    )
    return _sanitize_skill_categories(raw, candidate_skills)


# ---------------------------------------------------------------------------
# Bullet 校对：对已经写好的最终 bullet（改写产出的、或降级模板句）做一遍格式检查——
# 补全成完整句子、修正标点，可选给最多两处关键信息加 **bold** 标记。不是重写，防编造
# 校验对照的是原 bullet 本身（不是 unit 原始字段），比改写阶段的校验更收紧，因为这一步
# 唯一该做的事就是"让已经对的内容更规整"，不该再引入任何新信息。
# ---------------------------------------------------------------------------

_POLISH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["index", "text"],
            },
        },
    },
    "required": ["bullets"],
}

_BOLD_MARK_RE = re.compile(r"\*\*(.+?)\*\*")


def _strip_bold_markers(text: str) -> str:
    return _BOLD_MARK_RE.sub(r"\1", text)


def _polish_hallucination_check(new_text: str, original_text: str) -> list[str]:
    """跟 _check_bullet_hallucination 同思路、同收窄范围：只挡校对时新引入的编造数字，
    对照的是原 bullet 文本本身（校对通道不该引入原 bullet 里完全没有的数字）。"""
    plain_new = _strip_bold_markers(new_text)
    source_numbers = set(_NUMBER_RE.findall(original_text))

    violations: list[str] = []
    for num in _NUMBER_RE.findall(plain_new):
        if num not in source_numbers:
            violations.append(num)
    return violations


def polish_bullets(llm: LLMClient, bullets: list[dict]) -> list[dict]:
    """
    对已经确定要上简历的最终 bullet 列表做一遍格式校对（见文件头说明）。传入/传出都是
    rewrite_experience_bullets 那种 {"unit_id","text",...} 的 dict 列表，顺序和条数不变，
    只有 "text" 字段可能被替换成校对后的版本（可能带 **bold** 标记，见
    renderer/docx_render.py 的 _add_bullet_paragraph 负责解析成加粗 run）。
    任何一条：LLM 没返回、返回空文本、或疑似引入新事实，都保留校对前的原文，不是整批失败。
    调用失败（LLM 报错）直接原样返回全部 bullet，不阻断简历生成。
    """
    if not bullets:
        return bullets
    system_prompt = _load_polish_prompt()
    lines = "\n".join(
        f'{i}. [unit {b.get("unit_id")}] {b.get("text", "")}' for i, b in enumerate(bullets)
    )
    user_prompt = f"""## Bullets to proofread (index. [unit <id>] text — bullets tagged with the same unit id belong to the same experience entry)
{lines}

Return your answer via the structured output format you've been given."""
    try:
        raw = llm.generate_structured(
            task_name="resume_bullet_polish",
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            schema=_POLISH_SCHEMA,
        )
    except Exception as e:
        logger.warning("bullet 校对调用失败，原样保留全部 bullet: %s", e)
        return bullets

    by_index: dict[int, str] = {}
    for item in raw.get("bullets") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            by_index[idx] = text

    out: list[dict] = []
    for i, b in enumerate(bullets):
        original_text = b.get("text") or ""
        candidate = by_index.get(i)
        if not candidate or candidate == original_text:
            out.append(b)
            continue
        violations = _polish_hallucination_check(candidate, original_text)
        if violations:
            logger.warning(
                "unit_id=%s bullet 校对疑似引入新事实(%s)，保留校对前原文",
                b.get("unit_id"), ", ".join(violations[:5]),
            )
            out.append(b)
            continue
        out.append({**b, "text": candidate})
    return out
