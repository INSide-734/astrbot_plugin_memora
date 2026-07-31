# 配置入门

本页说明首次使用最需要确认的配置域。保存配置时以 Dashboard 显示的 Schema、字段校验和 revision 冲突结果为准。

## Provider

`provider_settings` 连接 AstrBot 中已有的模型配置：

- `embedding_provider_id`：生成向量并支持 FAISS 语义检索；留空时使用 AstrBot 默认 Embedding Provider。
- `llm_provider_id`：执行总结、重要性判断和反思；留空时使用 AstrBot 默认 LLM Provider。

Provider 暂时不可用时，Memora 会安全等待，而不是让聊天主链路失败。

## 召回与注入

`recall_engine` 控制检索数量、融合策略、预算和记忆交付方式。新安装建议先保持以下组合：

| 字段 | 建议起点 | 作用 |
|---|---|---|
| `injection_routing_mode` | `manual` | 使用确定性的人工预设路由。 |
| `injection_manual_preset` | `balanced` | 在质量、延迟和上下文预算之间取平衡。 |
| `injection_delivery_override` | `auto` | 根据当前 Provider 能力选择临时交付方式。 |

动态记忆只在当前请求中临时提供，不写入 System Prompt。修改策略前建议先在 Dashboard 的 Injection 页面查看策略预览。

## 记忆演化

`memory_evolution.enabled` 默认关闭。关闭时不启动演化 worker，也不影响 canonical 记忆写入、检索和注入。

启用后，worker 可以生成带 source revision 证据的 Relation 和 Projection。它们是派生解释数据，不能替代 canonical memory，也不能绕过 scope、privacy、validity 和 role 校验。

## 常用配置域

| 配置域 | 用途 |
|---|---|
| `session_manager` | 会话缓存、历史容量和群聊捕获。 |
| `reflection_engine`、`topic_segmentation` | 反思、总结与话题处理。 |
| `filtering_settings`、`reranker` | 隔离过滤与最终重排序。 |
| `knowledge_base`、`notes`、`user_profile` | 知识、笔记和画像能力。 |
| `backup_settings`、`update_settings` | 备份保留、在线检查与下载。 |
| `security`、`dashboard`、`agent_tools` | 安全策略、管理面板和 Agent 工具。 |

完整分组见[配置参考](/reference/configuration)。

需要在质量、延迟和 Provider 费用之间选择完整方案时，阅读[质量与成本档位指南](/guide/tuning-profiles)。

## 保存与冲突

Dashboard 先构建完整候选配置并执行字段校验，再使用 revision 保护写回。远端配置在编辑期间发生变化时，保存会返回冲突；本地草稿应保持不变，管理员需要明确重新加载或基于新 revision 重试。

::: danger 不要恢复已删除字段
`recall_engine.injection_method` 已移除，不提供旧字段兼容迁移。升级后应使用路由模式、人工预设和 delivery override。
:::
