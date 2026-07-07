"""Jargon 系统 — 群组黑话/方言自动发现。

两层架构：
  1. **JargonStatisticalFilter** — 零 LLM 成本的统计学预过滤器
  2. **JargonMiner** — 需要 LLM 的三步推断引擎
  3. **JargonQueryService** — 供 LLM Tool 使用的黑话查询服务
  4. **JargonStore** — 黑话持久化存储 (SQLite + FTS5)

所有子模块均为懒加载 — 仅在首次访问对应导出时才导入。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "JargonCandidate",
    "JargonMeaning",
    "JargonMiner",
    "JargonQueryService",
    "JargonStatisticalFilter",
    "JargonStats",
    "JargonStore",
]

_lazy: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    """Lazy-import submodules on first access."""
    global _lazy

    if name in _lazy:
        return _lazy[name]

    # ── models ──
    if name in ("JargonCandidate", "JargonMeaning", "JargonStats"):
        from .models import JargonCandidate, JargonMeaning, JargonStats  # noqa: F811
        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── statistical_filter ──
    if name == "JargonStatisticalFilter":
        from .statistical_filter import JargonStatisticalFilter  # noqa: F811
        _lazy["JargonStatisticalFilter"] = JargonStatisticalFilter
        return JargonStatisticalFilter

    # ── jargon_miner ──
    if name == "JargonMiner":
        from .jargon_miner import JargonMiner  # noqa: F811
        _lazy["JargonMiner"] = JargonMiner
        return JargonMiner

    # ── jargon_query ──
    if name == "JargonQueryService":
        from .jargon_query import JargonQueryService  # noqa: F811
        _lazy["JargonQueryService"] = JargonQueryService
        return JargonQueryService

    # ── jargon_store ──
    if name == "JargonStore":
        from .jargon_store import JargonStore  # noqa: F811
        _lazy["JargonStore"] = JargonStore
        return JargonStore

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
