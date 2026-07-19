"""将 AI 返回的简历 JSON 套入 Jinja HTML 母版。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, BaseLoader, select_autoescape

from core.logger import get_logger

logger = get_logger(__name__)

_TEMPLATE_WITH_PHOTO = Path(__file__).parent / "templates" / "cv_templet.html"
_TEMPLATE_WITHOUT_PHOTO = Path(__file__).parent / "templates" / "cv_templet_without_photo.html"

# 写入 content_json，供预览/PDF/编辑沿用同一版式
CV_TEMPLATE_WITH_PHOTO = "with_photo"
CV_TEMPLATE_WITHOUT_PHOTO = "without_photo"


def _read_template(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def get_cv_template_source(kind: str) -> str:
    """
    kind: cv_render.CV_TEMPLATE_WITH_PHOTO | CV_TEMPLATE_WITHOUT_PHOTO
    """
    if kind == CV_TEMPLATE_WITHOUT_PHOTO:
        text = _read_template(_TEMPLATE_WITHOUT_PHOTO)
        if not text.strip():
            logger.warning("cv_templet_without_photo.html 缺失，回退带照片模板")
            return _read_template(_TEMPLATE_WITH_PHOTO)
        return text
    return _read_template(_TEMPLATE_WITH_PHOTO)


def get_default_cv_template() -> str:
    """兼容旧调用：默认带照片槽位的模板。"""
    return get_cv_template_source(CV_TEMPLATE_WITH_PHOTO)


def resolve_cv_template_from_json(resume_json: dict | None) -> str:
    """根据 content_json['cv_template'] 选择 HTML 母版；缺省按带照片版。"""
    if not resume_json:
        return get_cv_template_source(CV_TEMPLATE_WITH_PHOTO)
    t = (resume_json.get("cv_template") or CV_TEMPLATE_WITH_PHOTO).strip()
    if t == CV_TEMPLATE_WITHOUT_PHOTO:
        return get_cv_template_source(CV_TEMPLATE_WITHOUT_PHOTO)
    return get_cv_template_source(CV_TEMPLATE_WITH_PHOTO)


def uses_photo_layout(resume_json: dict | None) -> bool:
    return (resume_json or {}).get("cv_template") != CV_TEMPLATE_WITHOUT_PHOTO


def _skill_row_to_template_dict(entry: Any) -> dict[str, str]:
    """技能行：category + items 字符串；LLM 若把 items 写成 dict/list 则压平为可读文本。"""
    if not isinstance(entry, dict):
        return {"category": "", "items": str(entry) if entry is not None else ""}
    cat = entry.get("category", "")
    raw = entry.get("items", "")
    if isinstance(raw, dict):
        parts = [f"{k}: {v}" for k, v in raw.items()]
        items_str = "; ".join(parts)
    elif isinstance(raw, list):
        items_str = ", ".join(str(x) for x in raw)
    else:
        items_str = str(raw) if raw is not None else ""
    return {"category": str(cat), "items": items_str}


def normalize_resume_context(data: dict[str, Any]) -> dict[str, Any]:
    """补全模板所需键，避免 Jinja 因缺键报错。"""
    ctx = dict(data)
    ctx.setdefault("full_name", "")
    ctx.setdefault("location", "")
    ctx.setdefault("phone", "")
    ctx.setdefault("email", "")
    ctx.setdefault("visa", "")
    ctx.setdefault("profile_summary", "")
    ctx.setdefault("profile_image_base64", "")
    li = ctx.get("linkedin")
    if li is not None and not isinstance(li, dict):
        ctx["linkedin"] = None
    gh = ctx.get("github")
    if gh is not None and not isinstance(gh, dict):
        ctx["github"] = None
    for key in ("education", "experience", "projects", "publications"):
        v = ctx.get(key)
        if not isinstance(v, list):
            ctx[key] = []
    sk = ctx.get("skills")
    if isinstance(sk, dict):
        # LLM 有时返回 { "Languages": "...", "Programming": "..." } 而非 [{category, items}, ...]
        ctx["skills"] = [
            _skill_row_to_template_dict({"category": key, "items": val})
            for key, val in sk.items()
        ]
    elif not isinstance(sk, list):
        ctx["skills"] = []
    else:
        ctx["skills"] = [_skill_row_to_template_dict(x) for x in sk]
    return ctx


def render_tailored_resume_html(
    template_source: str,
    resume_data: dict[str, Any],
    user_id: int | None = None,
    inject_profile_photo: bool = True,
) -> str:
    """resume_data 为 AI JSON（可含 llm_model / cv_template 及历史遗留字段，会先剥离）。
    inject_profile_photo=False 时不注入证件照（无照片模板或用户明确选无照片版式）。
    """
    payload = {
        k: v
        for k, v in resume_data.items()
        if k not in ("prompt_version", "llm_model", "cv_template")
    }
    ctx = normalize_resume_context(payload)
    if user_id is not None and inject_profile_photo:
        from services.profile_photo import get_profile_photo_base64

        b64 = get_profile_photo_base64(user_id)
        if b64:
            ctx["profile_image_base64"] = b64
    env = Environment(
        loader=BaseLoader(),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.from_string(template_source)
    return tpl.render(**ctx)
