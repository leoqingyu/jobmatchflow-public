from enum import Enum

from core.job_markets import ALLOWED_JOB_MARKET_CODES


class JobStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DUPLICATE = "duplicate"


class ApplicationStatus(str, Enum):
    """
    投递后跟进阶段：APPLIED -> INTERVIEW -> {OFFER, REJECTED}（也允许 APPLIED 直接到
    REJECTED，跳过面试）。APPLIED/INTERVIEW 单向不可回溯；OFFER/REJECTED 彼此互为合法
    下一步（点错/事后变化可以互改，比如 offer 被撤回改成 rejected），但都不能倒退回
    INTERVIEW/APPLIED。校验逻辑见 services/tracking_service.py::ApplicationTrackingService.
    _ALLOWED_TRANSITIONS。

    没有"投递前"的状态——ApplicationTracking 记录只在用户点击 mark-applied 时才创建；
    投递前"收藏关注"是完全独立的概念，见 db.models.user_saved_job.UserSavedJob。
    """

    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"


# 全部跟进阶段都属于"已投递之后"，简历/求职信一律仅在 Tracking 展示/下载，不出现在 Assets Preview。
ASSETS_DEFERRED_TO_TRACKING_STATUSES: frozenset[str] = frozenset(
    {
        ApplicationStatus.APPLIED.value,
        ApplicationStatus.INTERVIEW.value,
        ApplicationStatus.OFFER.value,
        ApplicationStatus.REJECTED.value,
    }
)


class ScoringDecision(str, Enum):
    DISCARD = "discard"
    REVIEW = "review"
    GENERATE = "generate"


class AssetType(str, Enum):
    RESUME_JSON = "resume_json"
    RESUME_HTML = "resume_html"
    RESUME_PDF = "resume_pdf"
    RESUME_DOCX = "resume_docx"
    MOTIVATION_LETTER = "motivation_letter"


class ExperienceTier(str, Enum):
    """用户对经历单元一次性标注的含金量粗档，见 db.models.user_experience_unit.UserExperienceUnit.tier。"""

    FLAGSHIP = "flagship"
    SOLID = "solid"
    FILLER = "filler"


class PreferenceAction(str, Enum):
    """简历选材偏好日志的动作类型，见 db.models.user_experience_preference_event。"""

    SELECTED_BY_AI = "selected_by_ai"
    REMOVED_BY_USER = "removed_by_user"
    ADDED_BY_USER = "added_by_user"
    REORDERED = "reordered"


class JobDomain(str, Enum):
    """岗位粗领域桶，Step 2 JD 原子要求提取时顺带产出，见 Job.domain。"""

    TECH_BACKEND = "tech-backend"
    TECH_DATA = "tech-data"
    FINANCE_COMPLIANCE = "finance-compliance"
    FINANCE_QUANT = "finance-quant"
    CROSS_FINTECH = "cross-fintech"
    OTHER = "other"


class EmploymentType(str, Enum):
    """岗位用工类型，Step 2 JD 原子要求提取时顺带产出，见 Job.employment_type。
    internship 覆盖实习/working student/trainee 这类明确的固定期限早期职业项目；
    graduate_program 单独一档——这类项目招聘时经常两说都通（有的按 internship 走，
    有的直接是全职轨道），不强行二选一分类，见 employment_type_mismatch：这一档
    对 internship_only / full_time_only 两种用户偏好都不触发硬过滤；
    其余（含 junior 全职）都算 full_time。"""

    INTERNSHIP = "internship"
    GRADUATE_PROGRAM = "graduate_program"
    FULL_TIME = "full_time"


class EmploymentTypePreference(str, Enum):
    """用户的实习/全职求职偏好，见 UserProfile.employment_type_preference。
    这是打分流水线最前置的硬性过滤——命中就直接跳过 Step 3/4/5，不是分数封顶。"""

    INTERNSHIP_ONLY = "internship_only"
    FULL_TIME_ONLY = "full_time_only"
    BOTH = "both"


class GenerationModel(str, Enum):
    """用户在 Settings 里为简历/求职信生成选的 LLM，见 UserProfile.generation_model。
    跟打分链路（core.config.scoring_model_cheap/mid/match，见 ai/llm_factory.py）是两条
    互不影响的选择——那三档是运营侧配置，这个是用户自己的生成偏好。"""

    GEMINI = "gemini"
    CLAUDE = "claude"


class ResumeTailoringMode(str, Enum):
    """用户在 Settings 里选的简历 bullet 改写尺度，见 UserProfile.resume_tailoring_mode。
    只影响 ai/resume_rewrite.py 的 bullet 改写 prompt（哪个文件、允许多大的推断尺度）——
    Skills 分类那边的 tier2/3"合理推断补技能"逻辑本来就一直开着，不受这个开关影响，见
    ai/prompts/resume_skills_categorize_v1.txt。

    两档在"数字指标/公司名必须来自原始经历"这条硬底线上完全一样，区别只在于允许 LLM
    在多大范围内做专业推断/自信框架：
    - HONEST：只能重组/改写/强调原文已经写明的事实，不做任何超出字面的推断。
    - JD_ALIGNED：可以更自信地表达 ownership、套用 JD 本身的术语描述同一件事，技术栈上
      也可以点出候选人大概率具备但没写进这条经历里的相关技能（比如常年用 AWS、JD 要
      Azure，可以点出候选人大概率也能上手）——最终稿用户会亲自过一遍，判断不准的地方
      用户自己删，不是无监督直接发出去。"""

    HONEST = "honest"
    JD_ALIGNED = "jd_aligned"


# Step 0/1 硬过滤的 UserJobScore 哨兵 llm_model 值（写入方见 services/scoring_service.py 的
# _persist_prefilter_reject / _persist_employment_type_reject；读取方见
# services/jobs_list_data.py 的 _join_score，主分数 join 需要认到这两个值，否则向量/用工类型
# 拦截掉的岗位在 Jobs List 里查不到对应的 UserJobScore 行，会被误判成"还没打分"而不是
# "已经被拦截"，见 processing_status 的计算逻辑）。两边共用同一份常量，避免字符串各写各的。
PREFILTER_REJECT_MODEL = "vector_prefilter"
EMPLOYMENT_TYPE_REJECT_MODEL = "employment_type_gate"


class AssetStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


class NotificationChannel(str, Enum):
    EMAIL = "email"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class PipelineStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class PipelineRunType(str, Enum):
    FULL = "full"
    FETCH_ONLY = "fetch_only"
    SCORE_ONLY = "score_only"
    GENERATE_ONLY = "generate_only"


# 支持的抓取来源
SUPPORTED_SOURCES = ["jobspy"]

# 目标国家（与 Settings 求职国家一致；实际抓取以 user_search_profiles.countries 为准）
TARGET_COUNTRIES = sorted(ALLOWED_JOB_MARKET_CODES)

# 目标领域关键词
TARGET_DOMAINS = ["data", "fintech", "analytics", "machine learning", "risk"]

# 用户可保存的原始简历份数上限（与素材库生成、评分推荐共用）
MAX_USER_SAVED_RESUMES = 5

# Settings 中「评分偏好」短文本上限（并入 Gemini 评分缓存上下文）
SCORING_PREFERENCES_MAX_CHARS = 300
