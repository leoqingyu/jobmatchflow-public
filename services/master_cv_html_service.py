"""根据简历库 PDF 抽取文本 + CV HTML 模板，生成 Master CV（HTML）供评分与展示。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ai.providers.gemini_model import GeminiModelClient
from core.logger import get_logger
from core.pdf_text import extract_text_from_pdf_path
from services.profile_service import ProfileService

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _ROOT / "ai" / "prompts" / "master_cv_from_pdfs_v1.txt"
_TEMPLATE_PATH = _ROOT / "renderer" / "templates" / "cv_templet.html"

_MAX_CHARS_PER_SOURCE = 14_000


def _load_instruction() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return "Build one HTML resume from the sources using the reference template."


def _load_template_reference() -> str:
    if not _TEMPLATE_PATH.is_file():
        raise RuntimeError(f"CV 模板缺失: {_TEMPLATE_PATH}")
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _pdf_slot_plain_body(cv: UserMasterCV) -> str:
    fp = (cv.source_file_path or "").strip()
    if fp and Path(fp).is_file() and fp.lower().endswith(".pdf"):
        try:
            return extract_text_from_pdf_path(fp)
        except Exception as e:
            logger.warning("PDF 重新抽取失败 cv_id=%s: %s", cv.id, e)
    return (cv.cv_markdown or "").strip()


def build_sources_user_message(cvs: list[UserMasterCV]) -> str:
    parts: list[str] = []
    for cv in cvs:
        body = _pdf_slot_plain_body(cv)
        if not body:
            continue
        if len(body) > _MAX_CHARS_PER_SOURCE:
            body = body[:_MAX_CHARS_PER_SOURCE] + "\n\n[... truncated ...]"
        parts.append(f"===== PDF_RESUME id={cv.id} name={cv.cv_name!r} =====\n\n{body}\n")
    return "\n".join(parts).strip()


def regenerate_master_cv_html_from_resume_pdfs(db: Session, user_id: int) -> str:
    """
    用流水线默认 Gemini：根据当前用户简历库（优先 PDF 路径抽取）+ cv_templet.html 参考，
    生成一份 Master CV HTML，写入 user_profiles.cv_material_library_html。
    """
    cvs = ProfileService.list_master_cvs(db, user_id)
    if not cvs:
        raise ValueError("请先在简历库上传至少一份 PDF")

    user_msg = build_sources_user_message(cvs)
    if not user_msg:
        raise ValueError("未能从已存简历中提取文本；请确认上传的是可选中文字的 PDF（非纯扫描件或需 OCR）")

    template_ref = _load_template_reference()
    instruction = _load_instruction()
    system_prompt = f"{instruction}\n\n--- REFERENCE_TEMPLATE START ---\n{template_ref}\n--- REFERENCE_TEMPLATE END ---\n"

    llm = GeminiModelClient()
    raw = llm.complete_text(
        task_name="master_cv_from_pdfs",
        user_prompt=user_msg,
        system_prompt=system_prompt,
    )
    html = (raw or "").strip()
    if not html:
        raise ValueError("模型未返回 Master CV HTML")
    if "```" in html[:80]:
        lines = html.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        html = "\n".join(lines).strip()
    if "<html" not in html.lower() and "<!doctype" not in html.lower():
        html = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/></head><body>\n"
            f"{html}\n</body></html>"
        )

    prof = ProfileService(db).ensure_profile_row(user_id)
    prof.cv_material_library_html = html
    prof.cv_material_library_updated_at = datetime.now(timezone.utc)
    db.flush()
    logger.info(
        "已更新 Master CV HTML user_id=%s chars=%s model=%s",
        user_id,
        len(html),
        llm.model_name,
    )
    return html
