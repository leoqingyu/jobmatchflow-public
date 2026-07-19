import time
from typing import Any, Literal

from core.exceptions import LLMError
from core.llm_usage_tracking import log_llm_usage
from core.logger import get_logger
from ai.llm_client import LLMClient, parse_llm_json_output

logger = get_logger(__name__)

# 429 退避重试：GLM/Qwen/DeepSeek 这类共享池 key 限流卡得比 Gemini 紧很多，测试时几十条
# 请求内就能连续撞限流；只对 429/连接中断重试（其它错误如 schema 字段不支持通常是 4xx 但不是
# 限流，应该让上层立刻拿到异常走 fallback，不该在这里死等）。
_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_BACKOFF_BASE_SEC = 5.0

# openai SDK 默认不设超时（能一直挂到 600s 才报错），实测请求偶尔会在网关/代理层卡住、
# 既不报错也不返回任何 chunk——加个显式超时，卡住的请求会被当成错误处理，进 429/连接中断
# 那套重试逻辑，而不是无限期挂起。
_REQUEST_TIMEOUT_SEC = 90.0

StructuredMode = Literal["json_schema", "strict_tools", "prompt_only"]


class OpenAICompatibleClient(LLMClient):
    """
    通用 OpenAI 兼容 Chat Completions 客户端：GLM（智谱）、Qwen（DashScope 兼容模式）、
    DeepSeek 都提供这套接口，公用一个实现，只是 base_url / api_key / model / 关闭思考模式的
    extra_body / 结构化输出方式不同（见 core/config.py + ai/llm_factory.py 的按 provider 分发）。

    没有 Gemini 那套显式缓存。structured_mode 控制 generate_structured 怎么拿到强约束输出：
    - "json_schema"：走 response_format=json_schema（默认），不支持时退回 generate_json。
    - "strict_tools"：走 tools + tool_choice 强制调用 + strict=True（DeepSeek 在 /beta 端点下
      实测支持，字段名/枚举值都能真正被约束住，比裸 prompt 靠谱很多）。
    - "prompt_only"：跳过强约束尝试，直接 generate_json——针对已验证过"两种强约束方式都不
      可靠"的 provider，省一次请求。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        extra_body: dict[str, Any] | None = None,
        structured_mode: StructuredMode = "json_schema",
    ):
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or "").strip()
        self._model = (model_name or "").strip()
        self._extra_body = extra_body or {}
        self._structured_mode = structured_mode
        self._client = None

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise LLMError("openai 包未安装，请运行 pip install openai") from e
            self._client = OpenAI(
                api_key=self._api_key, base_url=self._base_url, timeout=_REQUEST_TIMEOUT_SEC
            )
        return self._client

    def _messages(self, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def _stream_chunks(self, *, task_name: str, **kwargs: Any) -> list:
        """始终走 stream=True 请求：实测非流式请求在生成较长（20-30s+）时会被网关/连接
        中途掐断（Server disconnected without sending a response，DeepSeek 上很稳定复现），
        流式请求几秒内就有首个 chunk，不会撞上这个空闲超时。429/连接中断按指数退避重试；
        返回原始 chunk 列表，调用方按需要提取 content 或 tool_calls。

        单一真实调用点：_call 和两条 generate_structured 路径都经过这里，token 用量
        （stream_options include_usage 让最后一个 chunk 带上 usage，choices 为空，
        已有的 content/tool_calls 提取逻辑天然会跳过它）在这一处统一记录。"""
        from openai import APIConnectionError, RateLimitError

        client = self._get_client()
        attempt = 0
        while True:
            try:
                stream = client.chat.completions.create(
                    model=self._model,
                    extra_body=self._extra_body or None,
                    stream=True,
                    stream_options={"include_usage": True},
                    **kwargs,
                )
                chunks = list(stream)
                usage_chunk = next((c for c in chunks if getattr(c, "usage", None)), None)
                if usage_chunk is not None:
                    log_llm_usage(
                        task_name=task_name,
                        model_name=self._model,
                        prompt_tokens=usage_chunk.usage.prompt_tokens or 0,
                        completion_tokens=usage_chunk.usage.completion_tokens or 0,
                    )
                return chunks
            except (RateLimitError, APIConnectionError) as e:
                attempt += 1
                if attempt > _RATE_LIMIT_MAX_RETRIES:
                    raise
                kind = "限流(429)" if isinstance(e, RateLimitError) else "连接中断"
                wait_sec = _RATE_LIMIT_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "model=%s %s，第 %s/%s 次重试，等待 %.0fs: %s",
                    self._model, kind, attempt, _RATE_LIMIT_MAX_RETRIES, wait_sec, e,
                )
                time.sleep(wait_sec)

    def _create_completion_text(self, *, task_name: str, **kwargs: Any) -> str:
        chunks = self._stream_chunks(task_name=task_name, **kwargs)
        parts = [
            chunk.choices[0].delta.content
            for chunk in chunks
            if chunk.choices and chunk.choices[0].delta.content
        ]
        return "".join(parts).strip()

    def _create_completion_tool_args(self, *, task_name: str, **kwargs: Any) -> str:
        """强制单一 tool_call 场景下，把流式返回的 function.arguments 片段拼接成完整 JSON 字符串。"""
        chunks = self._stream_chunks(task_name=task_name, **kwargs)
        parts: list[str] = []
        for chunk in chunks:
            if not chunk.choices:
                continue
            for tc in chunk.choices[0].delta.tool_calls or []:
                if tc.function and tc.function.arguments:
                    parts.append(tc.function.arguments)
        return "".join(parts)

    def _call(self, system_prompt: str, user_prompt: str, task_name: str = "llm_call") -> str:
        try:
            return self._create_completion_text(
                task_name=task_name,
                messages=self._messages(system_prompt, user_prompt),
                temperature=0.2,
            )
        except Exception as e:
            raise LLMError(f"LLM 调用失败 model={self._model}: {e}") from e

    def _generate_structured_via_json_schema(
        self, task_name: str, user_prompt: str, system_prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        raw = self._create_completion_text(
            task_name=task_name,
            messages=self._messages(system_prompt, user_prompt),
            temperature=0.2,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": task_name, "schema": schema, "strict": True},
            },
        )
        return parse_llm_json_output(raw, task_name=task_name)

    def _generate_structured_via_strict_tool(
        self, task_name: str, user_prompt: str, system_prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        tool_name = f"submit_{task_name}"[:64]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": f"Submit the structured result for task '{task_name}'.",
                    "parameters": schema,
                    "strict": True,
                },
            }
        ]
        raw = self._create_completion_tool_args(
            task_name=task_name,
            messages=self._messages(system_prompt, user_prompt),
            temperature=0.2,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": tool_name}},
        )
        return parse_llm_json_output(raw, task_name=task_name)

    def generate_structured(
        self,
        task_name: str,
        user_prompt: str,
        system_prompt: str = "",
        *,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """429/连接中断在 _stream_chunks 里已经重试过，这里捕获到的是重试耗尽或其它错误
        （如字段/模式不支持），直接退回 generate_json（不强约束，靠 prompt 本身兜底）。"""
        if self._structured_mode == "prompt_only":
            return self.generate_json(task_name, user_prompt, system_prompt)
        try:
            if self._structured_mode == "strict_tools":
                return self._generate_structured_via_strict_tool(
                    task_name, user_prompt, system_prompt, schema
                )
            return self._generate_structured_via_json_schema(
                task_name, user_prompt, system_prompt, schema
            )
        except Exception as e:
            logger.warning(
                "task=%s model=%s structured_mode=%s 失败，退回 generate_json: %s",
                task_name, self._model, self._structured_mode, e,
            )
            return self.generate_json(task_name, user_prompt, system_prompt)
