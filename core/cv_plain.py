"""从母简历模型提取供 LLM 使用的纯文本（评分、领英初筛等）。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.models.user_master_cv import UserMasterCV


def strip_html_to_plain(html: str) -> str:
    s = (html or "").strip()
    if not s:
        return ""
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(s, "html.parser").get_text("\n", strip=True)
    except Exception:
        return s


def master_cv_plain_text(cv: "UserMasterCV") -> str:
    """优先上传的 md/txt；其次历史问卷 JSON；再其次旧版 HTML 抽字。"""
    md = (cv.cv_markdown or "").strip()
    if md:
        return md
    if cv.cv_json:
        try:
            return json.dumps(cv.cv_json, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(cv.cv_json)
    html = (cv.cv_master_html or "").strip()
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    except Exception:
        return html
