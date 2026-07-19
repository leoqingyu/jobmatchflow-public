from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# 与 cwd 无关，始终读取项目根目录下的 .env（SCORE_THRESHOLD_* 等由此全局生效）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库
    database_url: str = "postgresql://postgres:postgres@localhost:5432/jobmatchflow"
    # 为 True 时打印每条 SQL（极吵）；调试库问题时设 DATABASE_ECHO=true
    database_echo: bool = False

    # LLM - 入库前流水线统一 Gemini（去重、评分、JD 结构化提取等），与生成简历/求职信解耦
    gemini_api_key: str = ""
    gemini_model_name: str = "gemini-3.1-flash-lite"
    # 评分：显式上下文缓存（母简历 + scoring prompt 进 cache，每岗位只传 JD）。失败时自动回退非缓存。
    gemini_scoring_explicit_cache: bool = True
    # 缓存 TTL（秒）；正常会在该用户本轮打分结束后 delete，此值防进程崩溃遗留
    gemini_scoring_cache_ttl_seconds: int = 600

    claude_api_key: str = ""
    claude_model_name: str = "claude-sonnet-5"
    # 调试省钱开关：False 时 decision=generate 不会触发简历/求职信生成（打分本身照常进行、
    # 落库），AssetGenerationService 的两个入口（打分后立即生成 / Pipeline 全员生成）统一在
    # 这一处收口。上线前改回 True（或删掉这行走默认值）
    auto_generate_assets: bool = False
    # 可选：与主 GEMINI_API_KEY 区分的密钥；留空则回退 gemini_api_key
    gemini_pro_api_key: str = ""
    gemini_lite_api_key: str = ""

    # 跨源去重漏斗：本地职位文本向量（标题 + 完整 JD）+ 高相似/灰区调用 LLM
    job_dedup_embed_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    job_dedup_embed_sim_high: float = 0.97
    # 低于 low 直接保留；达到 high 或处于 [low, high] 都交给 LLM 判断（无 LLM 时保守保留）
    job_dedup_embed_sim_low: float = 0.87

    # 邮件通知
    resend_api_key: str = ""
    notification_email_from: str = ""
    notification_email_to: str = ""

    # 文件存储
    storage_provider: str = "local"
    local_storage_base_path: str = "data"

    # PDF：默认 chromium（Playwright）与 Chrome「打印→另存为 PDF」同一引擎；WeasyPrint 为另一套排版
    # 可选：chromium | weasyprint | weasyprint_first（先 Weasy 再 Chromium）
    pdf_engine: str = "chromium"
    # 无 @page 的 HTML 使用此纸张；模板内若有 @page（如求职信 A4）仍以 CSS 为准
    pdf_page_format: str = "a4"  # letter | a4；简历/求职信模板均为 A4，导出默认 A4
    # False：等同 Chrome 打印默认不勾选「背景图形」，白纸无灰边；True：保留彩色/灰底
    pdf_print_background: bool = False

    # 评分阈值（环境变量 SCORE_THRESHOLD_REVIEW / SCORE_THRESHOLD_GENERATE，全站与 ai/scoring 共用）
    score_threshold_review: int = 60
    score_threshold_generate: int = 80

    # 抓取 — 统一由 core.scrape_params 解析；勿在业务里硬编码
    # SCRAPE_LIMIT_PER_SEARCH：每国家/每轮上限（默认 500）
    scrape_limit_per_search: int = 500
    # SCRAPE_HOURS_OLD：时间窗（小时）；未设置时有效默认 24（见 scrape_params）
    scrape_hours_old: Optional[int] = None
    # 每次请求（JobSpy 搜索接口）之间随机等待秒数区间，避免固定间隔的请求节奏
    # 太像脚本；各时段之间有几个小时余量，不急着抓完，宁可等得久一点
    scrape_request_delay_min_sec: float = 5.0
    scrape_request_delay_max_sec: float = 15.0
    # 调试 HTTP 接口（api.debug_app）：若设置则请求需带 Header Authorization: Bearer <token>
    debug_api_token: Optional[str] = None

    # 打分：向量初筛用的本地 embedding 模型，与 job_dedup 用的模型分开配置——
    # 两个是不同的相似度任务（标题去重 vs 方向语义匹配），阈值/模型要能独立调
    scoring_embed_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    # 岗位向量与用户方向向量的最大余弦相似度：> pass 判 pass，< reject 判 reject，中间判 borderline
    # （pass/borderline 下游处理完全一样，只是三分类方便以后看分布调阈值）
    # 默认值未经真实数据验证，上线前应拿一批真实岗位/方向跑一遍看分布再调
    scoring_prefilter_sim_pass: float = 0.55
    scoring_prefilter_sim_reject: float = 0.3
    # 打分模型分档：三档各自的模型名前缀决定各自连去哪家 provider（见 ai/llm_factory.py），
    # 可以任意混搭，不是一个全局开关。当前实测结论：mid 固定用 Gemini，覆盖两处质量敏感的
    # 环节——Step2 JD 结构化提取（DeepSeek 在这一步会把复合技能要求拆得比 Gemini 细好几倍，
    # 系统性拖累最终分数，是实测唯一有具体质量差距的环节）和方向向量扩写
    # （load_active_direction_vectors，发生在 Step1 向量初筛之前）；cheap 只剩 Step5
    # 偏好加分（可选步骤）用 DeepSeek；match（Step3 逐项匹配）用 DeepSeek，更便宜、判断
    # 方向跟 Gemini 一致（更严格但不乱判）。
    # GLM 已评估后放弃（batch 接口不支持 glm-4.7-flash，同步接口限流卡得太死，可用吞吐太低）；
    # Qwen 先搁置，还没评估。
    scoring_model_cheap: str = "gemini-3.1-flash-lite"
    scoring_model_mid: str = "gemini-3.1-flash-lite"
    scoring_model_match: str = "gemini-3.1-flash-lite"

    # Qwen（阿里云百炼 DashScope，OpenAI 兼容 endpoint）；先搁置，还没评估
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model_name: str = "qwen3.5-flash"

    # DeepSeek（OpenAI 兼容 endpoint）；用 /beta 而非 /v1——只有 /beta 支持 strict function
    # calling（tools + strict:true 真正约束参数 schema，包括字段名和枚举值），/v1 下这个约束
    # 实测不生效；/beta 对普通 chat completions 也完全兼容，不影响其它调用
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/beta"
    deepseek_model_name: str = "deepseek-v4-flash"

    # 简历改写：全流程里唯一真正需要语言能力的一步，输入严格限定成"选中的几条经历 + must-have
    # 列表"（不塞整份素材库），贵在刀刃上；默认跟逐项匹配同档，评测后可单独调高
    resume_rewrite_model: str = "gemini-3.1-flash-lite"
    # LibreOffice headless 二进制路径（供 DOCX 渲染后做实际页数校验，见 renderer/docx_render.py）；
    # 找不到就跳过页数校验，只信任字符预算的保守估计，不阻断生成
    libreoffice_binary_path: str = "soffice"

    # 打分+生成长驻进程（scripts/run_matching_worker.py）：逐条处理之间的随机等待秒数区间
    # （避免把 Gemini 请求打得太密集）；所有用户都没有待处理岗位时，多久重新扫一轮
    matching_worker_delay_min_sec: float = 3.0
    matching_worker_delay_max_sec: float = 10.0
    matching_worker_idle_poll_sec: float = 300.0

    # 应用环境
    environment: str = "development"
    debug: bool = True

    # Jobs List：True 时列出库内全部岗位（含未评分、入库超过 3 天）；上线请保持 False
    jobs_list_debug_show_all: bool = False
    # 开发诊断：展示未评分/向量过滤岗位，并返回向量相似度；上线保持 False
    jobs_list_diagnostics: bool = False
    # gemini vs deepseek 打分模型对比面板（见 services/jobs_list_data.py 的
    # include_model_comparison）：只在专门做模型选型对比测试时临时打开，默认关闭——
    # 打分规则一旦定下来（见 SCORING_MODEL_CHEAP/MID/MATCH 的分工注释），主分数已经
    # 是当前配置下的真实结果，不需要常驻一个对比面板。
    jobs_list_model_comparison: bool = False

    # Auth: signing key for the httpOnly JWT session cookie (see api/auth_deps.py).
    # Must be a random secret in production; api/user_app.py refuses to start with
    # an empty key when environment == "production".
    secret_key: str = ""
    jwt_expires_days: int = 7

    # Admin bootstrap: used ONCE at startup (api/user_app.py) to create the first
    # admin User row if none exists yet. Not a login path itself — after bootstrap,
    # the admin logs in like any other user, via their real bcrypt-hashed password.
    admin_bootstrap_email: str = "admin@jobmatchflow.local"
    admin_bootstrap_password: str = ""

    # 用户 API（api.user_app）：每个 uvicorn worker 内，并发生成（简历+求职信）的最大协程数；
    # 总近似并发 ≈ workers × 本值；请与数据库连接池（pool_size+max_overflow）一起调大。
    api_generate_max_concurrent: int = 12
    # CORS：逗号分隔来源，如 https://app.example.com；留空则开发态允许 *
    api_cors_origins: str = ""


settings = Settings()
