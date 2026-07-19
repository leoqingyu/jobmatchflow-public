from abc import ABC, abstractmethod


class StorageAdapter(ABC):
    """文件存储抽象接口，支持本地和云端切换"""

    @abstractmethod
    def save_bytes(self, path: str, content: bytes) -> str:
        """保存文件，返回存储路径"""

    @abstractmethod
    def save_text(self, path: str, content: str) -> str:
        """保存文本文件，返回存储路径"""

    @abstractmethod
    def get_url(self, path: str) -> str:
        """获取文件访问 URL 或路径"""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """检查文件是否存在"""
