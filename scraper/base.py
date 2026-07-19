from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawJobData:
    """抓取器返回的原始岗位数据，统一结构"""
    source: str
    external_job_id: Optional[str]
    title: str
    company: Optional[str]
    location: Optional[str]
    country: Optional[str]
    url: Optional[str]
    description_raw: Optional[str]
    date_posted: Optional[datetime]
    extra: dict = field(default_factory=dict)


class BaseScraperProvider(ABC):
    """所有抓取器的抽象基类"""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """抓取源名称"""

    @abstractmethod
    def fetch_jobs(
        self,
        keywords: list[str],
        countries: list[str],
        limit: Optional[int] = None,
    ) -> list[RawJobData]:
        """抓取岗位列表。limit 为 None 时不按条数截断（仍受时间窗与站点能力限制）。"""
