"""
LLM 客户端工厂，两条互不相关的路径：

- 打分：按模型名前缀决定 cheap/mid/match 三档（core/config.py::
  scoring_model_cheap/mid/match）各自具体连去哪家 provider——不是一个全局开关，三档可以
  分别指向不同厂商（例如 mid=gemini-3.1-flash-lite 走 Gemini，cheap/match=deepseek-v4-flash
  走 DeepSeek），按需混搭。只用于打分链路（services/scoring_service.py + 经历库引导抽取
  api/experience_routes.py）。新增 provider 只需要在 _PROVIDER_PREFIXES 里加一行——不是
  每加一家就改一次 scoring_service.py。

- 简历/求职信生成：按用户在 Settings 里选的 core.constants.GenerationModel
  （UserProfile.generation_model，"gemini" 或 "claude"）二选一，是运营侧配置之外、用户
  自己的生成偏好，跟上面打分那三档完全解耦。
"""

from typing import Callable

from core.config import settings
from core.constants import GenerationModel
from core.exceptions import LLMError
from ai.llm_client import LLMClient
from ai.providers.claude_model import ClaudeModelClient
from ai.providers.gemini_model import GeminiModelClient
from ai.providers.openai_compatible_model import OpenAICompatibleClient


def _build_gemini(model_name: str) -> LLMClient:
    return GeminiModelClient(model_name=model_name)


def _build_qwen(model_name: str) -> LLMClient:
    return OpenAICompatibleClient(
        api_key=settings.qwen_api_key,
        base_url=settings.qwen_base_url,
        model_name=model_name,
        # Qwen3 混合思考模型通过 enable_thinking=False 关闭思考，见 DashScope 兼容模式文档
        extra_body={"enable_thinking": False},
    )


def _build_deepseek(model_name: str) -> LLMClient:
    return OpenAICompatibleClient(
        api_key=settings.deepseek_api_key,
        # /beta 端点才支持 strict function calling（strict:true 严格约束 tool 参数 schema），
        # 见 settings.deepseek_base_url 的注释
        base_url=settings.deepseek_base_url,
        model_name=model_name,
        # 实测 deepseek-v4-flash 默认是开着思考的（响应里能看到 reasoning_content），
        # 跟最初以为的"非思考款"不一样；GLM 那套 thinking.type=disabled 写法对它也生效，
        # 关掉后 reasoning_content 消失、明显更快
        extra_body={"thinking": {"type": "disabled"}},
        # response_format=json_schema 被直接拒绝（400 unavailable），但 /beta 端点下
        # tools + tool_choice 强制调用 + strict:true 实测可靠（字段名/枚举值都被真正约束），
        # 比裸 prompt 靠谱很多
        structured_mode="strict_tools",
    )


# 按模型名前缀匹配 provider，顺序即优先级（暂无歧义前缀，先到先得）。
_PROVIDER_PREFIXES: list[tuple[str, Callable[[str], LLMClient]]] = [
    ("gemini", _build_gemini),
    ("deepseek", _build_deepseek),
    ("qwen", _build_qwen),
]


def get_scoring_llm_client(model_name: str) -> LLMClient:
    """按 model_name 的前缀推断 provider 并构造对应 LLMClient；model_name 通常来自
    settings.scoring_model_cheap/mid/match——三档各自的模型名前缀决定各自的 provider，
    互不影响，可以任意混搭（见本文件头 docstring）。"""
    name = (model_name or "").strip().lower()
    for prefix, builder in _PROVIDER_PREFIXES:
        if name.startswith(prefix):
            return builder(model_name)
    raise LLMError(
        f"无法从模型名 {model_name!r} 推断 provider，已知前缀: {[p for p, _ in _PROVIDER_PREFIXES]}"
    )


def get_generation_llm_client(model_choice: str | None) -> LLMClient:
    """简历/求职信生成用的 LLM 客户端：model_choice 通常来自
    UserProfile.generation_model（缺省当 "gemini" 处理，兼容还没存过这个字段的老用户/
    user_id 未知的调用方）。跟 get_scoring_llm_client 是两条不相关的路径，见文件头。"""
    choice = (model_choice or GenerationModel.GEMINI.value).strip().lower()
    if choice == GenerationModel.CLAUDE.value:
        if not settings.claude_api_key:
            raise LLMError("未配置 CLAUDE_API_KEY，无法用 Claude 生成简历/求职信——请在环境变量里设置，或在 Settings 切回 Gemini")
        return ClaudeModelClient(api_key=settings.claude_api_key, model_name=settings.claude_model_name)
    return GeminiModelClient(model_name=settings.resume_rewrite_model)
