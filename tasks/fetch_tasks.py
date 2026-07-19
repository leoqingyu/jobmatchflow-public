from datetime import datetime
from typing import Optional

from core.alerts import alert_task_failure
from core.logger import get_logger
from core.swiss_cantons import HOT_CANTONS, TAIL_CANTON_WEEKDAY_GROUPS
from db.session import get_db
from scraper.providers.jobspy_provider import JobSpyProvider
from services.pipeline_service import PipelineService
from services.scoped_scrape_service import scrape_source_and_ingest

logger = get_logger(__name__)

_CH = ["Switzerland"]
_LU = ["Luxembourg"]


def run_full_pipeline_task(user_id: int) -> dict:
    """
    完整 pipeline 任务入口：一次性抓全部源（旧 ScrapeOrchestrator）+ 下游，供手动/调试触发使用
    （`/pipeline/run`，见 AdminPage 的 "Run Pipeline" 按钮）。自动调度改用下面的分时段抓取任务；
    打分+生成是独立常驻进程（services/matching_worker_service.py），不挂在这条手动路径上——
    这个函数触发的下游步骤基本都是"已处理过就跳过"的空跑，留着只为兼容这个手动调试入口。
    """
    logger.info(f"开始执行 Pipeline user_id={user_id}")
    with get_db() as db:
        pipeline = PipelineService(db)
        result = pipeline.run_full_pipeline(user_id)
    logger.info(f"Pipeline 完成: {result}")
    return result


def _run_jobspy_slot(
    *,
    countries: list[str],
    hours_old: int,
    canton_codes: Optional[list[str]],
    keyword_domains: Optional[list[str]],
    log_tag: str,
) -> dict:
    """
    一个抓取时段内跑 JobSpy（同一批州/领域/时间窗），查重入库。
    失败会记日志 + 发告警邮件，标明具体是哪个时段（jobspy_<tag>）失败，不静默吞掉。
    """
    with get_db() as db:
        provider = JobSpyProvider(
            site_names=["linkedin"],
            hours_old=hours_old,
            use_location_keywords=True,
            keyword_domains=keyword_domains,
            canton_codes=canton_codes,
            linkedin_fetch_description=False,
        )
        try:
            result = scrape_source_and_ingest(
                db,
                provider,
                countries=countries,
                hours_old=hours_old,
                log_source=f"jobspy_{log_tag}",
            )
        except Exception as e:
            db.rollback()
            alert_task_failure(f"jobspy_{log_tag}", e)
            result = {"error": str(e)}
    return {"jobspy": result}


def run_ch_hot_canton_slot_task(domain: str) -> dict:
    """
    瑞士六个岗位密度最高的州（core.swiss_cantons.HOT_CANTONS），一天 3 个时段各只搜一个领域
    （tech/finance/cross），24h 窗口。只做抓取 + 入库，不跑下游。
    """
    logger.info("热门州抓取时段开始: domain=%s cantons=%s", domain, HOT_CANTONS)
    result = _run_jobspy_slot(
        countries=_CH,
        hours_old=24,
        canton_codes=HOT_CANTONS,
        keyword_domains=[domain],
        log_tag=f"hot6_{domain}",
    )
    logger.info("热门州抓取时段完成 domain=%s: %s", domain, result)
    return result


def run_lu_daily_slot_task() -> dict:
    """卢森堡，每天一次，全领域，24h 窗口。只做抓取 + 入库。"""
    logger.info("卢森堡每日抓取时段开始")
    result = _run_jobspy_slot(
        countries=_LU,
        hours_old=24,
        canton_codes=None,
        keyword_domains=None,
        log_tag="lu",
    )
    logger.info("卢森堡每日抓取时段完成: %s", result)
    return result


def run_ch_tail_weekday_slot_task() -> dict:
    """
    其余 20 个岗位较少的州：按 core.swiss_cantons.TAIL_CANTON_WEEKDAY_GROUPS 取
    datetime.now().weekday() 对应的当天分组，168h（一周）窗口，全领域。
    """
    weekday = datetime.now().weekday()
    group = TAIL_CANTON_WEEKDAY_GROUPS.get(weekday, [])
    logger.info("尾部州抓取时段开始: weekday=%s cantons=%s", weekday, group)
    if not group:
        logger.warning("尾部州抓取时段: weekday=%s 无对应分组，跳过", weekday)
        return {"skipped": True, "weekday": weekday}
    result = _run_jobspy_slot(
        countries=_CH,
        hours_old=24 * 7,
        canton_codes=group,
        keyword_domains=None,
        log_tag=f"tail_wd{weekday}",
    )
    logger.info("尾部州抓取时段完成 weekday=%s: %s", weekday, result)
    return result
