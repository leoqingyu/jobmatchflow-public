"""从简历库多份 PDF/文本抽取并集，生成可编辑的 Master CV JSON + 渲染 HTML。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ai.providers.gemini_model import GeminiModelClient
from core.logger import get_logger
from core.pdf_text import extract_text_from_pdf_path
from renderer.cv_render import (
    CV_TEMPLATE_WITHOUT_PHOTO,
    get_cv_template_source,
    normalize_resume_context,
    render_tailored_resume_html,
)
from services.profile_service import ProfileService

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _ROOT / "ai" / "prompts" / "master_cv_union_json_v1.txt"

_MAX_CHARS_PER_SOURCE = 14_000


def _load_instruction() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return "Return one JSON resume that is the union of all sources."


def _pdf_slot_plain_body(cv: Any) -> str:
    fp = (getattr(cv, "source_file_path", None) or "").strip()
    if fp and Path(fp).is_file() and fp.lower().endswith(".pdf"):
        try:
            return extract_text_from_pdf_path(fp)
        except Exception as e:
            logger.warning("PDF 重新抽取失败 cv_id=%s: %s", getattr(cv, "id", "?"), e)
    return (getattr(cv, "cv_markdown", None) or "").strip()


def _sources_user_message(cvs: list[Any]) -> str:
    parts: list[str] = []
    for cv in cvs:
        body = _pdf_slot_plain_body(cv)
        if not body:
            continue
        if len(body) > _MAX_CHARS_PER_SOURCE:
            body = body[:_MAX_CHARS_PER_SOURCE] + "\n\n[... truncated ...]"
        parts.append(f"===== RESUME_SOURCE id={cv.id} name={cv.cv_name!r} =====\n\n{body}\n")
    return "\n".join(parts).strip()


def _coerce_master_cv_json(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("模型返回的 Master CV JSON 不是对象")
    payload = dict(payload)
    payload.setdefault("cv_template", CV_TEMPLATE_WITHOUT_PHOTO)
    payload.setdefault("html_lang", "en")
    return payload


def render_master_cv_html_from_json(master_json: dict[str, Any]) -> str:
    tpl = get_cv_template_source(CV_TEMPLATE_WITHOUT_PHOTO)
    ctx = normalize_resume_context(master_json)
    return render_tailored_resume_html(
        tpl,
        ctx,
        user_id=None,
        inject_profile_photo=False,
    ).strip()


def regenerate_master_cv_union_from_resume_sources(
    db: Session, user_id: int
) -> tuple[dict[str, Any], str]:
    """
    从简历库多份 PDF/文本抽取并集，生成 Master CV JSON + HTML。
    写入 user_profiles.master_cv_json 与 user_profiles.cv_material_library_html。
    """
    cvs = ProfileService.list_master_cvs(db, user_id)
    if not cvs:
        raise ValueError("请先在简历库上传至少一份 PDF")

    user_msg = _sources_user_message(cvs)
    if not user_msg:
        raise ValueError(
            "未能从已存简历中提取文本；请确认上传的是可选中文字的 PDF（非纯扫描件或需 OCR）"
        )

    llm = GeminiModelClient()
    master_json_raw = llm.generate_json(
        task_name="master_cv_union_json",
        user_prompt=user_msg,
        system_prompt=_load_instruction(),
    )
    master_json = _coerce_master_cv_json(master_json_raw)
    html = render_master_cv_html_from_json(master_json)
    if "<html" not in html.lower():
        raise ValueError("渲染 Master CV HTML 失败：输出不是 HTML 文档")

    prof = ProfileService(db).ensure_profile_row(user_id)
    prof.master_cv_json = master_json
    prof.cv_material_library_html = html
    prof.cv_material_library_updated_at = datetime.now(timezone.utc)
    db.flush()

    logger.info(
        "已更新 Master CV(JSON+HTML) user_id=%s json_keys=%s html_chars=%s model=%s",
        user_id,
        len(master_json.keys()),
        len(html),
        llm.model_name,
    )
    return master_json, html


def save_master_cv_json(
    db: Session, user_id: int, master_json: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """保存用户手动编辑后的 Master CV JSON，并重新渲染 HTML。"""
    master_json = _coerce_master_cv_json(master_json)
    html = render_master_cv_html_from_json(master_json)
    prof = ProfileService(db).ensure_profile_row(user_id)
    prof.master_cv_json = master_json
    prof.cv_material_library_html = html
    prof.cv_material_library_updated_at = datetime.now(timezone.utc)
    db.flush()
    return master_json, html

