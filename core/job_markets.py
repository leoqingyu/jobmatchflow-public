"""求职国家：与 Settings 多选、爬虫 country 参数一致（存库为英文标准名）。"""

from __future__ import annotations

# 展示顺序与文案（中文标签 → 存库 / JobSpy 用英文名）
JOB_MARKET_OPTIONS_ZH_EN: list[tuple[str, str]] = [
    ("德国", "Germany"),
    ("卢森堡", "Luxembourg"),
    ("英国", "United Kingdom"),
    ("荷兰", "Netherlands"),
    ("瑞士", "Switzerland"),
    ("法国", "France"),
]

ALLOWED_JOB_MARKET_CODES: frozenset[str] = frozenset({en for _, en in JOB_MARKET_OPTIONS_ZH_EN})

ZH_LABEL_BY_CODE: dict[str, str] = {en: zh for zh, en in JOB_MARKET_OPTIONS_ZH_EN}

# python-jobspy 的 scrape_jobs(country_indeed=...) 参数（小写关键字，与 jobspy.model.Country 一致；
# 库要求该参数始终传入，与实际抓取的站点无关）
_MARKET_TO_JOBSPY_COUNTRY: dict[str, str] = {
    "Germany": "germany",
    "Luxembourg": "luxembourg",
    "United Kingdom": "uk",
    "Netherlands": "netherlands",
    "Switzerland": "switzerland",
    "France": "france",
}


def sanitize_user_countries(raw: list[str] | None) -> list[str]:
    """只保留允许列表中的国家，去重、保序。"""
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for x in raw:
        s = str(x).strip()
        if s in ALLOWED_JOB_MARKET_CODES and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def filter_scrape_countries(requested: list[str] | None) -> list[str]:
    """爬虫侧：与用户配置一致的可抓取国家列表（空则不调 JobSpy）。"""
    return sanitize_user_countries(requested)


def jobspy_country_param(canonical_market: str) -> str:
    """canonical 英文名 → scrape_jobs(country_indeed=...)。"""
    c = (canonical_market or "").strip()
    return _MARKET_TO_JOBSPY_COUNTRY.get(c, c.lower().replace(" ", "_"))
