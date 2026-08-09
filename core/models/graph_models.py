"""graph 领域模型的兼容导出。"""

if __package__:
    from ..features.memory.graph.domain.models import (
        ExtractedGraph,
        GraphEdge,
        GraphEntry,
        GraphNode,
    )
else:
    from core.features.memory.graph.domain.models import (
        ExtractedGraph,
        GraphEdge,
        GraphEntry,
        GraphNode,
    )

__all__ = ["ExtractedGraph", "GraphEdge", "GraphEntry", "GraphNode"]
