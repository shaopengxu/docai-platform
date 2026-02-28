"""
嵌入模型管理
加载 BGE-M3 模型，提供批量编码接口
"""

from __future__ import annotations

import structlog
import numpy as np

from config.settings import settings

logger = structlog.get_logger()

_model = None


def get_embedding_model():
    """懒加载嵌入模型（首次调用时下载并加载，约 2GB）"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info(
            "Loading embedding model",
            model=settings.embedding_model_name,
            device=settings.embedding_device,
        )
        _model = SentenceTransformer(
            settings.embedding_model_name,
            device=settings.embedding_device,
        )
        logger.info(
            "Embedding model loaded",
            dimension=_model.get_sentence_embedding_dimension(),
        )
    return _model


def encode_texts(texts: list[str], show_progress: bool = False) -> list[list[float]]:
    """
    批量编码文本为向量

    Args:
        texts: 待编码的文本列表
        show_progress: 是否显示进度条

    Returns:
        向量列表，每个向量为 float list，维度 = settings.embedding_dimension
    """
    model = get_embedding_model()
    embeddings = model.encode(
        texts,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=show_progress,
        normalize_embeddings=True,  # L2 归一化，配合 cosine 距离
    )
    return embeddings.tolist()


def encode_single(text: str) -> list[float]:
    """编码单条文本"""
    return encode_texts([text])[0]
