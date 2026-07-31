# 配置参考

Memora 的配置由仓库根目录的 [`_conf_schema.json`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/_conf_schema.json) 定义。以下详细页按当前实现逐项解释每个叶字段的完整路径、类型、默认值、选项、范围、实际作用和修改注意事项。

## 阅读方式

- **配置项**使用完整点路径，例如 `recall_engine.injection_routing_mode`。
- **默认值**是新配置采用的 Schema 默认值；已有配置不会因为文档构建而被改写。
- **选项与范围**来自 Schema 的 `options`、`min`、`max` 和特殊编辑器声明。
- `""` 表示空字符串，`[]` 表示空列表，`null` 表示未指定具体值。

::: tip 推荐编辑入口
优先在 AstrBot Dashboard 的 Memora 配置页面修改。Dashboard 会提供字段控件、Schema 校验和 revision 冲突保护；不要直接编辑生成的参考页。
:::

## 分组导航

| 主题 | 包含的配置域 | 适合查找 |
|---|---|---|
| [基础运行与记忆生成](/reference/configuration/basic) | `bot_language`、`provider_settings`、`session_manager`、`reflection_engine`、`prompt_templates` 等 | Provider、会话捕获、反思和提示词。 |
| [召回、注入与索引](/reference/configuration/retrieval) | `recall_engine`、`graph_memory`、`reranker`、`index_management` 等 | 检索数量、注入路由、融合、过滤和索引。 |
| [记忆生命周期](/reference/configuration/lifecycle) | `memory_evolution`、`topic_segmentation`、`atom_quality_filter`、`forgetting_agent` 等 | 演化、分割、质量、衰减、压缩和遗忘。 |
| [智能与内容增强](/reference/configuration/features) | `user_profile`、`knowledge_base`、`notes`、`agent_tools` 等 | 画像、知识、笔记、关系、学习和工具。 |
| [运维、可靠性与安全](/reference/configuration/operations) | `backup_settings`、`cost_control`、`update_settings`、`security` 等 | 迁移、重建、备份、更新、成本和安全。 |

## 修改与生效

1. 在 Dashboard 打开 Memora 配置页面并修改字段。
2. 保存前检查关联字段，尤其是总开关、模式、阈值和预算。
3. 保存时若出现 revision 冲突，重新加载远端配置后再明确合并，不要覆盖其他管理员的修改。
4. 涉及 Provider、后台 worker 或启动期组件的设置时，按 Dashboard 提示重新加载插件或重启 AstrBot。
5. 使用 `/memora status`、`/memora health` 或对应 Dashboard 页面确认配置已生效。

配置思路和推荐起点见[配置入门](/guide/configuration)，配置导致启动、召回或运维异常时见[故障排除](/operations/troubleshooting)。

希望让插件整体偏向高质量、均衡或低成本时，使用[质量与成本档位指南](/guide/tuning-profiles)，不要只修改一组同名预设。

::: danger 不要只改 Schema
开发新配置字段时，必须同步 `_conf_schema.json`、Pydantic 模型、运行时读取、Dashboard 类型和默认值、契约验证以及这里的人工说明。Schema 本身不会补齐运行时实现。
:::
