import re
from scraper.base import RawJobData


def clean_description(text: str) -> str:
    """清洗 JD 原始文本：去除多余空白和 HTML 标签"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_country(country: str) -> str:
    """国家名称标准化（与 core.job_markets 英文名一致）。"""
    key = country.lower().strip()
    mapping = {
        "ch": "Switzerland",
        "switzerland": "Switzerland",
        "schweiz": "Switzerland",
        "suisse": "Switzerland",
        "lu": "Luxembourg",
        "luxembourg": "Luxembourg",
        "de": "Germany",
        "germany": "Germany",
        "deutschland": "Germany",
        "fr": "France",
        "france": "France",
        "nl": "Netherlands",
        "netherlands": "Netherlands",
        "holland": "Netherlands",
        "uk": "United Kingdom",
        "gb": "United Kingdom",
        "united kingdom": "United Kingdom",
        "great britain": "United Kingdom",
    }
    return mapping.get(key, country.strip())


def normalize_job(raw: RawJobData) -> RawJobData:
    """对 RawJobData 做标准化处理"""
    raw.title = raw.title.strip() if raw.title else ""
    raw.company = raw.company.strip() if raw.company else None
    raw.location = raw.location.strip() if raw.location else None
    raw.country = normalize_country(raw.country) if raw.country else None
    raw.description_clean = clean_description(raw.description_raw or "")
    return raw
