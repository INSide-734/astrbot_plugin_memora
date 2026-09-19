"""话题分割使用的纯向量原语：余弦相似度、相似度矩阵与哈希兜底向量。

这些函数只做数值计算，不接触候选正文、身份或来源边界；话题聚类与消息切块
共用同一套相似度口径。缺少真实 `embed_fn` 时调用方须自行记录告警。
"""

from __future__ import annotations

from astrbot.api import logger


def cosine_sim(a: list[float], b: list[float]) -> float:
    """计算余弦相似度；维度不一致或零范数返回 0.0，不猜测语义。"""

    if not a or not b or len(a) != len(b):
        if len(a) != len(b) and a and b:
            logger.warning(
                "[余弦相似度] 向量维度不一致：len=%d vs len=%d；返回 0.0",
                len(a),
                len(b),
            )
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def similarity_matrix(embeddings: list[list[float]]) -> list[list[float]]:
    """生成相似度矩阵：对角线为 1.0，只填上三角。"""

    n = len(embeddings)
    mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        mat[i][i] = 1.0
        for j in range(i + 1, n):
            s = cosine_sim(embeddings[i], embeddings[j])
            mat[i][j] = s
    return mat


def dummy_embeddings(texts: list[str]) -> list[list[float]]:
    """在缺少 `embed_fn` 时使用的兜底方案：基于哈希生成伪向量。

    这些向量不具备真实语义，只是为了让聚类流程仍能继续运行。
    调用方在走到该路径时应记录告警日志。
    """
    import hashlib

    dim = 64
    out: list[list[float]] = []
    for t in texts:
        h = hashlib.sha256(t.encode()).digest()
        # 取前 dim 个字节并做归一化
        vec = [((b / 255.0) * 2.0 - 1.0) for b in h[:dim]]
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        out.append(vec)
    return out


__all__ = ["cosine_sim", "dummy_embeddings", "similarity_matrix"]
