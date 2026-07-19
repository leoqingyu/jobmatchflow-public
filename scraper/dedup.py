import hashlib
import re
import unicodedata

from bs4 import BeautifulSoup

from scraper.base import RawJobData


def normalize_jd_text(text: str | None) -> str:
    """将 JD 清洗成适合做精确指纹的稳定文本。"""
    if not text:
        return ""
    plain = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    plain = unicodedata.normalize("NFKC", plain).casefold()
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain


def compute_jd_fingerprint(raw: RawJobData) -> str | None:
    """对完整 JD 计算 SHA-256；没有 JD 时返回 None。"""
    normalized = normalize_jd_text(raw.description_raw)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_content_hash(raw: RawJobData) -> str:
    """
    计算岗位内容哈希，用于去重。
    基于：title + company + description_raw 前 500 字
    """
    text = f"{raw.title}|{raw.company or ''}|{(raw.description_raw or '')[:500]}"
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def make_dedup_key(raw: RawJobData) -> tuple[str, str | None]:
    """
    返回 (source, external_job_id) 元组作为去重主键。
    如果 external_job_id 为空，退回到 url 或 content_hash。
    """
    if raw.external_job_id:
        return (raw.source, raw.external_job_id)
    if raw.url:
        return ("url", raw.url)
    return ("hash", compute_content_hash(raw))
