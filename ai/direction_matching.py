"""
打分 Step 1：免费向量初筛（岗位侧完全不碰 LLM）。

用户求职方向的向量在方向创建/编辑时一次性算好（见 services/experience_library_service.py），
这里只做「岗位向量 vs 方向向量」的余弦比对。岗位向量本身也是 job 级缓存（Job.title_embedding），
同一岗位在不同用户的打分循环里只算一次。

reject：跟用户所有方向都明显不像，直接丢弃，不进入后续 LLM 步骤。
pass/borderline：都进入 Step 2-4，二者处理完全一样——这个三分类只是给以后调阈值时看分布用，
不是两条代码路径。

scoring_prefilter_sim_pass/reject 是拍的默认值，没有真实数据验证过，上线前应拿一批真实岗位/
方向的相似度分布看一眼再调（这台机器没有运行环境，验证不了）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.config import settings
from core.logger import get_logger
from db.models import Job, UserJobDirection

logger = get_logger(__name__)

_JOB_SNIPPET_LEN = 500

_embedder = None


def _get_embedder():
    global _embedder
    # 向量模型只在真正执行评分/方向编辑时加载；Web/API 启动不应强制安装它。
    from ai.title_embedder import TitleEmbedder
    if _embedder is None or _embedder.model_name != settings.scoring_embed_model:
        _embedder = TitleEmbedder(settings.scoring_embed_model)
    return _embedder


def _job_embed_text(job: Job) -> str:
    snippet = (job.description_clean or "")[:_JOB_SNIPPET_LEN]
    return f"{job.title}\n{snippet}".strip()


def embed_text(text: str) -> list[float]:
    """通用文本 embedding，供求职方向的扩写文本使用（见 services/experience_library_service.py）。
    复用同一个 embedder 单例，保证方向向量和岗位向量出自同一模型、可直接比余弦。"""
    return _get_embedder().embed_one(text).tolist()


def ensure_job_embedding(db: Session, job: Job) -> list[float]:
    """job.title_embedding 缺失时惰性计算并写回；job 级缓存，全体用户复用，不用每个用户重算。"""
    if job.title_embedding:
        return job.title_embedding
    vec = _get_embedder().embed_one(_job_embed_text(job))
    job.title_embedding = vec.tolist()
    db.flush()
    return job.title_embedding


def ensure_direction_expansion(
    db: Session, direction: UserJobDirection, llm, candidate_background: str | None = None
) -> None:
    """
    方向扩写（同义/近义职位名 + JD 口吻的一段话，提升向量匹配质量）在打分时才尝试补，不阻塞
    方向的保存——保存时只用原始 title 算的本地向量，已经能正常参与匹配。这里失败了就沿用
    现有向量继续本轮匹配，不抛出、不重试阻断；expanded_text 留空，下次打分再试，成功一次后
    写回缓存就不用再调了。candidate_background 传入时用候选人真实技能/经历给扩写文本"接地"
    （见 ai.scoring.expand_direction），只在第一次扩写时用得上——扩写一旦成功缓存，后续候选人
    编辑经历库不会触发重新扩写（跟"经历库/方向变更不倒查重打分"的既定成本取舍一致）。
    """
    if direction.expanded_text:
        return
    from ai.scoring import expand_direction
    try:
        expand = expand_direction(llm, direction.title, candidate_background)
        texts = [
            expand.get("expanded_text_en"),
            expand.get("expanded_text_de"),
            expand.get("expanded_text_fr"),
        ]
        texts = [t for t in texts if t]
        if not texts:
            texts = [direction.title]
        # expanded_text 只存英文版供调试/API 展示；实际参与比对的是下面三语向量的列表
        direction.expanded_text = texts[0]
        direction.embedding = [embed_text(t) for t in texts]
        direction.embed_model = settings.scoring_embed_model
        db.flush()
    except Exception as e:
        logger.warning("方向扩写失败（title=%r），本轮沿用原始标题向量继续匹配: %s", direction.title, e)


def load_active_direction_vectors(
    db: Session, user_id: int, llm=None, candidate_background: str | None = None
) -> list[list[float]]:
    """
    llm 传入时，顺带尝试给还没扩写过的方向补扩写（见 ensure_direction_expansion）。
    每个方向的 embedding 现在是三语（英/德/法）向量的列表，这里拍平成一个扁平的向量
    列表返回——classify()/classify_with_similarity() 本来就是对传入的整批向量取 max，
    不关心它们来自几个方向、几个语言版本，拍平之后语义完全对：只要某个方向的某个
    语言版本命中，这条岗位就该过，不要求同一方向的三个语言版本都过阈值。
    """
    rows = (
        db.query(UserJobDirection)
        .filter(UserJobDirection.user_id == user_id, UserJobDirection.is_active.is_(True))
        .all()
    )
    if llm is not None:
        for d in rows:
            ensure_direction_expansion(db, d, llm, candidate_background)
    return [vec for d in rows if d.embedding for vec in d.embedding]


def classify(job_vector: list[float], direction_vectors: list[list[float]]) -> str:
    """direction_vectors 为空（用户没配置方向）时一律视为 pass，不阻断老用户。"""
    if not direction_vectors:
        return "pass"
    import numpy as np
    jv = np.asarray(job_vector, dtype=np.float32)
    dv = np.asarray(direction_vectors, dtype=np.float32)
    sims = dv @ jv  # 都已 L2 归一化，点积=余弦
    max_sim = float(sims.max()) if sims.size else 0.0
    if max_sim >= settings.scoring_prefilter_sim_pass:
        return "pass"
    if max_sim <= settings.scoring_prefilter_sim_reject:
        return "reject"
    return "borderline"


def classify_with_similarity(
    job_vector: list[float], direction_vectors: list[list[float]]
) -> tuple[str, float | None]:
    """返回初筛标签和最高方向余弦相似度，供开发诊断使用。"""
    if not direction_vectors:
        return "pass", None
    import numpy as np
    jv = np.asarray(job_vector, dtype=np.float32)
    dv = np.asarray(direction_vectors, dtype=np.float32)
    sims = dv @ jv
    max_sim = float(sims.max()) if sims.size else 0.0
    if max_sim >= settings.scoring_prefilter_sim_pass:
        label = "pass"
    elif max_sim <= settings.scoring_prefilter_sim_reject:
        label = "reject"
    else:
        label = "borderline"
    return label, max_sim


def prefilter_job(db: Session, job: Job, direction_vectors: list[list[float]]) -> str:
    """一站式：direction_vectors 为空直接 pass（不算岗位向量，省一次 embed）；否则算/取岗位向量比对。"""
    if not direction_vectors:
        return "pass"
    job_vector = ensure_job_embedding(db, job)
    return classify(job_vector, direction_vectors)


def prefilter_job_with_similarity(
    db: Session, job: Job, direction_vectors: list[list[float]]
) -> tuple[str, float | None]:
    """初筛结果 + 最高相似度；不改变原有 prefilter_job API。"""
    if not direction_vectors:
        return "pass", None
    job_vector = ensure_job_embedding(db, job)
    return classify_with_similarity(job_vector, direction_vectors)
