---
aside: false
pageClass: config-reference-page
---

# 智能与内容增强配置

本页逐项说明画像、学习、知识、笔记、关系、连续性和 Agent 工具配置。这些功能在基础记忆链之上提供附加能力，适合在核心写入和召回稳定后按需启用。

涉及自动写入、Agent 写工具或在线学习的配置会改变数据或策略状态。启用前应先确认权限边界、最小置信度和每日上限，避免把低质量推断长期固化。

## 用户画像

配置域：`"user_profile"`。为每个用户构建动态画像（标签+偏好），驱动个性化记忆检索

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"user_profile.enabled"` | `"bool"` | `true` | - | 启用用户画像<br><small>开启后自动从对话中提取用户标签与偏好，并用于个性化记忆排序。</small> |
| `"user_profile.boost_strength"` | `"float"` | `0.15` | - | 个性化加权强度<br><small>用户标签匹配时的检索得分加成比例。建议 0.1-0.2。</small> |
| `"user_profile.tag_decay_rate"` | `"float"` | `0.98` | - | 标签衰减率<br><small>每日标签置信度的指数衰减系数。0.98 表示每天降低 2%。值越小标签遗忘越快。</small> |
| `"user_profile.min_tag_confidence"` | `"float"` | `0.1` | - | 标签最低置信度<br><small>置信度低于此值的标签将被自动清理。0.1 表示只保留有足够证据的标签。</small> |

## 自主学习

配置域：`"auto_learning"`。从对话反馈中自动调整检索权重、记忆重要性、TTL 等参数

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"auto_learning.enabled"` | `"bool"` | `true` | - | 启用自主学习<br><small>开启后插件会根据记忆复用率和对话质量自动优化参数。</small> |
| `"auto_learning.learning_rate"` | `"float"` | `0.01` | - | 学习率<br><small>参数更新的步长。建议 0.01-0.05。</small> |
| `"auto_learning.target_hit_rate_low"` | `"float"` | `0.3` | - | 目标命中率下限<br><small>记忆召回命中率低于此值时自动降低 importance 阈值、提高 top_k。</small> |
| `"auto_learning.target_hit_rate_high"` | `"float"` | `0.7` | - | 目标命中率上限<br><small>记忆召回命中率高于此值时自动提高 importance 阈值、降低 top_k。</small> |
| `"auto_learning.quality_ema_alpha"` | `"float"` | `0.2` | - | 质量评分 EMA 系数<br><small>对话质量评分的指数移动平均平滑系数。0.2 表示新评分权重 20%。</small> |

## 知识库

配置域：`"knowledge_base"`。结构化知识存储与查询，从记忆原子中提炼长期知识

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"knowledge_base.enabled"` | `"bool"` | `true` | - | 启用知识库<br><small>开启后自动从重要记忆中提取结构化知识条目。</small> |
| `"knowledge_base.dedup_threshold"` | `"float"` | `0.85` | - | 去重 Jaccard 阈值<br><small>新知识与已有知识的文本相似度超过该值时触发合并。建议 0.75-0.90。</small> |
| `"knowledge_base.expire_days"` | `"int"` | `365` | - | 知识过期天数<br><small>超过该天数的知识条目将被自动清理。0 表示永不过期。</small> |

## 自主笔记

配置域：`"notes"`。支持自主创建、修改、读取和索引笔记，提供 Agent 工具接口

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"notes.enabled"` | `"bool"` | `true` | - | 启用笔记系统<br><small>开启后插件可自主管理笔记，支持全文搜索和版本历史。</small> |
| `"notes.auto_create_min_length"` | `"int"` | `50` | - | 自动创建最低长度<br><small>对话文本达到该字符数时才触发自动笔记生成。建议 50-200。</small> |
| `"notes.max_tags"` | `"int"` | `10` | - | 笔记最大标签数<br><small>自动生成笔记标签的数量上限。</small> |
| `"notes.max_versions"` | `"int"` | `20` | - | 每条笔记最多保留的历史版本数。超过上限时淘汰最旧版本，用于限制编辑历史占用的存储空间；调低前先确认不再需要长期回溯。 |

## 异常检测

配置域：`"anomaly_detection"`。监控记忆创建速率的异常波动，3-sigma 阈值告警

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"anomaly_detection.enabled"` | `"bool"` | `true` | - | 启用异常检测<br><small>开启后每日统计记忆创建量，与 7 日滚动窗口对比，超 3-sigma 时记录告警。</small> |
| `"anomaly_detection.window_days"` | `"int"` | `7` | - | 滚动窗口天数<br><small>计算基线均值与标准差所用的历史天数。</small> |
| `"anomaly_detection.sigma_threshold"` | `"float"` | `3` | - | Sigma 告警阈值<br><small>当日创建量偏离均值超过 sigma 倍标准差时触发告警。3.0 为标准 3-sigma。</small> |

## 关系阶段追踪

配置域：`"relationship_tracking"`。追踪用户与 Bot 的亲密度变化，从陌生人到密友

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"relationship_tracking.enabled"` | `"bool"` | `true` | - | 启用关系追踪<br><small>开启后根据互动频率和情感极性累积用户亲密度 warmth score。</small> |
| `"relationship_tracking.warmth_decay_per_day"` | `"float"` | `0.005` | - | 温暖度日衰减率<br><small>每天自然的亲密度衰减。0.005 表示每天降低 0.5%（极慢）。设为 0 可停用。</small> |

## 对话连续性追踪

配置域：`"continuity_tracking"`。追踪未完成话题，下次对话时优先恢复上下文

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"continuity_tracking.enabled"` | `"bool"` | `true` | - | 启用连续性追踪<br><small>开启后标记未完成话题并在下次对话时优先注入上下文。</small> |
| `"continuity_tracking.topic_ttl_days"` | `"int"` | `7` | - | 话题存活天数<br><small>未完成话题的最大保留时间。超过后自动清理不再注入。</small> |
| `"continuity_tracking.max_pending_topics"` | `"int"` | `10` | - | 最大待处理话题数<br><small>同时追踪的未完成话题数量上限。超出后最旧的将被移除。</small> |

## Agent 工具

配置域：`"agent_tools"`。控制注册到 AstrBot Agent 的 LLM 工具

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"agent_tools.enable_recall_tool"` | `"bool"` | `true` | - | 启用记忆召回工具<br><small>向 Agent 注册 memory_search 工具。</small> |
| `"agent_tools.enable_memorize_tool"` | `"bool"` | `false` | - | 启用记忆写入工具<br><small>向 Agent 注册 memory_memorize 工具。谨慎开启。</small> |
| `"agent_tools.enable_note_tools"` | `"bool"` | `null` | - | 启用笔记工具（旧版兼容）<br><small>旧版总开关；新配置请使用 enable_note_read_tools 与 enable_note_write_tool。</small> |
| `"agent_tools.enable_note_read_tools"` | `"bool"` | `true` | - | 启用笔记读取工具<br><small>向 Agent 注册 note_search 与 note_read 工具。</small> |
| `"agent_tools.enable_note_write_tool"` | `"bool"` | `false` | - | 启用笔记写入工具<br><small>向 Agent 注册 note_write 工具。默认关闭，建议仅在可信场景下开启。</small> |
| `"agent_tools.enable_knowledge_tools"` | `"bool"` | `true` | - | 启用知识库工具<br><small>向 Agent 注册 knowledge_search/read 工具。</small> |
| `"agent_tools.enable_profile_tools"` | `"bool"` | `true` | - | 启用用户画像工具<br><small>向 Agent 注册 profile_lookup 工具。</small> |
| `"agent_tools.enable_jargon_tools"` | `"bool"` | `true` | - | 启用黑话查询工具<br><small>向 Agent 注册 jargon_explain/list 工具。self_learning 伴侣插件活跃时可能委托并跳过本地注册。</small> |
| `"agent_tools.enable_affection_tools"` | `"bool"` | `true` | - | 启用好感度工具<br><small>向 Agent 注册 affection_check/bot_mood 工具。self_learning 伴侣插件活跃时可能委托并跳过本地注册。</small> |
| `"agent_tools.enable_social_tools"` | `"bool"` | `true` | - | 启用社交关系工具<br><small>向 Agent 注册 relation_lookup/graph 工具。self_learning 伴侣插件活跃时可能委托并跳过本地注册。</small> |
| `"agent_tools.enable_expression_tools"` | `"bool"` | `true` | - | 启用表达模式工具<br><small>向 Agent 注册 expression_recall 工具。self_learning 伴侣插件活跃时可能委托并跳过本地注册。</small> |

## 黑话发现

配置域：`"jargon"`。控制群消息统计与 LLM 黑话推断；关闭后保留已有词条数据，但不再收集候选或调用 LLM。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"jargon.enabled"` | `"bool"` | `false` | - | 启用黑话自动发现<br><small>默认关闭。开启后会统计群消息候选词，并在达到渐进阈值时调用 LLM 推断。修改后需重载插件生效。</small> |

## MAB 权重学习

配置域：`"weight_learning"`。Epsilon-Greedy MAB 在线学习文档路/图路融合权重

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"weight_learning.enabled"` | `"bool"` | `false` | - | 是否启用多臂老虎机（MAB）权重学习。开启后系统会根据反馈逐步调整策略权重；缺少稳定反馈数据时应保持关闭。 |
| `"weight_learning.epsilon"` | `"float"` | `0.1` | - | MAB 的探索概率，取值应在 0 到 1 之间。值越高越常尝试当前非最优策略，学习更充分但短期结果波动也更大。 |
| `"weight_learning.group_by_persona"` | `"bool"` | `true` | - | 是否按人格分别维护学习结果。开启可避免不同人格的反馈相互污染，但会减少每组可用于学习的样本量。 |

返回[配置参考总览](/reference/configuration)。
