"""查询意图检测关键词 — 用于双路由检索器动态权重调整。

中文和英文关键词列表，按意图类别分组：
- relation: 关系查询
- temporal: 时间查询
- factual: 事实/解释查询

如需扩展，直接编辑此文件即可。
"""

RELATION_TERMS: tuple[str, ...] = (
    "谁",
    "和谁",
    "关系",
    "认识",
    "朋友",
    "同事",
    "家人",
    "父母",
    "妈妈",
    "爸爸",
    "老师",
    "同学",
    "来自",
    "来源",
    "依赖",
    "partner",
    "friend",
    "relationship",
    "with whom",
)

TEMPORAL_TERMS: tuple[str, ...] = (
    "上次",
    "昨天",
    "前天",
    "刚才",
    "之前",
    "什么时候",
    "哪天",
    "最近",
    "last time",
    "yesterday",
    "recently",
    "when",
)

FACTUAL_TERMS: tuple[str, ...] = (
    "是什么",
    "什么是",
    "解释",
    "定义",
    "怎么",
    "如何",
    "why",
    "what is",
    "explain",
    "define",
    "how to",
)
