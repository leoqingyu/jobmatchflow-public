class JobMatchFlowBaseError(Exception):
    """所有自定义异常的基类"""


class ConfigurationError(JobMatchFlowBaseError):
    """配置错误"""


class DatabaseError(JobMatchFlowBaseError):
    """数据库操作错误"""


class ScraperError(JobMatchFlowBaseError):
    """抓取器错误"""


class NormalizationError(JobMatchFlowBaseError):
    """岗位标准化错误"""


class LLMError(JobMatchFlowBaseError):
    """LLM 调用错误"""


class LLMParseError(LLMError):
    """LLM 返回结果解析错误"""


class ScoringError(JobMatchFlowBaseError):
    """评分错误"""


class GenerationError(JobMatchFlowBaseError):
    """生成资产错误"""


class RenderError(JobMatchFlowBaseError):
    """PDF/HTML 渲染错误"""


class StorageError(JobMatchFlowBaseError):
    """文件存储错误"""


class NotificationError(JobMatchFlowBaseError):
    """通知发送错误"""


class UserNotFoundError(JobMatchFlowBaseError):
    """用户不存在"""


class JobNotFoundError(JobMatchFlowBaseError):
    """岗位不存在"""
