from abc import ABC, abstractmethod
import json
import re
from typing import Any

from core.logger import get_logger
from core.exceptions import LLMError, LLMParseError

logger = get_logger(__name__)


def parse_llm_json_output(raw: str, *, task_name: str = "llm") -> dict[str, Any]:
    """将模型返回的文本解析为 JSON 对象（与 generate_json 同一套清洗/修复逻辑）。"""
    try:
        cleaned = LLMClient._extract_json(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e1:
            if "escape" in str(e1).lower() or "Invalid" in str(e1):
                repaired = _repair_invalid_json_string_escapes(cleaned)
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
            raise e1
    except json.JSONDecodeError as e:
        logger.warning("JSON 解析失败，原始输出: %s", raw[:200])
        raise LLMParseError(
            f"LLM 返回不是合法 JSON task={task_name}: {e}"
        ) from e


def _backslashes_before(s: str, idx: int) -> int:
    """紧邻 s[idx] 之前连续的反斜杠个数。"""
    j = idx - 1
    n = 0
    while j >= 0 and s[j] == "\\":
        n += 1
        j -= 1
    return n


def _repair_invalid_json_string_escapes(s: str) -> str:
    """
    LLM 常在 JSON 字符串里写 LaTeX/Windows 路径式反斜杠（如 \\%、\\(），标准 json 会报 Invalid \\escape。
    仅在「双引号字符串内部」把非法 \\x 改成 \\\\x（多一个反斜杠，变成合法字面量反斜杠 + 字符）。
    """
    out: list[str] = []
    i = 0
    in_string = False
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == '"':
            if not in_string:
                in_string = True
                out.append(ch)
                i += 1
                continue
            # 结束引号或字符串内的转义引号
            if _backslashes_before(s, i) % 2 == 1:
                out.append(ch)
                i += 1
                continue
            in_string = False
            out.append(ch)
            i += 1
            continue

        if in_string and ch == "\\":
            if i + 1 >= n:
                out.append("\\\\")
                i += 1
                continue
            nxt = s[i + 1]
            if nxt == "u" and i + 5 < n and re.match(
                r"u[0-9a-fA-F]{4}", s[i + 1 : i + 6]
            ):
                out.append(s[i : i + 6])
                i += 6
                continue
            if nxt in '"\\/bfnrt':
                out.append(ch)
                i += 1
                continue
            # 非法转义：再插入一个反斜杠
            out.append("\\\\")
            i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out)


class LLMClient(ABC):
    """LLM 调用抽象基类"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称，用于记录日志和数据库"""

    @property
    def supports_explicit_cache(self) -> bool:
        """是否支持显式上下文缓存（create_job_scoring_cache / generate_with_scoring_cache /
        delete_scoring_cache，见 ai/providers/gemini_model.py）。默认 False；调用方（如
        services/scoring_service.py）据此决定是否走缓存路径，不是每个 provider 都有这套能力。"""
        return False

    @abstractmethod
    def _call(self, system_prompt: str, user_prompt: str, task_name: str = "llm_call") -> str:
        """调用底层模型，返回原始文本。task_name 供实现方在 core/llm_usage_tracking.py
        记录 token 用量时打标签，不影响调用本身。"""

    def complete_text(
        self,
        task_name: str,
        user_prompt: str,
        system_prompt: str = "",
    ) -> str:
        """只取模型原始文本（不解析 JSON），用于 true/false 等简单协议。"""
        logger.debug(f"LLM text: task={task_name} model={self.model_name}")
        try:
            return self._call(system_prompt=system_prompt, user_prompt=user_prompt, task_name=task_name)
        except Exception as e:
            raise LLMError(f"LLM 调用失败 task={task_name}: {e}") from e

    def generate_json(
        self,
        task_name: str,
        user_prompt: str,
        system_prompt: str = "",
    ) -> dict[str, Any]:
        """
        调用模型并解析 JSON 返回值。
        所有结构化任务统一走这个接口。
        """
        logger.info(f"LLM call: task={task_name} model={self.model_name}")
        try:
            raw = self._call(system_prompt=system_prompt, user_prompt=user_prompt, task_name=task_name)
        except Exception as e:
            raise LLMError(f"LLM 调用失败 task={task_name}: {e}") from e

        return parse_llm_json_output(raw, task_name=task_name)

    def generate_structured(
        self,
        task_name: str,
        user_prompt: str,
        system_prompt: str = "",
        *,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """
        调用模型并按 JSON Schema 强约束返回值（如枚举字段不能被模型输出任意值，见
        ai/scoring.py 里 match_level 的用法）。

        基类默认实现只是退化成 generate_json（不强约束，纯 prompt 靠人品），子类应覆写此方法
        接入真正的 schema 约束能力（如 Gemini 的 response_schema）。退化路径打 warning，
        免得以后接了不支持 schema 的 provider（如 Claude）却没人发现质量在悄悄下降。
        """
        logger.warning(
            "task=%s model=%s 未实现真正的 schema 强约束，退化为 generate_json（结果不保证符合 schema）",
            task_name,
            self.model_name,
        )
        return self.generate_json(task_name, user_prompt, system_prompt)


    @staticmethod
    def _extract_json(text: str) -> str:
        """从可能包含 markdown 代码块的文本中提取 JSON"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        return text
