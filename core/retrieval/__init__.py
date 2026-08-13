"""检索系统模块旧路径兼容导出。

真实实现已迁至 ``core.features.retrieval``；本模块只保留单实现 re-export，
供尚未切换到 feature 路径的历史调用方与契约测试使用。
"""

from ..features.retrieval.bm25_retriever import BM25Retriever
from ..features.retrieval.dual_route_retriever import DualRouteRetriever
from ..features.retrieval.graph_keyword_retriever import GraphKeywordRetriever
from ..features.retrieval.graph_retriever import GraphRetriever
from ..features.retrieval.graph_vector_retriever import GraphVectorRetriever
from ..features.retrieval.hybrid_retriever import HybridRetriever
from ..features.retrieval.rrf_fusion import (
    BM25Result,
    FusedResult,
    HybridResult,
    RRFFusion,
    VectorResult,
)
from ..features.retrieval.vector_retriever import VectorRetriever

__all__ = [
    "RRFFusion",
    "BM25Result",
    "VectorResult",
    "FusedResult",
    "HybridResult",
    "BM25Retriever",
    "VectorRetriever",
    "HybridRetriever",
    "GraphKeywordRetriever",
    "GraphVectorRetriever",
    "GraphRetriever",
    "DualRouteRetriever",
]
