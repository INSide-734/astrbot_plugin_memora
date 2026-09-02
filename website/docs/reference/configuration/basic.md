---
aside: false
pageClass: config-reference-page
---

# 基础运行与记忆生成配置

本页逐项说明模型、语言、会话捕获、反思和提示词相关配置。首次部署通常只需确认两个 Provider；其余字段应在明确遇到容量、上下文或提示词需求时再调整。

表中的默认值、选项和范围已与当前 `_conf_schema.json` 核对；“说明”同时解释字段的运行作用和适用场景。

## 机器人回复语言

配置域：`"bot_language"`。设置插件命令和状态回复的语言。zh=中文（默认），en=English，ru=Русский。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"bot_language"` | `"string"` | `"zh"` | 可选：`"zh"` / `"en"` / `"ru"` | 机器人回复语言<br><small>设置插件命令和状态回复的语言。zh=中文（默认），en=English，ru=Русский。</small> |

## 调试模式（问题报告）

配置域：`"debug"`。仅在用户报告问题时开启。会输出不含对话、记忆、身份或 Provider 敏感信息的详细诊断日志，并写入轮转文件。问题复现后请关闭。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"debug"` | `"bool"` | `false` | - | 调试模式（问题报告）<br><small>仅在用户报告问题时开启。会输出不含对话、记忆、身份或 Provider 敏感信息的详细诊断日志，并写入轮转文件。问题复现后请关闭。</small> |

## 模型提供商

配置域：`"provider_settings"`。配置记忆系统使用的 AI 模型

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"provider_settings.embedding_provider_id"` | `"string"` | `""` | - | Embedding 模型 ID<br><small>用于生成记忆向量的 Embedding 模型 ID。留空使用 AstrBot 默认。可在 AstrBot 后台「LLM 配置」中查看已有的 Embedding Provider ID（如 openai_embedding）。</small> |
| `"provider_settings.llm_provider_id"` | `"string"` | `""` | 通过 Dashboard Provider 选择器设置 | LLM 模型 ID<br><small>用于总结对话、评估记忆重要性的大语言模型 ID。留空使用 AstrBot 默认。推荐使用推理能力较强的模型。</small> |

## 会话管理

配置域：`"session_manager"`。管理用户会话状态和对话历史的缓存行为

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"session_manager.enable_full_group_capture"` | `"bool"` | `true` | - | 捕获群聊所有消息<br><small>是否捕获群聊中的所有消息（包括非 @Bot 的消息）。开启后群聊中每条消息都会被记录，有助于构建更完整的群聊上下文。</small> |
| `"session_manager.max_sessions"` | `"int"` | `100` | 最小值：`1`<br>最大值：`10000` | 最大缓存会话数<br><small>内存中同时保留的最大会话数（LRU 淘汰）。超出后最久未使用的会话从缓存移除，但数据仍保存在数据库中。</small> |
| `"session_manager.session_ttl"` | `"int"` | `3600` | 最小值：`60`<br>最大值：`86400` | 会话空闲超时（秒）<br><small>会话超过此时间无活动后从缓存移除。默认 3600（1 小时）。</small> |
| `"session_manager.context_window_size"` | `"int"` | `50` | 最小值：`1`<br>最大值：`1000` | 上下文窗口大小（条）<br><small>传给 LLM 的最大历史消息条数。越大上下文越丰富，但消耗 token 越多。建议 20-100。</small> |
| `"session_manager.max_messages_per_session"` | `"int"` | `1000` | 最小值：`100`<br>最大值：`10000` | 单会话最大历史消息数<br><small>数据库中每个会话保留的历史消息上限。超过后只会删除已经完成总结的最旧消息，避免丢失未总结上下文。</small> |
| `"session_manager.cleanup_batch_size"` | `"int"` | `50` | 最小值：`1`<br>最大值：`1000` | 历史消息批量清理数量<br><small>会话历史超过上限后，每次至少尝试删除的旧已总结消息数量。值过小会导致超限后每轮只删少量消息。</small> |

## 记忆生成设置

配置域：`"reflection_engine"`。控制何时触发对话总结尝试，并从有价值的结果生成记忆。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"reflection_engine.summary_trigger_rounds"` | `"int"` | `10` | 最小值：`1`<br>最大值：`100` | 总结触发轮次<br><small>累计对话达到该轮次（一问一答为一轮）后尝试总结；没有稳定事实时不写入长期记忆。</small> |

## 自定义提示词模板

配置域：`"prompt_templates"`。自定义记忆总结的提示词模板。留空则使用内置默认模板（位于 core/prompts/ 目录）。模板支持 {current_date}（当前日期时间）和 {conversation}（对话历史）两个占位符，LLM 将收到完整的 JSON 输出格式说明。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"prompt_templates.group_chat_template"` | `"text"` | `""` | - | 群聊记忆总结提示词<br><small>自定义群聊对话生成记忆的提示词模板。留空则使用内置 group_chat_prompt.txt。修改后即时生效，无需重启插件。</small> |
| `"prompt_templates.private_chat_template"` | `"text"` | `""` | - | 私聊记忆总结提示词<br><small>自定义私聊对话生成记忆的提示词模板。留空则使用内置 private_chat_prompt.txt。修改后即时生效，无需重启插件。</small> |

返回[配置参考总览](/reference/configuration)。
