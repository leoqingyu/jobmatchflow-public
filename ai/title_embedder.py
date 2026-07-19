"""职位文本本地向量：用于跨源去重漏斗（sentence-transformers，惰性加载）。"""

from __future__ import annotations

from typing import Optional

import numpy as np

from core.logger import get_logger

logger = get_logger(__name__)


def _canon_embed_text(text: Optional[str]) -> str:
    """空标题用占位，避免 encode 异常。"""
    s = (text or "").strip()
    return s if s else " "


class TitleEmbedder:
    """按职位文本缓存向量；encode 使用 L2 归一化，余弦相似度 = 点积。"""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self._cache: dict[str, np.ndarray] = {}

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "未安装 sentence-transformers，无法使用标题向量去重。请执行: pip install sentence-transformers"
            ) from e
        logger.info("加载本地 Embedding 模型: %s（首次可能下载权重）", self.model_name)
        self._model = SentenceTransformer(self.model_name)

    def embed_one(self, title: Optional[str]) -> np.ndarray:
        """返回 shape (d,) 的单位方向向量（近似）。"""
        return self.embed_many([title])[0]

    def embed_many(self, titles: list[Optional[str]]) -> np.ndarray:
        """titles 与返回矩阵逐行对应；内部对相同标题去重批量编码。"""
        self._ensure_model()
        if not titles:
            return np.zeros((0, 0), dtype=np.float32)

        keys = [_canon_embed_text(t) for t in titles]
        missing: list[str] = []
        for k in keys:
            if k not in self._cache and k not in missing:
                missing.append(k)

        if missing:
            assert self._model is not None
            mat = self._model.encode(
                missing,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            for i, k in enumerate(missing):
                self._cache[k] = np.asarray(mat[i], dtype=np.float32)

        rows = [self._cache[k] for k in keys]
        return np.stack(rows, axis=0)

    @staticmethod
    def _full_text_chunks(text: Optional[str], max_chars: int = 1200) -> list[str]:
        s = _canon_embed_text(text)
        if len(s) <= max_chars:
            return [s]
        # 适配本地模型有限的 token window：分块编码后取平均方向，避免只看 JD 开头。
        return [s[i : i + max_chars] for i in range(0, len(s), max_chars)]

    def embed_full_many(self, texts: list[Optional[str]]) -> np.ndarray:
        """对完整文本分块编码并平均，返回单位方向向量。"""
        self._ensure_model()
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)

        keys = [_canon_embed_text(t) for t in texts]
        out: list[np.ndarray | None] = [None] * len(keys)
        missing_chunks: list[str] = []
        chunk_map: dict[str, list[str]] = {}
        for key in keys:
            if key in self._cache:
                continue
            chunks = self._full_text_chunks(key)
            chunk_map[key] = chunks
            for chunk in chunks:
                if chunk not in self._cache and chunk not in missing_chunks:
                    missing_chunks.append(chunk)

        if missing_chunks:
            assert self._model is not None
            mat = self._model.encode(
                missing_chunks,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            for i, chunk in enumerate(missing_chunks):
                self._cache[chunk] = np.asarray(mat[i], dtype=np.float32)

        for i, key in enumerate(keys):
            if key not in self._cache:
                vectors = [self._cache[c] for c in chunk_map[key]]
                v = np.mean(np.stack(vectors, axis=0), axis=0)
                norm = float(np.linalg.norm(v))
                self._cache[key] = v / norm if norm else v
            out[i] = self._cache[key]
        return np.stack([v for v in out if v is not None], axis=0)
