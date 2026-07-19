"""
岗位漏斗去重（JobSpy 抓取结果共用）：

1) 规范化：去重音、小写、去掉标点与特殊符号（仅保留 Unicode 词字符与空白），再比较「公司|职位」整键；相等则视为重复。
2) 同规范化公司名下，对职位标题用本地 Embedding 余弦相似度（归一化向量点积）召回候选：
   - > embed_sim_high（默认 0.97）→ 仍交给 LLM 判断，不再直接删除
   - < embed_sim_low（默认 0.87）→ 不重复
   - 介于两者之间 → 调用 LLM 单对单判断（A=母库余弦最高岗，B=候选；无 LLM 时保守保留候选）
   - 标题包含不同职级/明确岗位方向标记时强制保留，防止同公司不同岗位误合并
3) 候选批内：同一规范化整键仅保留首次出现，不调模型。

`dedupe_ordered_sequence`：按列表顺序依次并入已保留集（先出现者优先）。

入库前在 `jobs_ready_for_ingestion` 内再跑顺序去重：有向量依赖时为步骤 1 + 步骤 2；无 LLM 时向量只召回、不删除，最终保留候选。
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import numpy as np

from core.config import settings
from core.logger import get_logger
from scraper.base import RawJobData

from ai.title_embedder import TitleEmbedder
from scraper.dedup import compute_jd_fingerprint

if TYPE_CHECKING:
    from ai.llm_client import LLMClient

logger = get_logger(__name__)

# INFO 日志里打印模型原文的最大字符数
_RAW_LOG_MAX = 1200

# 去重灰区每调用一次 LLM 计数；每满 N 次暂停，降低限流 / RESOURCE_EXHAUSTED
_DEDUP_GREY_LLM_EVERY = 10
_DEDUP_GREY_LLM_SLEEP_SEC = 1.0

# 这些词表示同公司下的不同招聘岗位，不允许仅凭标题向量/LLM 直接合并。
# 例如 Salesforce 的普通 / Lead / Senior 岗位，或 ETH 的 Evaluations / Deployment 岗位。
_TITLE_MARKER_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "senior",
            "lead",
            "principal",
            "staff",
            "junior",
            "intern",
            "manager",
            "director",
            "head",
            "trainee",
            "apprentice",
            "lehre",
            "efz",
        }
    ),
    frozenset(
        {
            "infrastructure",
            "deployment",
            "evaluations",
            "evaluation",
            "application",
            "applikationsentwicklung",
            "plattformentwicklung",
        }
    ),
)


def normalize_funnel_text(s: str | None) -> str:
    """
    第一步规范化：小写、NFKD、去组合音标、非词字符（含标点）变空格、压成单空格。
    用于公司或职位单列或拼接对比。
    """

    def _part(text: str) -> str:
        t = (text or "").strip().lower()
        if not t:
            return ""
        t = unicodedata.normalize("NFKD", t)
        t = "".join(ch for ch in t if not unicodedata.combining(ch))
        t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    return _part(s or "")


def funnel_step1_key(company: str | None, title: str | None) -> str:
    """规范化后的「公司|职位」整键，用于第一步相等即重复。"""
    c = normalize_funnel_text(company)
    t = normalize_funnel_text(title)
    if not c and not t:
        return "__empty__"
    return f"{c}|{t}"


@dataclass(frozen=True)
class DedupRemovalExplain:
    """单笔候选相对当前母库被判重时，与母库中哪一条对齐、因何去掉。"""

    reason: str
    matched: RawJobData
    similarity: float | None = None


def ordered_step1_removal_match(ordered: list[RawJobData], removed_index: int) -> RawJobData | None:
    """与 `dedupe_ordered_deterministic` 一致：去掉项对应「同规范化整键」下最先出现的那条。"""
    if removed_index <= 0:
        return None
    k = funnel_step1_key(ordered[removed_index].company, ordered[removed_index].title)
    for j in range(removed_index):
        if funnel_step1_key(ordered[j].company, ordered[j].title) == k:
            return ordered[j]
    return None


def _first_mother_same_step1_key(mother_jobs: list[RawJobData], cand_key: str) -> RawJobData | None:
    for m in mother_jobs:
        if funnel_step1_key(m.company, m.title) == cand_key:
            return m
    return None


def normalize_dedup_key(company: str | None, title: str | None) -> str:
    """兼容旧名，等同 funnel_step1_key。"""
    return funnel_step1_key(company, title)


def _mother_key_set(mother_jobs: list[RawJobData]) -> set[str]:
    keys = {funnel_step1_key(j.company, j.title) for j in mother_jobs}
    keys.discard("__empty__")
    return keys


def _build_company_bucket(mother_jobs: list[RawJobData]) -> dict[str, list[RawJobData]]:
    """规范化公司名 -> 该公司下母库岗位列表（用于同公司完整职位文本向量比对）。"""
    bucket: dict[str, list[RawJobData]] = {}
    for j in mother_jobs:
        cn = normalize_funnel_text(j.company)
        if not cn:
            continue
        bucket.setdefault(cn, []).append(j)
    return bucket


def _job_jd_key(job: RawJobData) -> str | None:
    """公司 + 完整 JD 指纹；避免相同模板 JD 跨公司误合并。"""
    fp = compute_jd_fingerprint(job)
    company = normalize_funnel_text(job.company)
    if not fp or not company:
        return None
    return f"{company}|{fp}"


def _job_embedding_text(job: RawJobData) -> str:
    """全文 embedding 输入：标题保留召回信息，JD 使用完整正文。"""
    title = (job.title or "").strip()
    description = (job.description_raw or "").strip()
    return f"{title}\n{description}".strip() or " "


_SYSTEM_GREY = """你是招聘岗位去重助手。你会收到两条记录 A（母库已保留）与 B（候选）。
判断它们是否指向同一个真实招聘岗位，而不是仅仅属于同一职位族。

以下情况通常不是重复，必须回答 false：
- 职级不同（如 Senior、Lead、Principal、Junior、Intern、Manager）；
- 工作方向或团队不同（如 Infrastructure、Deployment、Evaluations、Platform、Application）；
- 同公司下不同招聘岗位，即使职位名都含 Engineer、Analyst 或 Developer；
- JD 中职责、技术栈或项目目标明显不同。

只有在公司相同且职位实质相同、只是来源重复或标题轻微改写时，才回答 true。
只输出一行：true 或 false（小写），不要其它文字。true = 重复（应丢弃候选 B）。"""


def _parse_duplicate_line(raw: str) -> bool:
    text = (raw or "").strip().lower()
    if not text:
        return False
    first = text.splitlines()[0].strip()
    for noise in ("`", "*", '"', "'"):
        first = first.replace(noise, "")
    first = first.strip()
    if first == "true" or first.startswith("true"):
        return True
    if first == "false" or first.startswith("false"):
        return False
    return False


def _has_conflicting_title_markers(title_a: str | None, title_b: str | None) -> bool:
    """判断两个标题是否包含明确冲突的职级/岗位方向标记。"""
    a = set(normalize_funnel_text(title_a).split())
    b = set(normalize_funnel_text(title_b).split())
    for group in _TITLE_MARKER_GROUPS:
        ma = a & group
        mb = b & group
        if ma != mb and (ma or mb):
            return True
    return False


def _cosine_max_one_vs_many(v_cand: np.ndarray, v_matrix: np.ndarray) -> tuple[float, int]:
    """v_cand (d,), v_matrix (k,d) 均已 L2 归一化。返回 (max_sim, argmax_index)。"""
    if v_matrix.size == 0:
        return 0.0, -1
    sims = v_matrix @ v_cand
    j = int(np.argmax(sims))
    return float(sims[j]), j


def filter_new_only_step1(
    mother_jobs: list[RawJobData],
    candidates: list[RawJobData],
) -> tuple[list[RawJobData], list[int]]:
    """
    无向量服务时：仅第一步规范化 + 批内同键去重，相对母库过滤。
    供编排器在 build_job_dedup_service 返回 None 时仍对齐英与母库。
    """
    if not candidates:
        return [], []
    if not mother_jobs:
        return list(candidates), []

    mother_keys = _mother_key_set(mother_jobs)
    first_index_by_key: dict[str, int] = {}
    intra_batch_dup_indices: set[int] = set()
    for i, cand in enumerate(candidates):
        k = funnel_step1_key(cand.company, cand.title)
        if k in first_index_by_key:
            intra_batch_dup_indices.add(i)
        else:
            first_index_by_key[k] = i

    kept: list[RawJobData] = []
    removed_idx: list[int] = []
    n1 = len(candidates) - 1

    for i, cand in enumerate(candidates):
        c_company = (cand.company or "").strip() or "(未知公司)"
        c_title = (cand.title or "").strip() or "(无标题)"
        if i in intra_batch_dup_indices:
            j0 = first_index_by_key[funnel_step1_key(cand.company, cand.title)]
            logger.info(
                "job_dedup_step1 idx=%s/%s reason=intra_batch_duplicate first_idx=%s candidate=%r / %r",
                i,
                n1,
                j0,
                c_company,
                c_title,
            )
            removed_idx.append(i)
            continue
        cand_key = funnel_step1_key(cand.company, cand.title)
        if cand_key in mother_keys:
            logger.info(
                "job_dedup_step1 idx=%s/%s reason=step1_key_match candidate=%r / %r",
                i,
                n1,
                c_company,
                c_title,
            )
            removed_idx.append(i)
            continue
        kept.append(cand)

    logger.info(
        "job_dedup_step1_only: 候选=%s 去掉=%s 保留=%s",
        len(candidates),
        len(removed_idx),
        len(kept),
    )
    return kept, removed_idx


def dedupe_ordered_step1_before_ingest(jobs: list[RawJobData]) -> list[RawJobData]:
    """入库前仅第一步：按列表顺序，同一规范化整键保留首次。"""
    seen: set[str] = set()
    out: list[RawJobData] = []
    for j in jobs:
        k = funnel_step1_key(j.company, j.title)
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out


def _st_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def build_job_dedup_service(skip_llm: bool) -> Optional["JobDedupLLMService"]:
    """
    构造漏斗去重服务。若未安装 sentence-transformers，返回 None（编排器退回仅第一步）。
    skip_llm=True 时灰区一律保留候选，不调 Gemini。
    """
    llm: Optional[LLMClient] = None
    if not skip_llm:
        try:
            from ai.providers.gemini_model import GeminiModelClient

            llm = GeminiModelClient()
        except Exception as e:
            logger.warning("无法初始化 Gemini 去重客户端: %s", e)

    if not _st_available():
        logger.warning("未安装 sentence-transformers，跨源去重仅使用第一步规范化（无文本向量漏斗）")
        return None

    emb = TitleEmbedder(settings.job_dedup_embed_model)
    return JobDedupLLMService(
        llm=llm,
        embedder=emb,
        embed_sim_high=settings.job_dedup_embed_sim_high,
        embed_sim_low=settings.job_dedup_embed_sim_low,
    )


class JobDedupLLMService:
    """漏斗去重：规范化/JD 指纹 → 本地完整职位文本向量 → 可选 LLM。"""

    def __init__(
        self,
        llm: Optional[LLMClient],
        embedder: TitleEmbedder,
        *,
        embed_sim_high: float,
        embed_sim_low: float,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._high = float(embed_sim_high)
        self._low = float(embed_sim_low)
        self._dedup_grey_llm_calls = 0

    def _pause_after_dedup_grey_llm(self) -> None:
        self._dedup_grey_llm_calls += 1
        if self._dedup_grey_llm_calls % _DEDUP_GREY_LLM_EVERY == 0:
            time.sleep(_DEDUP_GREY_LLM_SLEEP_SEC)

    @staticmethod
    def _job_block(label: str, job: RawJobData) -> str:
        """给 LLM 的最小判断上下文；正文只取摘要，避免把整份 JD 送入每次判断。"""
        company = (job.company or "").strip() or "(未知公司)"
        title = (job.title or "").strip() or "(无标题)"
        description = (job.description_raw or "").strip()
        if len(description) > 1800:
            description = description[:1800] + "…"
        return (
            f"{label} 公司：{company}\n"
            f"{label} 职位：{title}\n"
            f"{label} JD 摘要：{description or '(无 JD 摘要)'}"
        )

    def _llm_decides_duplicate(
        self,
        mother: RawJobData,
        cand: RawJobData,
        similarity: float,
    ) -> bool:
        """向量只做召回；最终是否删除交给 LLM，失败时保守保留。"""
        if _has_conflicting_title_markers(mother.title, cand.title):
            logger.info(
                "job_dedup_marker_keep candidate=%r / %r vs mother=%r / %r",
                cand.company,
                cand.title,
                mother.company,
                mother.title,
            )
            return False
        if self._llm is None:
            return False
        user = (
            f"{self._job_block('A', mother)}\n\n"
            f"{self._job_block('B', cand)}\n\n"
            f"标题向量相似度：{similarity:.4f}\n"
            "B 是否与 A 指向同一个真实招聘岗位？只回答 true 或 false。"
        )
        try:
            raw = self._llm.complete_text(
                task_name="job_dedup_pair",
                user_prompt=user,
                system_prompt=_SYSTEM_GREY,
            )
            is_dup = _parse_duplicate_line(raw)
            raw_s = raw or ""
            first_line = raw_s.strip().splitlines()[0] if raw_s.strip() else ""
            logger.info(
                "job_dedup_llm sim=%.4f candidate=%r / %r vs mother=%r / %r "
                "parsed_duplicate=%s first_line=%r",
                similarity,
                cand.company,
                cand.title,
                mother.company,
                mother.title,
                is_dup,
                first_line,
            )
            return is_dup
        except Exception as e:
            logger.warning("job_dedup LLM 判断失败: %s，保留候选", e)
            return False
        finally:
            self._pause_after_dedup_grey_llm()

    def explain_if_removed_vs_mother(
        self,
        mother_jobs: list[RawJobData],
        cand: RawJobData,
    ) -> Optional[DedupRemovalExplain]:
        """
        与 ``filter_new_only(mother_jobs, [cand])`` 单笔语义一致（无日志）：
        若该候选会被去掉则返回母库中对应的那条及原因，否则 ``None``。
        母库为空时候选恒保留，返回 ``None``。
        """
        if not mother_jobs:
            return None

        mother_keys = _mother_key_set(mother_jobs)
        mother_jd_keys = {_job_jd_key(j) for j in mother_jobs} - {None}
        company_bucket = _build_company_bucket(mother_jobs)

        cand_key = funnel_step1_key(cand.company, cand.title)
        if cand_key in mother_keys:
            matched = _first_mother_same_step1_key(mother_jobs, cand_key)
            if matched is None:
                return None
            return DedupRemovalExplain(reason="step1_key_match", matched=matched, similarity=None)

        cand_jd_key = _job_jd_key(cand)
        if cand_jd_key and cand_jd_key in mother_jd_keys:
            matched = next((m for m in mother_jobs if _job_jd_key(m) == cand_jd_key), None)
            if matched is not None and not _has_conflicting_title_markers(matched.title, cand.title):
                return DedupRemovalExplain(reason="jd_fingerprint", matched=matched, similarity=1.0)

        comp_n = normalize_funnel_text(cand.company)
        if not comp_n:
            return None

        mothers = company_bucket.get(comp_n, [])
        if not mothers:
            return None

        try:
            mother_texts = [_job_embedding_text(m) for m in mothers]
            v_matrix = self._embedder.embed_full_many(mother_texts)
            v_cand = self._embedder.embed_full_many([_job_embedding_text(cand)])[0]
            s_max, j = _cosine_max_one_vs_many(v_cand, v_matrix)
        except Exception:
            return None

        if s_max > self._high:
            best = mothers[j] if j >= 0 else mothers[0]
            if self._llm_decides_duplicate(best, cand, s_max):
                return DedupRemovalExplain(reason="llm_duplicate", matched=best, similarity=s_max)
            return None

        if s_max < self._low:
            return None

        best = mothers[j] if j >= 0 else mothers[0]
        if self._llm_decides_duplicate(best, cand, s_max):
            return DedupRemovalExplain(reason="llm_duplicate", matched=best, similarity=s_max)
        return None

    def filter_new_only(
        self,
        mother_jobs: list[RawJobData],
        candidates: list[RawJobData],
    ) -> tuple[list[RawJobData], list[int]]:
        if not candidates:
            return [], []
        if not mother_jobs:
            return list(candidates), []

        mother_keys = _mother_key_set(mother_jobs)
        mother_jd_keys = {_job_jd_key(j) for j in mother_jobs} - {None}
        company_bucket = _build_company_bucket(mother_jobs)

        first_index_by_key: dict[str, int] = {}
        first_index_by_jd: dict[str, int] = {}
        intra_batch_dup_indices: set[int] = set()
        for i, cand in enumerate(candidates):
            k = funnel_step1_key(cand.company, cand.title)
            if k in first_index_by_key:
                intra_batch_dup_indices.add(i)
            else:
                first_index_by_key[k] = i
            jd_key = _job_jd_key(cand)
            if jd_key and jd_key in first_index_by_jd:
                intra_batch_dup_indices.add(i)
            elif jd_key:
                first_index_by_jd[jd_key] = i

        kept: list[RawJobData] = []
        removed_idx: list[int] = []
        n1 = len(candidates) - 1

        stats = {
            "step1_mother": 0,
            "step1_intra": 0,
            "embed_high": 0,
            "embed_low_keep": 0,
            "llm_dup": 0,
            "llm_keep": 0,
            "no_company_bucket": 0,
        }

        for i, cand in enumerate(candidates):
            c_company = (cand.company or "").strip() or "(未知公司)"
            c_title = (cand.title or "").strip() or "(无标题)"

            if i in intra_batch_dup_indices:
                j0 = first_index_by_key[funnel_step1_key(cand.company, cand.title)]
                logger.info(
                    "job_dedup idx=%s/%s reason=intra_batch_duplicate first_idx=%s candidate=%r / %r",
                    i,
                    n1,
                    j0,
                    c_company,
                    c_title,
                )
                removed_idx.append(i)
                stats["step1_intra"] += 1
                continue

            cand_key = funnel_step1_key(cand.company, cand.title)
            if cand_key in mother_keys:
                logger.info(
                    "job_dedup idx=%s/%s reason=step1_key_match candidate=%r / %r",
                    i,
                    n1,
                    c_company,
                    c_title,
                )
                removed_idx.append(i)
                stats["step1_mother"] += 1
                continue

            cand_jd_key = _job_jd_key(cand)
            if cand_jd_key and cand_jd_key in mother_jd_keys:
                matched = next((m for m in mother_jobs if _job_jd_key(m) == cand_jd_key), None)
                if matched is not None and _has_conflicting_title_markers(matched.title, cand.title):
                    logger.info(
                        "job_dedup_marker_keep candidate=%r / %r vs fingerprint_match=%r / %r",
                        c_company,
                        c_title,
                        matched.company,
                        matched.title,
                    )
                    kept.append(cand)
                    continue
                logger.info(
                    "job_dedup idx=%s/%s reason=jd_fingerprint candidate=%r / %r matched=%r / %r",
                    i,
                    n1,
                    c_company,
                    c_title,
                    matched.company if matched else "(未知公司)",
                    matched.title if matched else "(无标题)",
                )
                removed_idx.append(i)
                stats["step1_mother"] += 1
                continue

            comp_n = normalize_funnel_text(cand.company)
            if not comp_n:
                kept.append(cand)
                stats["no_company_bucket"] += 1
                continue

            mothers = company_bucket.get(comp_n, [])
            if not mothers:
                kept.append(cand)
                continue

            try:
                mother_texts = [_job_embedding_text(m) for m in mothers]
                v_matrix = self._embedder.embed_full_many(mother_texts)
                v_cand = self._embedder.embed_full_many([_job_embedding_text(cand)])[0]
                s_max, j = _cosine_max_one_vs_many(v_cand, v_matrix)
            except Exception as e:
                logger.warning("job_dedup idx=%s 完整职位文本向量失败，保留候选: %s", i, e)
                kept.append(cand)
                continue

            if s_max > self._high:
                stats["embed_high"] += 1
                best = mothers[j] if j >= 0 else mothers[0]
                if self._llm_decides_duplicate(best, cand, s_max):
                    removed_idx.append(i)
                    stats["llm_dup"] += 1
                    continue
                logger.info(
                    "job_dedup idx=%s/%s reason=embed_high_llm_keep sim=%.4f candidate=%r / %r",
                    i,
                    n1,
                    s_max,
                    c_company,
                    c_title,
                )
                kept.append(cand)
                stats["llm_keep"] += 1
                continue

            if s_max < self._low:
                kept.append(cand)
                stats["embed_low_keep"] += 1
                continue

            if self._llm is None:
                logger.info(
                    "job_dedup idx=%s/%s reason=grey_no_llm_keep sim=%.4f candidate=%r / %r",
                    i,
                    n1,
                    s_max,
                    c_company,
                    c_title,
                )
                kept.append(cand)
                continue

            best = mothers[j] if j >= 0 else mothers[0]
            if self._llm_decides_duplicate(best, cand, s_max):
                removed_idx.append(i)
                stats["llm_dup"] += 1
            else:
                kept.append(cand)
                stats["llm_keep"] += 1

        logger.info(
            "job_dedup 漏斗统计: 候选=%s 去掉=%s "
            "(step1母库=%s step1批内=%s embed高候选=%s embed低保留=%s 无公司桶=%s llm丢=%s llm留=%s)",
            len(candidates),
            len(removed_idx),
            stats["step1_mother"],
            stats["step1_intra"],
            stats["embed_high"],
            stats["embed_low_keep"],
            stats["no_company_bucket"],
            stats["llm_dup"],
            stats["llm_keep"],
        )
        return kept, removed_idx

    def dedupe_ordered_sequence(
        self,
        jobs: list[RawJobData],
    ) -> tuple[list[RawJobData], list[int]]:
        if not jobs:
            return [], []

        kept: list[RawJobData] = []
        removed_idx: list[int] = []
        for i, job in enumerate(jobs):
            new_rows, _ = self.filter_new_only(kept, [job])
            if new_rows:
                kept.append(new_rows[0])
            else:
                removed_idx.append(i)
        logger.info(
            "job_dedup_sequence: 输入 %s 条，保留 %s 条，去掉 %s 条",
            len(jobs),
            len(kept),
            len(removed_idx),
        )
        return kept, removed_idx


def dedupe_ordered_deterministic(jobs: list[RawJobData]) -> tuple[list[RawJobData], list[int]]:
    """无向量/无服务时仅用第一步规范化：同键保留首次出现。"""
    kept: list[RawJobData] = []
    removed_idx: list[int] = []
    seen: set[str] = set()
    for i, job in enumerate(jobs):
        k = funnel_step1_key(job.company, job.title)
        if k in seen:
            removed_idx.append(i)
            continue
        seen.add(k)
        kept.append(job)
    logger.info(
        "job_dedup_sequence_deterministic: 输入 %s 条，保留 %s 条，去掉 %s 条",
        len(jobs),
        len(kept),
        len(removed_idx),
    )
    return kept, removed_idx
