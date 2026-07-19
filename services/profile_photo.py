"""用户证件照：按 user_id 存一份 JPG，供定制简历模板渲染头像。"""
from __future__ import annotations

import base64
from pathlib import Path

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

SUBDIR = "profile_photos"
MAX_BYTES = 8 * 1024 * 1024  # 8MB


def profile_photo_path(user_id: int) -> Path:
    root = Path(settings.local_storage_base_path).resolve()
    d = root / SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"u{int(user_id)}.jpg"


def has_profile_photo(user_id: int) -> bool:
    return profile_photo_path(user_id).is_file()


def _validate_jpeg(data: bytes) -> None:
    if len(data) < 3:
        raise ValueError("文件过小，请上传有效的 JPG 图片")
    if len(data) > MAX_BYTES:
        raise ValueError("图片过大（上限 8MB）")
    if data[0:3] != b"\xff\xd8\xff":
        raise ValueError("不是有效的 JPEG 文件（需以 .jpg/.jpeg 保存的标准 JPEG）")


def save_profile_photo_jpeg(user_id: int, file_bytes: bytes) -> str:
    """校验 JPEG 并写入磁盘，返回绝对路径。"""
    _validate_jpeg(file_bytes)
    path = profile_photo_path(user_id)
    path.write_bytes(file_bytes)
    logger.info("已保存用户证件照 user_id=%s path=%s", user_id, path)
    return str(path.resolve())


def get_profile_photo_base64(user_id: int) -> str | None:
    """读取证件照为纯 base64（无 data: 前缀），供 cv_templet 使用。"""
    path = profile_photo_path(user_id)
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        if len(raw) < 3 or raw[0:3] != b"\xff\xd8\xff":
            logger.warning("证件照文件损坏或非 JPEG，已忽略: %s", path)
            return None
        return base64.b64encode(raw).decode("ascii")
    except OSError as e:
        logger.warning("读取证件照失败 %s: %s", path, e)
        return None
