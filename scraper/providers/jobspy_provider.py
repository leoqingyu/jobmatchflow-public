import time
from datetime import datetime
from typing import Optional

from core.logger import get_logger
from core.scrape_params import effective_scrape_limit, random_scrape_delay
from core.exceptions import ScraperError
from core.job_markets import filter_scrape_countries, jobspy_country_param
from core.swiss_cantons import jobspy_geo_passes_by_canton
from core.search_keywords import keywords_for_country, keywords_for_swiss_canton
from scraper.base import BaseScraperProvider, RawJobData

logger = get_logger(__name__)


class JobSpyProvider(BaseScraperProvider):
    """
    基于 python-jobspy 库的抓取器（编排器默认只用 LinkedIn 站点）。
    """

    def __init__(
        self,
        site_names: Optional[list[str]] = None,
        hours_old: Optional[int] = None,
        *,
        broad_keyword: bool = False,
        linkedin_fetch_description: bool = False,
        use_location_keywords: bool = False,
        keyword_domains: Optional[list[str]] = None,
        canton_codes: Optional[list[str]] = None,
    ):
        self.site_names = site_names or ["linkedin"]
        self.hours_old = hours_old  # None 表示不限制发布时间
        # True：不按关键词列表循环，每个国家只抓一次（search_term 为空，由站点展示「最新」类结果）
        self.broad_keyword = broad_keyword
        # True：尝试拉取 LinkedIn 详情（仍可能被站点限制；无 JD 时走 visitor 补全）
        self.linkedin_fetch_description = linkedin_fetch_description
        # True：忽略 fetch_jobs 传入的 keywords，改为按「州/国家 → 语言 → 关键词」
        # （core.search_keywords）逐个地点解析关键词，实现「关键词 + 语言 + 地点」抓取
        self.use_location_keywords = use_location_keywords
        # 传给 core.search_keywords 的领域过滤（tech/finance/cross）；None 用其默认全启用
        self.keyword_domains = keyword_domains
        # 仅瑞士生效：只抓这些州代码（core.swiss_cantons 里的键）；None = 26 州全抓
        self.canton_codes = canton_codes

    @property
    def source_name(self) -> str:
        return "jobspy"

    def fetch_jobs(
        self,
        keywords: list[str],
        countries: list[str],
        limit: Optional[int] = None,
        *,
        broad_keyword: Optional[bool] = None,
        linkedin_fetch_description: Optional[bool] = None,
        use_location_keywords: Optional[bool] = None,
        keyword_domains: Optional[list[str]] = None,
        canton_codes: Optional[list[str]] = None,
    ) -> list[RawJobData]:
        try:
            from jobspy import scrape_jobs  # type: ignore
        except ImportError as e:
            raise ScraperError("python-jobspy 未安装，请运行 pip install python-jobspy") from e

        target_countries = filter_scrape_countries(countries)
        if not target_countries:
            logger.info("JobSpy：有效 countries 为空，跳过抓取")
            return []

        use_broad = self.broad_keyword if broad_keyword is None else broad_keyword
        use_loc_kw = (
            self.use_location_keywords if use_location_keywords is None else use_location_keywords
        )
        domains = keyword_domains if keyword_domains is not None else self.keyword_domains
        cantons = canton_codes if canton_codes is not None else self.canton_codes

        fetch_desc = (
            self.linkedin_fetch_description
            if linkedin_fetch_description is None
            else linkedin_fetch_description
        )

        results: list[RawJobData] = []
        seen_keys: set[tuple[str, str]] = set()
        raw_hits_before_dedup = 0
        raw_dup_skipped = 0
        total_requests = 0
        failed_requests = 0
        # 关键词召回统计：keyword -> 原始命中条数（跨该次调用的所有州/国家累加），去重前的量，
        # 用来判断哪些词值钱、哪些词该从 core.search_keywords 里砍掉
        keyword_hits: dict[str, int] = {}

        wanted = int(limit) if limit is not None else effective_scrape_limit()
        if limit is None:
            logger.info("JobSpy limit 未传入，使用 effective_scrape_limit()=%s", wanted)

        for country in target_countries:
            sites = self._site_names_for_country(country)
            passes = jobspy_geo_passes_by_canton(country)
            if cantons and country.strip() == "Switzerland":
                allowed = {c.upper() for c in cantons}
                passes = [p for p in passes if p[0] and p[0].upper() in allowed]
            n_passes = len(passes)
            # 瑞士多州多次请求：把 results_wanted 摊开，避免每州都要满额导致总量爆炸
            if n_passes > 1:
                per_pass_wanted = max(30, (wanted + n_passes - 1) // n_passes)
            else:
                per_pass_wanted = wanted

            country_param = jobspy_country_param(country)
            for canton_code, loc_str, dist_mi in passes:
                if use_loc_kw:
                    if canton_code:
                        loc_keywords = keywords_for_swiss_canton(canton_code, domains)
                    else:
                        loc_keywords = keywords_for_country(country, domains)
                    keyword_loop: list[Optional[str]] = loc_keywords if loc_keywords else [None]
                elif use_broad:
                    keyword_loop = [None]
                else:
                    keyword_loop = list(keywords) if keywords else [None]

                for keyword in keyword_loop:
                    if total_requests > 0:
                        delay = random_scrape_delay()
                        logger.debug("请求间随机等待 %.1fs", delay)
                        time.sleep(delay)

                    logger.info(
                        "抓取岗位: keyword=%r market=%s location=%r distance_mi=%s sites=%s results_wanted=%s",
                        keyword,
                        country,
                        loc_str,
                        dist_mi,
                        sites,
                        per_pass_wanted,
                    )
                    total_requests += 1
                    keyword_label = keyword if keyword is not None else "(broad)"
                    try:
                        df = scrape_jobs(
                            site_name=sites,
                            search_term=keyword,
                            country_indeed=country_param,
                            location=loc_str,
                            distance=dist_mi,
                            results_wanted=per_pass_wanted,
                            hours_old=self.hours_old,
                            linkedin_fetch_description=fetch_desc,
                        )
                        keyword_hits[keyword_label] = keyword_hits.get(keyword_label, 0) + len(df)
                        for _, row in df.iterrows():
                            raw = self._row_to_raw(row, country)
                            raw_hits_before_dedup += 1
                            eid = raw.external_job_id
                            src = raw.source or "unknown"
                            if eid:
                                k = (src, eid)
                                if k in seen_keys:
                                    raw_dup_skipped += 1
                                    continue
                                seen_keys.add(k)
                            results.append(raw)
                    except Exception as e:
                        failed_requests += 1
                        logger.warning(
                            "抓取失败 keyword=%r market=%s location=%r: %s",
                            keyword,
                            country,
                            loc_str,
                            e,
                        )

        logger.info(
            "JobSpy：原始命中 %s 条，跨区去重跳过 %s 条，合并后 %s 条（请求 %s/%s 失败）",
            raw_hits_before_dedup,
            raw_dup_skipped,
            len(results),
            failed_requests,
            total_requests,
        )
        if keyword_hits:
            ranked = sorted(keyword_hits.items(), key=lambda kv: kv[1], reverse=True)
            logger.info(
                "JobSpy 关键词召回统计（去重前，跨本次所有州/国家累加）: %s",
                ", ".join(f"{k!r}={v}" for k, v in ranked),
            )
        # 单个请求失败会被上面吞掉继续下一个（避免一个州/关键词的问题拖垮整批）；但如果这一轮
        # 全部请求都失败，大概率是站点限流/网络中断/账号被封之类的系统性问题，不该当成"恰好没有新岗位"
        # 静默放过——抛出去让上层（scoped_scrape_service/fetch_tasks）能感知并告警。
        if total_requests > 0 and failed_requests == total_requests:
            raise ScraperError(
                f"JobSpy 抓取全部 {total_requests} 次请求都失败（sites={self.site_names}, "
                f"countries={target_countries}），疑似限流/网络故障，未静默返回空列表"
            )
        return results

    def _site_names_for_country(self, country: str) -> list[str]:
        """站点列表规范化（卢森堡无 Glassdoor 时由上游不再传入 glassdoor）。"""
        raw = [str(s).strip().lower() for s in (self.site_names or ["linkedin"])]
        if country.strip().lower() == "luxembourg":
            filtered = [s for s in raw if s != "glassdoor"]
            return filtered if filtered else ["linkedin"]
        return [s for s in raw]

    def _row_to_raw(self, row: any, country: str) -> RawJobData:
        return RawJobData(
            source=str(row.get("site", "unknown")),
            external_job_id=self._to_str(row.get("id")),
            title=self._to_str(row.get("title")) or "",
            company=self._to_str(row.get("company")),
            location=self._to_str(row.get("location")),
            country=country,
            url=self._to_str(row.get("job_url")),
            description_raw=self._to_str(row.get("description")),
            date_posted=self._parse_date(row.get("date_posted")),
            extra={},
        )

    @staticmethod
    def _to_str(value: any) -> str | None:
        """安全转换：None 和字符串 'None'/'nan' 都返回 None"""
        if value is None:
            return None
        s = str(value).strip()
        if s.lower() in ("none", "nan", ""):
            return None
        return s

    @staticmethod
    def _parse_date(value: any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
