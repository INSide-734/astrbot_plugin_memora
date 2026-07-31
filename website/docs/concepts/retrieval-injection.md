# 检索与注入

Memora 将“找到相关记忆”和“安全交给当前模型请求”分成两个边界，避免检索结果直接、无预算地进入上下文。

## 多路检索

| 路径 | 适合内容 |
|---|---|
| BM25 / FTS5 | 名称、关键词和精确文本。 |
| FAISS | 不同表达方式下的语义相关性。 |
| 图关系 | 人物、实体、来源和多跳关联。 |

候选结果经过 RRF 或混合评分融合，可再执行关系扩展和重排序。最终结果继续按会话、用户、群组、角色、隐私级别、有效期和 revision 过滤。

## Relation 与 Projection

启用记忆演化后，系统可以生成有 source revision 证据的 Relation 和 Projection。它们只解释 canonical memory，不能形成第二套 canonical 记录或独立 `doc_id`。

模型可见的 Projection metadata 只包含 `type`、`summary` 和 `confidence`；内部 source mapping、revision、scope、隐私和 job 信息不得进入模型上下文。

## 注入路由

Memora 支持三种路由模式：

- `manual`：使用管理员选择的固定预设。
- `auto`：根据请求信号和 Provider 能力自动选择策略。
- `hybrid`：在人工边界内使用自动决策。

内置预设包括 Tool First、Low Cost、Balanced 和 Quality。普通记忆受条数、字符数和全局硬预算共同限制。

## 原子请求变更

`InjectionExecutor` 先完成候选选择、分层格式化、Prompt 防护和 Provider 适配，再一次性应用请求变更。任一构建或交付阶段失败时，请求保持原样。

动态记忆不会写入 System Prompt，也不会永久改写会话模板。

## 安全观测

策略预览、决策历史和召回 trace 只记录允许的标量摘要，不持久化 query、Prompt、模型回复、记忆正文、ID 列表、原始身份或 Provider 凭据。

下一步可查看[诊断与评测](/operations/diagnostics-evaluation)和[配置入门](/guide/configuration)。
