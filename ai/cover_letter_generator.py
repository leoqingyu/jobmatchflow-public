"""
求职信生成：一次结构化调用，不像 ai/resume_rewrite.py 那样有重试/防编造校验机制——输入本身
就是已经过简历改写那一层防编造校验的 bullet，信任那一层已经把关过，这里只管组织成信件。
"""

from __future__ import annotations

from pathlib import Path

from ai.llm_client import LLMClient

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_COVER_LETTER_PROMPT = "cover_letter_v1.txt"

_PARAGRAPHS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "paragraphs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["paragraphs"],
}


def _load_prompt() -> str:
    path = _PROMPTS_DIR / _COVER_LETTER_PROMPT
    return path.read_text(encoding="utf-8") if path.exists() else ""


_JD_EXCERPT_MAX_CHARS = 2000


def generate_cover_letter(
    llm: LLMClient,
    *,
    candidate_name: str,
    company: str,
    job_title: str,
    must_have_requirements: list[dict],
    resume_bullets: list[str],
    job_description: str | None = None,
) -> dict:
    """返回 {"paragraphs": [...]}。resume_bullets 应来自已经生成好的定制简历（选材+改写后的
    bullet），不是原始经历库全量——求职信只在简历已经确认的内容基础上组织措辞，不重新判断
    什么跟 JD 相关。job_description 是原始 JD 文本（截断），只用来让开头段落写出对这家公司
    具体、真实的兴趣点（产品/使命/技术方向等），不用于候选人经历事实校验——那仍然只信
    resume_bullets。"""
    system_prompt = _load_prompt()
    req_block = "\n".join(f'- {r.get("text", "")}' for r in must_have_requirements) or "(none stated)"
    bullets_block = "\n".join(f"- {b}" for b in resume_bullets) or "(none)"
    jd_excerpt = (job_description or "").strip()[:_JD_EXCERPT_MAX_CHARS] or "(not provided)"
    user_prompt = f"""## Candidate
{candidate_name or "The candidate"}

## Target
{job_title or "the role"} at {company or "the company"}

## Job description (for company/role context — use only to identify genuine, specific reasons to be interested in this company; do not treat as a source of candidate facts)
{jd_excerpt}

## Job's must-have requirements
{req_block}

## Candidate's selected resume bullets for this application (only source of truth about the candidate)
{bullets_block}

Return your answer via the structured output format you've been given."""
    result = llm.generate_structured(
        task_name="cover_letter",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        schema=_PARAGRAPHS_SCHEMA,
    )
    paragraphs = [str(p).strip() for p in (result.get("paragraphs") or []) if str(p).strip()]
    return {"paragraphs": paragraphs}
