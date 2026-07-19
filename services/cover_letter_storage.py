"""求职信 DOCX 单文件落盘：每个 user+job 仅一份，更新时删除旧文件。跟 resume_storage.py
结构完全对称，分开成两个文件只是为了文件名不冲突（同一 user+job 下简历和求职信各一份）。"""
from __future__ import annotations

from pathlib import Path

from core.config import settings
from core.exceptions import RenderError
from core.logger import get_logger
from services.resume_storage import remove_file_if_exists

logger = get_logger(__name__)

COVER_LETTER_SUBDIR = "cover_letters"


def _cover_letter_docx_path(user_id: int, job_id: int) -> Path:
    root = Path(settings.local_storage_base_path).resolve()
    d = root / COVER_LETTER_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"u{user_id}_j{job_id}.docx"


def write_cover_letter_docx(docx_bytes: bytes, user_id: int, job_id: int, previous_path: str | None) -> str:
    """写入（覆盖）当前 user+job 的求职信 DOCX。若 previous_path 指向另一路径则先删。"""
    if not docx_bytes:
        raise RenderError("DOCX 内容为空，无法写入")
    target = _cover_letter_docx_path(user_id, job_id)
    if previous_path:
        prev = Path(previous_path).resolve()
        if prev.is_file() and prev != target.resolve():
            remove_file_if_exists(previous_path)
    target.write_bytes(docx_bytes)
    return str(target.resolve())
