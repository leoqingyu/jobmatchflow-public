from typing import Any

from core.config import settings
from core.exceptions import LLMError
from core.llm_usage_tracking import log_llm_usage
from ai.llm_client import LLMClient


def _strict_schema(schema: Any) -> Any:
    """递归给每一层 object 类型的 schema 节点补上 additionalProperties: false——Claude 的
    strict tool use 要求这个才会真正按 schema 校验 tool_use.input（见 generate_structured）。
    不在原地改：这些 schema 常量（ai/resume_rewrite.py 的 _BULLET_SCHEMA 等）是 Gemini/Claude
    共用的，Gemini 的 response_schema 不认识 additionalProperties，原样传可能报错或被忽略；
    只在传给 Claude 前深拷贝加工一份，不动共享的原始 schema。"""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if out.get("type") == "object":
        out.setdefault("additionalProperties", False)
        props = out.get("properties")
        if isinstance(props, dict):
            out["properties"] = {k: _strict_schema(v) for k, v in props.items()}
    if out.get("type") == "array":
        items = out.get("items")
        if isinstance(items, dict):
            out["items"] = _strict_schema(items)
    return out


class ClaudeModelClient(LLMClient):
    """Anthropic Messages API：用于简历 / 求职信 JSON 生成。"""

    def __init__(self, api_key: str, model_name: str | None = None):
        self._api_key = (api_key or "").strip()
        self._model = (model_name or settings.claude_model_name).strip()
        self._client = None

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as e:
                raise LLMError("anthropic 包未安装，请运行 pip install anthropic") from e
            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def _send(
        self,
        task_name: str,
        user_prompt: str,
        system_prompt: str = "",
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ):
        """单一真实调用点：_call 和 generate_structured 都经过这里，token 用量在这一处
        统一记录。不传 temperature：Claude Sonnet 5 / Opus 4.7+ / Fable 5 只接受默认值，
        传非默认值直接 400 invalid_request_error——省着用 prompt 本身控制输出的确定性。"""
        client = self._get_client()
        kwargs: dict[str, Any] = dict(
            model=self._model,
            max_tokens=16384,
            system=system_prompt or "",
            messages=[{"role": "user", "content": user_prompt}],
        )
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        msg = client.messages.create(**kwargs)
        usage = getattr(msg, "usage", None)
        if usage is not None:
            log_llm_usage(
                task_name=task_name,
                model_name=self.model_name,
                prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
                completion_tokens=getattr(usage, "output_tokens", 0) or 0,
            )
        return msg

    def _call(self, system_prompt: str, user_prompt: str, task_name: str = "llm_call") -> str:
        msg = self._send(task_name, user_prompt, system_prompt)
        parts: list[str] = []
        for block in msg.content:
            if getattr(block, "type", None) == "text" and getattr(block, "text", None):
                parts.append(block.text)
        return "".join(parts).strip()

    def generate_structured(
        self,
        task_name: str,
        user_prompt: str,
        system_prompt: str = "",
        *,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """用 tool_choice 强制一次 tool 调用拿到符合 schema 的结构化输出——跟
        ai/providers/openai_compatible_model.py 对 DeepSeek 用的 strict_tools 模式同一个
        思路，不退化成基类默认的裸 prompt JSON（那样枚举值/必填字段都没有硬约束，见
        ai/llm_client.py::LLMClient.generate_structured 的降级警告）。

        strict=True 是必须的，不是锦上添花：实测不带它时，Claude 偶尔会把整个答案套一层
        JSON 字符串塞进第一个必填字段里（比如 {"categories": "{\\"categories\\": [...]}"}），
        `block.input` 表面上"看起来是 dict"、类型检查也过得去，但字段值本身是错的，会让
        下游按 schema 读字段时全部落空、静默退化成兜底分支（技能全塞进 Other 之类）。
        strict=True + schema 补 additionalProperties: false（见 _strict_schema）才会让
        Claude 真正按 schema 结构返回。"""
        tool_name = "emit_result"
        msg = self._send(
            task_name,
            user_prompt,
            system_prompt,
            tools=[
                {
                    "name": tool_name,
                    "description": "Emit the structured result for this task.",
                    "input_schema": _strict_schema(schema),
                    "strict": True,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                return block.input
        raise LLMError(f"Claude 未返回预期的 tool_use 结果 task={task_name}")
