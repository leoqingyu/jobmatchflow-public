"""
打分 Step 1（方向扩写）/2（JD 原子要求提取）/3（逐项匹配）的 LLM 部分：只负责
"调 LLM + 解析/校验结构"，不算分——算分逻辑在 ai/scoring_rules.py。

四段调用：
- expand_direction：求职方向 -> 扩写文本 + embedding 用的近义词（方向创建时一次性，见
  services/experience_library_service.py）。
- extract_jd_requirements：JD -> 原子要求列表（job 级，调用方负责缓存到 Job.structured_requirements）。
- extract_experience_library_from_text：已上传简历全文 -> 种子事实 atoms + 经历单元（用于
  "从 Master CV 引导抽取"的 bootstrap 接口，一次性，不持续同步）。
- match_requirements_to_candidate(_with_cache)：该岗位全部要求 vs 该用户全部事实+经历单元，
  一次调用打包判断，严格走 JSON Schema 约束（match_level 只能是固定枚举，不允许模型自己拍小数）。

不再有"推荐 Master CV"这一段——生成阶段（简历/求职信）完全由经历库驱动，压根不读
UserJobScore.recommended_cv_id/master_cv_id，调 LLM 去猜"该用哪份上传的 CV 文件"是纯浪费。
见 services/scoring_service.py::_pick_recommended_cv（现在是零 LLM 调用的纯代码兜底）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from core.constants import EmploymentType, JobDomain
from core.logger import get_logger
from ai.llm_client import LLMClient
from ai.scoring_rules import MATCH_VALUES

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_DIRECTION_EXPAND_PROMPT = "direction_expand_v1.txt"
_JD_REQUIREMENTS_PROMPT = "jd_requirements_extract_v1.txt"
_EXPERIENCE_EXTRACT_PROMPT = "experience_library_extract_v1.txt"
_REQUIREMENT_MATCH_PROMPT = "requirement_match_v1.txt"
_PREFERENCE_BONUS_PROMPT = "preference_bonus_v1.txt"

_VALID_CATEGORIES = {
    "skill", "experience", "capability", "education",
    "domain", "language", "work_authorization", "certification",
}
_VALID_IMPORTANCE = {"must", "nice"}
_HARD_CONSTRAINT_CATEGORIES = {"language", "work_authorization", "certification"}
_VALID_OWNERSHIP = {"independent", "participant"}
_VALID_ATOM_TYPES = {
    "skill", "language", "education", "certification", "work_authorization", "industry",
}
_VALID_JOB_DOMAINS = {d.value for d in JobDomain}
_VALID_JOB_SENIORITY = {"junior", "mid", "senior"}
_VALID_EMPLOYMENT_TYPES = {e.value for e in EmploymentType}

_MATCH_RESULT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "reason_summary": {"type": "string"},
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_id": {"type": "string"},
                    "match_level": {"type": "string", "enum": list(MATCH_VALUES.keys())},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["requirement_id", "match_level", "evidence_ids", "reason", "confidence"],
            },
        },
    },
    "required": ["reason_summary", "matches"],
}


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_match_system_prompt() -> str:
    """供显式缓存创建复用，与 match_requirements_to_candidate 使用的 system 文本一致。"""
    return _load_prompt(_REQUIREMENT_MATCH_PROMPT)


# ---------------------------------------------------------------------------
# Step 1：方向扩写
# ---------------------------------------------------------------------------

def expand_direction(llm: LLMClient, title: str, candidate_background: str | None = None) -> dict:
    """
    求职方向标题 -> {"expanded_text_en/de/fr": ..., "related_titles": [...]}。一次性调用，
    调用方负责存。三语版本是因为瑞士岗位大量是德语/法语原文，之前只扩写英文版，多语
    embedding 模型对跨语言语义相似度天然打折，会把语言不同但方向对的岗位误判成不相关
    （见 ai/direction_matching.py::load_active_direction_vectors 现在会把三语向量都拿去跟
    岗位向量比，取最高分——只要有一个语言版本过阈值就算过，不要求三个都过）。
    candidate_background 传入时（技能 + 过往岗位摘要，见 services/scoring_service.py
    ::_load_candidate_background_summary），expanded_text 会用候选人真实技能/经历"接地"，
    而不是 LLM 凭空猜"这个方向通常配什么技能"——同时写成 JD 口吻的一段话（不是关键词
    堆砌），跟真实岗位（title+JD 片段）的 embedding 空间更接近，见 prompt 文件里的说明。
    """
    system_prompt = _load_prompt(_DIRECTION_EXPAND_PROMPT)
    background_block = (
        f"\n\n## Candidate's actual skills/experience (for grounding, not for copying verbatim)\n{candidate_background}"
        if candidate_background
        else ""
    )
    user_prompt = f"""## Job search direction
{title}{background_block}

Return ONLY the JSON object as specified in your instructions."""
    result = llm.generate_json(
        task_name="direction_expand",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
    )
    result.setdefault("expanded_text_en", "")
    result.setdefault("expanded_text_de", "")
    result.setdefault("expanded_text_fr", "")
    result.setdefault("related_titles", [])
    return result


# ---------------------------------------------------------------------------
# Step 2：JD 原子要求提取
# ---------------------------------------------------------------------------

def _normalize_requirements(result: dict) -> dict:
    """兜底：category/importance/hard_constraint 不在允许范围内时回退到安全默认值，id 缺失时补一个。"""
    reqs = result.get("requirements") or []
    out = []
    for i, r in enumerate(reqs):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or f"r{i + 1}")
        category = r.get("category") if r.get("category") in _VALID_CATEGORIES else "skill"
        importance = r.get("importance") if r.get("importance") in _VALID_IMPORTANCE else "must"
        hard_constraint = r.get("hard_constraint")
        if hard_constraint not in _HARD_CONSTRAINT_CATEGORIES or category != hard_constraint:
            hard_constraint = None
        out.append(
            {
                "id": rid,
                "category": category,
                "text": (r.get("text") or "").strip(),
                "importance": importance,
                "hard_constraint": hard_constraint,
                "hard_constraint_detail": r.get("hard_constraint_detail") if hard_constraint else None,
            }
        )
    job_domain = result.get("job_domain")
    if job_domain not in _VALID_JOB_DOMAINS:
        job_domain = JobDomain.OTHER.value
    job_seniority = result.get("job_seniority")
    if job_seniority not in _VALID_JOB_SENIORITY:
        job_seniority = "mid"  # 缺失/越界时默认 mid——mid 不触发 Step4 任何封顶，是安全默认值
    employment_type = result.get("employment_type")
    if employment_type not in _VALID_EMPLOYMENT_TYPES:
        employment_type = "full_time"  # 缺失/越界时默认 full_time——大多数岗位本来就是，且不会
        # 被 internship_only 偏好误纳入、也不会被 full_time_only 偏好误排除
    return {
        "requirements": out,
        "job_domain": job_domain,
        "job_seniority": job_seniority,
        "employment_type": employment_type,
    }


def build_jd_requirements_prompt(jd_text: str) -> tuple[str, str]:
    """(system_prompt, user_prompt)，供同步路径（extract_jd_requirements，下方）和 Gemini
    Batch API 路径（services/gemini_jd_batch_service.py）共用，保证两条路径产出结果一致。"""
    system_prompt = _load_prompt(_JD_REQUIREMENTS_PROMPT)
    user_prompt = f"""## Job Description
{jd_text}

Return ONLY the JSON object as specified in your instructions."""
    return system_prompt, user_prompt


def normalize_jd_requirements(result: dict) -> dict:
    return _normalize_requirements(result)


def extract_jd_requirements(llm: LLMClient, jd_text: str) -> dict:
    """JD 原子要求提取：{"requirements": [{id, category, text, importance, hard_constraint, ...}], "job_domain": ..., "job_seniority": ..., "employment_type": ...}。"""
    system_prompt, user_prompt = build_jd_requirements_prompt(jd_text)
    result = llm.generate_json(
        task_name="jd_requirements_extract",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
    )
    return normalize_jd_requirements(result)


# ---------------------------------------------------------------------------
# 经历库 bootstrap：从已上传简历抽取种子事实 + 经历单元
# ---------------------------------------------------------------------------

def _normalize_experience_library(result: dict) -> dict:
    atoms_out = []
    for a in result.get("facts_atoms") or []:
        if not isinstance(a, dict) or not a.get("label"):
            continue
        atype = a.get("type") if a.get("type") in _VALID_ATOM_TYPES else "skill"
        atoms_out.append({"type": atype, "label": str(a.get("label")).strip(), "detail": a.get("detail") or {}})

    units_out = []
    for u in result.get("experience_units") or []:
        if not isinstance(u, dict):
            continue
        ownership = u.get("ownership") if u.get("ownership") in _VALID_OWNERSHIP else "participant"
        technologies = [t for t in (u.get("technologies") or []) if isinstance(t, str) and t.strip()]
        units_out.append(
            {
                "title": u.get("title"),
                "employer": u.get("employer"),
                "background": u.get("background"),
                "actions": u.get("actions"),
                "technologies": technologies,
                "ownership": ownership,
                "results": u.get("results"),
                "domain": u.get("domain"),
                "start_year": u.get("start_year"),
                "start_month": u.get("start_month"),
                "end_year": u.get("end_year"),
                "end_month": u.get("end_month"),
                "raw_date_text": u.get("raw_date_text"),
                "raw_text": u.get("raw_text"),
            }
        )

    return {
        "total_years_experience": result.get("total_years_experience"),
        "facts_atoms": atoms_out,
        "experience_units": units_out,
    }


def extract_experience_library_from_text(llm: LLMClient, resume_text: str) -> dict:
    """从已上传简历全文抽取种子经历库：{"total_years_experience", "facts_atoms", "experience_units"}。"""
    system_prompt = _load_prompt(_EXPERIENCE_EXTRACT_PROMPT)
    user_prompt = f"""## Candidate resume(s)
{resume_text}

Return ONLY the JSON object as specified in your instructions."""
    result = llm.generate_json(
        task_name="experience_library_extract",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
    )
    return _normalize_experience_library(result)


# ---------------------------------------------------------------------------
# Step 3：逐项匹配
# ---------------------------------------------------------------------------

def duration_months(start_date: date | None, end_date: date | None) -> int | None:
    """程序算好的可核验时间事实：喂给模型，模型只管判断这构成"几年经验"，不自己脑补日期。"""
    if not start_date:
        return None
    end = end_date or date.today()
    return max(0, (end.year - start_date.year) * 12 + (end.month - start_date.month))


def _atom_lines(atoms: list[dict]) -> str:
    lines = []
    for a in atoms or []:
        detail = a.get("detail") or {}
        detail_str = f" ({', '.join(f'{k}={v}' for k, v in detail.items())})" if detail else ""
        lines.append(f'- id={a.get("id")} type={a.get("type")} label="{a.get("label")}"{detail_str}')
    return "\n".join(lines) if lines else "(none)"


def _education_lines(education_entries: list[dict]) -> str:
    """Basic info 里的 education（master_cv_json.education）是唯一的教育背景来源——
    facts atoms 里的 education 类型不再喂给这里，避免同一件事有两处可编辑、两边可能对不上。"""
    lines = []
    for e in education_entries or []:
        parts = [p for p in (e.get("degree"), e.get("major"), e.get("institution")) if p]
        label = " · ".join(parts) if parts else "(untitled)"
        date_range = e.get("date_range")
        lines.append(f"- {label}{f' ({date_range})' if date_range else ''}")
    return "\n".join(lines) if lines else "(none)"


def _experience_unit_block(u: dict) -> str:
    dur = duration_months(u.get("start_date"), u.get("end_date"))
    dur_str = f"{dur} months" if dur is not None else "unknown duration"
    return (
        f'### Experience unit id=exp_{u.get("id")}: "{u.get("title") or ""}" '
        f'at {u.get("employer") or "(unspecified)"}\n'
        f'Duration: {dur_str} ({u.get("raw_date_text") or ""})\n'
        f'Ownership: {u.get("ownership") or "unspecified"}\n'
        f'Domain: {u.get("domain") or "unspecified"}\n'
        f'Background: {u.get("background") or ""}\n'
        f'Actions: {u.get("actions") or ""}\n'
        f'Technologies mentioned (as written, not normalized): {", ".join(u.get("technologies") or [])}\n'
        f'Results: {u.get("results") or ""}\n'
    )


def build_candidate_context_plain(
    atoms: list[dict],
    total_years_experience: float | None,
    experience_units: list[dict],
    education_entries: list[dict] | None = None,
) -> str:
    """
    Step 3 用的候选人上下文：facts atoms（不含 education，见下）+ education（来自 Basic info/
    master_cv_json，唯一来源）+ experience units 全量（含算好的 duration）。
    量级只有几十条，全喂比向量检索更准、不会漏判，见 ai/prompts/requirement_match_v1.txt。
    """
    non_education_atoms = [a for a in (atoms or []) if a.get("type") != "education"]
    atoms_block = _atom_lines(non_education_atoms)
    education_block = _education_lines(education_entries or [])
    units_block = (
        "\n\n".join(_experience_unit_block(u) for u in experience_units) if experience_units else "(none)"
    )
    total_years_str = str(total_years_experience) if total_years_experience is not None else "unknown"
    return (
        "## Candidate deterministic facts (canonical, normalized — source of truth for skills/languages/etc)\n"
        f"Total years of professional experience (program-estimated): {total_years_str}\n"
        f"{atoms_block}\n\n"
        "## Candidate education (from Basic info, the only education source)\n"
        f"{education_block}\n\n"
        "## Candidate experience units (raw evidence text; technologies here are AS WRITTEN, not "
        "normalized; duration is program-computed from stated dates, treat as verified fact)\n"
        f"{units_block}\n"
    )


def _match_user_prompt(requirements: list[dict]) -> str:
    lines = "\n".join(
        f'{r.get("id")}. [{r.get("category")}/{r.get("importance")}] {r.get("text", "")}'
        for r in requirements
    )
    return f"""## Requirements to judge (one match object per id, ids must match exactly, same set — no more no less)
{lines if lines else "(none)"}

Return your answer via the structured output format you've been given."""


def _normalize_match_result(result: dict | list, requirements: list[dict] | None = None) -> dict:
    """部分 provider（如 GLM/DeepSeek，在 response_format=json_schema 不生效退回纯 prompt JSON
    模式时）不总是遵守「顶层是 {reason_summary, matches} 对象」这个约定，偶尔直接吐一个裸数组当
    matches——这里做兼容，不当成解析失败。

    字段名同理：requirement_id 这个键名只在 JSON Schema 里定义过（见 _MATCH_RESULT_SCHEMA），
    prompt 正文从没提过要叫这个名字——没有 schema 强约束兜底时，不同模型的表现还不一样：
    GLM 会照抄输入 requirement 自己的字段名 "id"；DeepSeek（非思考模式下）观察到的是干脆
    不带任何 id 字段，只靠"跟输入顺序一一对应"（prompt 里确实这么要求的）。这里依次按
    requirement_id -> id -> （数量对得上时）按位置对齐 requirements 兜底识别，不然没有
    schema 约束的 provider 会被当成"每条都没返回"，全部被 compute_final_score 默认成
    match_level=none——不是模型没判断，是我们没认出它的回答。按位置对齐只在返回条数与
    requirements 数量完全一致时才启用，数量对不上说明模型漏项/加项，不能瞎猜谁对应谁。
    """
    if isinstance(result, list):
        result = {"reason_summary": None, "matches": result}
    matches = result.get("matches") or []
    req_ids_in_order = [r.get("id") for r in (requirements or [])]
    positional_ok = bool(requirements) and len(matches) == len(req_ids_in_order)
    out = []
    for i, m in enumerate(matches):
        if not isinstance(m, dict):
            continue
        rid = m.get("requirement_id") or m.get("id")
        if not rid and positional_ok:
            rid = req_ids_in_order[i]
        if not rid:
            continue
        out.append({**m, "requirement_id": rid})
    return {"reason_summary": result.get("reason_summary"), "matches": out}


def match_requirements_to_candidate(
    llm: LLMClient,
    requirements: list[dict],
    candidate_context_plain: str,
) -> dict:
    """非缓存比对：同一次请求携带候选人上下文 + 全部 requirements，schema 强约束输出。"""
    system_prompt = _load_prompt(_REQUIREMENT_MATCH_PROMPT)
    user_prompt = f"""{candidate_context_plain}

---

{_match_user_prompt(requirements)}"""

    result = llm.generate_structured(
        task_name="requirement_match",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        schema=_MATCH_RESULT_SCHEMA,
    )
    return _normalize_match_result(result, requirements)


def match_requirements_to_candidate_with_cache(
    gemini: "GeminiModelClient",
    requirements: list[dict],
    cache_name: str,
) -> dict:
    """使用已创建的显式缓存比对：请求里只含 requirements，候选人上下文与 system prompt 在缓存中。"""
    from ai.providers.gemini_model import GeminiModelClient
    from ai.llm_client import parse_llm_json_output
    from core.exceptions import LLMError

    if not isinstance(gemini, GeminiModelClient):
        raise TypeError("match_requirements_to_candidate_with_cache 仅支持 GeminiModelClient")

    user_prompt = _match_user_prompt(requirements)
    logger.info("LLM call: task=requirement_match_cached model=%s", gemini.model_name)
    try:
        raw = gemini.generate_with_scoring_cache(
            cache_name, user_prompt, schema=_MATCH_RESULT_SCHEMA, task_name="requirement_match_cached"
        )
    except Exception as e:
        raise LLMError(f"LLM 调用失败 task=requirement_match_cached: {e}") from e

    result = parse_llm_json_output(raw, task_name="requirement_match_cached")
    return _normalize_match_result(result)


# ---------------------------------------------------------------------------
# Step 5（可选）：主观偏好附加分 —— 只在用户填了 scoring_preferences_text 或
# 目标岗位标题时才调用，算分本身（Step 4）在 ai/scoring_rules.py::apply_preference_bonus。
# ---------------------------------------------------------------------------

_PREFERENCE_BONUS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "bonus": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["bonus", "reason"],
}


def compute_preference_bonus(
    llm: LLMClient,
    *,
    job_title: str | None,
    job_company: str | None,
    job_domain: str | None,
    job_seniority: str | None,
    reason_summary: str | None,
    scoring_preferences_text: str | None,
    target_role_titles: list[str],
) -> dict:
    """{"bonus": 0-10 整数, "reason": "..."}；bonus 越界/非法时兜底为 0。"""
    system_prompt = _load_prompt(_PREFERENCE_BONUS_PROMPT)
    roles_text = ", ".join(target_role_titles) if target_role_titles else "(none stated)"
    user_prompt = f"""## Job
Title: {job_title or "(unknown)"}
Company: {job_company or "(unknown)"}
Domain: {job_domain or "(unknown)"}
Seniority: {job_seniority or "(unknown)"}
Why this job was already judged a fit: {reason_summary or "(no summary available)"}

## Candidate's stated preferences
Scoring preferences: {scoring_preferences_text or "(none stated)"}
Target roles: {roles_text}

Return ONLY the JSON object as specified in your instructions."""

    result = llm.generate_structured(
        task_name="preference_bonus",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        schema=_PREFERENCE_BONUS_SCHEMA,
    )
    try:
        bonus = int(result.get("bonus") or 0)
    except (TypeError, ValueError):
        bonus = 0
    bonus = max(0, min(10, bonus))
    return {"bonus": bonus, "reason": result.get("reason")}
