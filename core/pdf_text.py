"""从 PDF 抽取纯文本（简历库上传）。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path


def extract_text_from_pdf_bytes(data: bytes) -> str:
    if not data or not data.strip().startswith(b"%PDF"):
        raise ValueError("不是有效的 PDF 文件（缺少 %PDF 文件头）")
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("请安装 pypdf：pip install pypdf") from e

    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("PDF 已加密，无法提取文本") from None

    parts: list[str] = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def extract_text_from_pdf_path(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return extract_text_from_pdf_bytes(p.read_bytes())
