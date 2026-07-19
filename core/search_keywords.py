"""
关键词爬虫配置（初稿）：按「地区 → 语言组」「语言 × 领域 → 关键词」组织。

设计要点
- 瑞士按语言区分配语言组，避免在苏黎世搜法语、在日内瓦搜德语的无效请求。
- 卢森堡 EN + FR（金融业工作语言以英/法为主）。
- 请求数 = Σ(每个州 × 该州语言组的关键词数)，不做三语叉乘。
- 关键词分 finance / tech / cross 三组，可按需只启用部分。

调优提示（标 ⚠️ 的词召回高但噪音也高，先观察抽查结果再决定去留）
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1) 地区 → 语言组
# ---------------------------------------------------------------------------

# 瑞士 26 州（键与 core/swiss_cantons.SWISS_CANTONS 一致）
SWISS_CANTON_LANGS: dict[str, list[str]] = {
    # 德语区（EN + DE）
    "ZH": ["en", "de"],   # 苏黎世：金融科技主战场，银行 IT / 保险
    "ZG": ["en", "de"],   # 楚格：加密、基金、大宗商品，含金量极高
    "BS": ["en", "de"],   # 巴塞尔：制药为主，但也有金融 IT
    "BL": ["en", "de"],
    "BE": ["en", "de"],   # 伯尔尼：主要德语（Biel 有法语，暂不单列）
    "LU": ["en", "de"],
    "AG": ["en", "de"],
    "SG": ["en", "de"],
    "SO": ["en", "de"],
    "SZ": ["en", "de"],
    "TG": ["en", "de"],
    "SH": ["en", "de"],
    "GL": ["en", "de"],
    "UR": ["en", "de"],
    "OW": ["en", "de"],
    "NW": ["en", "de"],
    "AR": ["en", "de"],
    "AI": ["en", "de"],
    "GR": ["en", "de"],   # 格劳宾登：三语州，但岗位少，按 DE 处理

    # 法语区（EN + FR）
    "GE": ["en", "fr"],   # 日内瓦：私人银行、大宗商品交易、合规岗密度高
    "VD": ["en", "fr"],   # 沃州 / 洛桑：EPFL 周边科技 + 金融
    "NE": ["en", "fr"],
    "JU": ["en", "fr"],

    # 双语州（EN + DE + FR，别赌单边）
    "FR": ["en", "de", "fr"],   # 弗里堡
    "VS": ["en", "de", "fr"],   # 瓦莱

    # 意语区（岗位少，只用 EN，ROI 低；也可直接跳过）
    "TI": ["en"],               # 提契诺 / 卢加诺：少量私人银行
}

# 国家级（非瑞士）
COUNTRY_LANGS: dict[str, list[str]] = {
    "Luxembourg": ["en", "fr"],
    # 以后扩地区时在这里加，例如：
    # "Germany": ["en", "de"],
    # "Netherlands": ["en"],
}


# ---------------------------------------------------------------------------
# 2) 语言 × 领域 → 关键词
# ---------------------------------------------------------------------------

KEYWORDS: dict[str, dict[str, list[str]]] = {
    # ---------------- 技术 ----------------
    "tech": {
        "en": [
            "software engineer",
            "software developer",
            "backend developer",
            "full stack developer",
            "data engineer",
            "data scientist",
            "machine learning engineer",
            "ai engineer",
            "data analyst",          # ⚠️ 噪音中等，会带回一些纯业务岗
        ],
        "de": [
            "Softwareentwickler",

        ],
        "fr": [
            "développeur",           # 建议同时试无重音变体 "developpeur"

        ],
    },

    # ---------------- 金融 / 合规（卢森堡的深水区） ----------------
    "finance": {
        "en": [
            "compliance officer",
            "AML",
            "KYC",
            "anti money laundering",
            "risk analyst",
            "regulatory reporting",
            "fund accountant",
            "financial analyst",
            "internal audit",
            "financial controller",
            "consultant",   
            "asset manager"         # ⚠️ 噪音很高，只在卢森堡试，不行就删
        ],
        "de": [
            "Risikoanalyst",

        ],
        "fr": [
            "conformité",            # 同时试 "conformite"
        ],
    },

    # ---------------- 金融 × 计算机 交叉（你的核心命中区） ----------------
    "cross": {
        "en": [
            "quantitative analyst",
            "quant developer",
            "fintech",
            "regtech",
            "financial software engineer",
            "trading systems developer",
            "risk data analyst",
            "financial data engineer",
            "blockchain developer",   # 楚格加密圈
            "core banking",
            "digital banking",
        ],
        "de": [
            "Quantitative Analyst",
        ],
        "fr": [
            "analyste quantitatif",
        ],
    },
}


# ---------------------------------------------------------------------------
# 3) 组装：按州 / 国家取实际要跑的关键词列表
# ---------------------------------------------------------------------------

# 可在 .env / settings 里开关领域组
ENABLED_DOMAINS: list[str] = ["tech", "finance", "cross"]


def keywords_for_langs(
    langs: list[str],
    domains: list[str] | None = None,
) -> list[str]:
    """给定语言组，返回去重后的关键词列表（保序）。"""
    use_domains = domains or ENABLED_DOMAINS
    seen: set[str] = set()
    out: list[str] = []
    for domain in use_domains:
        by_lang = KEYWORDS.get(domain, {})
        for lang in langs:
            for kw in by_lang.get(lang, []):
                k = kw.strip()
                if k and k.lower() not in seen:
                    seen.add(k.lower())
                    out.append(k)
    return out


def keywords_for_swiss_canton(canton_code: str, domains: list[str] | None = None) -> list[str]:
    langs = SWISS_CANTON_LANGS.get(canton_code.upper(), ["en"])
    return keywords_for_langs(langs, domains)


def keywords_for_country(country: str, domains: list[str] | None = None) -> list[str]:
    langs = COUNTRY_LANGS.get(country.strip(), ["en"])
    return keywords_for_langs(langs, domains)


def estimate_request_count(domains: list[str] | None = None) -> dict[str, int]:
    """
    粗估每轮抓取的请求数（每个 州×关键词 = 一次 scrape_jobs 调用）。
    用来在加词之前先看会不会把站点抓爆。
    """
    ch = sum(
        len(keywords_for_swiss_canton(code, domains))
        for code in SWISS_CANTON_LANGS
    )
    lu = len(keywords_for_country("Luxembourg", domains))
    return {"switzerland_calls": ch, "luxembourg_calls": lu, "total": ch + lu}


if __name__ == "__main__":
    for d in (["tech"], ["finance"], ["cross"], ENABLED_DOMAINS):
        print(d, estimate_request_count(d))
