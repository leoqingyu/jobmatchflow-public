"""瑞士 26 州：用于 JobSpy 的 location 文本与半径策略。"""

from __future__ import annotations


def km_to_miles_rounded(km: float) -> int:
    """JobSpy 使用英里半径，取整且至少 1。"""
    return max(1, round(float(km) / 1.609344))


# 州缩写 → JobSpy 易解析的地名（与常见写法一致）
SWISS_CANTONS: dict[str, str] = {
    "ZH": "Zürich",
    "BE": "Bern",
    "LU": "Luzern",
    "UR": "Uri",
    "SZ": "Schwyz",
    "OW": "Obwalden",
    "NW": "Nidwalden",
    "GL": "Glarus",
    "ZG": "Zug",
    "FR": "Fribourg",
    "SO": "Solothurn",
    "BS": "Basel-Stadt",
    "BL": "Basel-Landschaft",
    "SH": "Schaffhausen",
    "AR": "Appenzell Ausserrhoden",
    "AI": "Appenzell Innerrhoden",
    "SG": "St. Gallen",
    "GR": "Graubünden",
    "AG": "Aargau",
    "TG": "Thurgau",
    "TI": "Ticino",
    "VD": "Vaud",
    "VS": "Valais",
    "NE": "Neuchâtel",
    "GE": "Genève",
    "JU": "Jura",
}


# 岗位密度最高的六州：一天 3 时段（tech/finance/cross）单独轮转，见 tasks/scheduler.py
HOT_CANTONS: list[str] = ["ZH", "ZG", "GE", "VD", "BS", "BE"]

# 其余 20 州岗位较少：按语言聚拢分 7 组，每天抓一组，一组一周被抓一次
# datetime.weekday()：0=周一 ... 6=周日
TAIL_CANTON_WEEKDAY_GROUPS: dict[int, list[str]] = {
    0: ["BL", "LU"],
    1: ["AG", "SG"],
    2: ["SO", "SZ", "TG"],
    3: ["SH", "GL", "UR"],
    4: ["OW", "NW", "AR", "AI"],
    5: ["GR", "NE", "JU"],
    6: ["FR", "VS", "TI"],
}


def jobspy_geo_passes(canonical_country: str) -> list[tuple[str, int]]:
    """
    返回 (location, distance_miles)，供 scrape_jobs 每次调用使用。
    - 瑞士：26 州中心名 + \", Switzerland\"，半径 25 km
    - 卢森堡：单点，半径 35 km
    - 其它国家：整国名，半径 50 英里（与 JobSpy 默认一致）
    """
    c = (canonical_country or "").strip()
    if c == "Switzerland":
        mi = km_to_miles_rounded(25)
        return [(f"{name}, Switzerland", mi) for name in SWISS_CANTONS.values()]
    if c == "Luxembourg":
        return [("Luxembourg", km_to_miles_rounded(35))]
    return [(c, 50)]


def jobspy_geo_passes_by_canton(canonical_country: str) -> list[tuple[str | None, str, int]]:
    """
    同 `jobspy_geo_passes`，但额外带上瑞士州代码（与 `core.search_keywords.SWISS_CANTON_LANGS` 一致），
    供按州选语言关键词的抓取模式使用。非瑞士时州代码为 None（按国家取语言关键词）。

    返回 (canton_code_or_None, location, distance_miles)。
    """
    c = (canonical_country or "").strip()
    if c == "Switzerland":
        mi = km_to_miles_rounded(25)
        return [(code, f"{name}, Switzerland", mi) for code, name in SWISS_CANTONS.items()]
    return [(None, loc, dist) for loc, dist in jobspy_geo_passes(c)]
