from pathlib import Path

from core.config import settings
from core.exceptions import StorageError
from storage.base import StorageAdapter


class LocalStorageAdapter(StorageAdapter):
    def __init__(self, base_path: str = None):
        self.base = Path(base_path or settings.local_storage_base_path)

    def save_bytes(self, path: str, content: bytes) -> str:
        full_path = self.base / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            full_path.write_bytes(content)
        except OSError as e:
            raise StorageError(f"本地文件写入失败: {path}") from e
        return str(full_path)

    def save_text(self, path: str, content: str) -> str:
        return self.save_bytes(path, content.encode("utf-8"))

    def get_url(self, path: str) -> str:
        return str(self.base / path)

    def exists(self, path: str) -> bool:
        return (self.base / path).exists()
