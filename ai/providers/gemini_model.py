import time
from typing import Any

from core.config import settings
from core.exceptions import LLMError
from core.llm_usage_tracking import log_llm_usage
from core.logger import get_logger
from ai.llm_client import LLMClient, parse_llm_json_output

logger = get_logger(__name__)


def _api_model_id(model: str) -> str:
    """Gemini API / CachedContent 使用的 model 资源 id。"""
    m = (model or "").strip()
    if m.startswith("models/"):
        return m
    return f"models/{m}"


class GeminiModelClient(LLMClient):
    """
    Google Gemini：全站统一 LLM（去重、评分、JD 提取、简历/求职信）。
    模型名与密钥来自 GEMINI_MODEL_NAME、GEMINI_API_KEY。
    需要安装：pip install google-genai
    """

    def __init__(self, model_name: str | None = None, api_key: str | None = None):
        self._model = model_name or settings.gemini_model_name
        self._api_key = (
            settings.gemini_api_key if api_key is None else (api_key or "")
        )
        self._client = None

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def supports_explicit_cache(self) -> bool:
        return True

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai

                self._client = genai.Client(api_key=self._api_key)
            except ImportError as e:
                raise LLMError("google-genai 包未安装，请运行 pip install google-genai") from e
        return self._client

    def _generate(
        self,
        task_name: str,
        user_prompt: str,
        system_prompt: str = "",
        *,
        schema: dict[str, Any] | None = None,
        cached_content: str | None = None,
    ):
        """单一真实调用点：_call / generate_structured / generate_with_scoring_cache 都
        经过这里，token 用量在这一处统一记录，不用在每个调用方法里分别埋点。"""
        client = self._get_client()
        from google.genai import types

        config_kwargs: dict[str, Any] = {"temperature": 0.2}
        if cached_content:
            config_kwargs["cached_content"] = cached_content
        else:
            config_kwargs["system_instruction"] = system_prompt if system_prompt else None
        if schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = schema
        config = types.GenerateContentConfig(**config_kwargs)

        response = client.models.generate_content(
            model=_api_model_id(self._model),
            contents=user_prompt,
            config=config,
        )
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            log_llm_usage(
                task_name=task_name,
                model_name=self.model_name,
                prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                completion_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            )
        return response

    def _call(self, system_prompt: str, user_prompt: str, task_name: str = "llm_call") -> str:
        response = self._generate(task_name, user_prompt, system_prompt)
        return response.text or ""

    def generate_structured(
        self,
        task_name: str,
        user_prompt: str,
        system_prompt: str = "",
        *,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """用 Gemini 原生 response_schema 做 JSON Schema 强约束（含 enum），锁死如 match_level
        这类字段不能被模型输出任意值——不是靠 prompt 教育模型，是解码时就不允许。"""
        logger.info(f"LLM call (structured): task={task_name} model={self.model_name}")
        try:
            response = self._generate(task_name, user_prompt, system_prompt, schema=schema)
        except Exception as e:
            raise LLMError(f"LLM 调用失败 task={task_name}: {e}") from e
        return parse_llm_json_output(response.text or "", task_name=task_name)

    def create_job_scoring_cache(
        self,
        *,
        system_instruction: str,
        candidate_context_plain: str,
        user_id: int,
    ) -> str:
        """
        创建评分用显式缓存：system = 打分 prompt，contents = 候选人事实+经历单元固定块。
        返回 cachedContents 资源 name，供 generate_with_scoring_cache 使用；用完须 delete_scoring_cache。
        """
        from google.genai import types

        client = self._get_client()
        candidate_block = (
            "## Scoring context (fixed for this session)\n"
            "Each following user message contains ONLY one job's requirement list. "
            "Use this context and your system instructions to judge each requirement.\n\n"
            f"{candidate_context_plain}"
        )
        ttl_sec = max(60, int(settings.gemini_scoring_cache_ttl_seconds))
        display = f"jobmatchflow_scoring_u{user_id}_{int(time.time())}"
        cache = client.caches.create(
            model=_api_model_id(self._model),
            config=types.CreateCachedContentConfig(
                display_name=display[:120],
                system_instruction=system_instruction,
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=candidate_block)],
                    )
                ],
                ttl=f"{ttl_sec}s",
            ),
        )
        if not cache.name:
            raise LLMError("创建评分缓存失败：未返回 cache.name")
        return cache.name

    def generate_with_scoring_cache(
        self,
        cache_name: str,
        jd_user_prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        task_name: str = "scoring_match_cached",
    ) -> str:
        """
        在已有显式缓存上生成文本（不再传 system / 候选人上下文）。

        schema 非空时按 response_schema 强约束解码——cached_content 与 response_schema 能否在
        同一次请求里共存未经这台机器验证过（拿不到 API key），调用方（ai/scoring.py）出错时应
        对这一条岗位回退成非缓存 + 全量上下文重试，不要整批放弃缓存。
        """
        response = self._generate(task_name, jd_user_prompt, schema=schema, cached_content=cache_name)
        return response.text or ""

    def delete_scoring_cache(self, cache_name: str) -> None:
        """删除显式缓存；失败仅记日志，不抛给业务。"""
        if not cache_name:
            return
        from core.logger import get_logger

        log = get_logger(__name__)
        try:
            self._get_client().caches.delete(name=cache_name)
            log.debug("已删除评分显式缓存 name=%s", cache_name)
        except Exception as e:
            log.warning("删除评分显式缓存失败 name=%s: %s", cache_name, e)

