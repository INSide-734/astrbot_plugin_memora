---
aside: false
pageClass: config-reference-page
---

# 召回、注入与索引配置

本页逐项说明候选检索、融合、过滤、图召回、重排序和索引管理配置。召回链按“取候选 → 融合与扩展 → 重排序 → 隔离过滤 → 注入路由”工作，因此单独提高 `top_k` 并不保证最终注入更多记忆。

建议先选择注入预设，再按观测结果调整预算和阈值。高级覆盖、图检索与重排序会增加延迟和成本，启用前应先确认对应 Provider 与索引可用。

## 记忆召回

配置域：`"recall_engine"`。控制记忆检索和注入到对话的行为

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"recall_engine.top_k"` | `"int"` | `5` | 最小值：`0`<br>最大值：`50` | 单次召回数量<br><small>每次检索返回的最相关记忆条数。设为 0 可跳过自动召回和注入，仅保留历史注入片段清理。建议 3-10。</small> |
| `"recall_engine.max_k"` | `"int"` | `10` | 最小值：`1`<br>最大值：`50` | 主动检索最大数量<br><small>Agent 主动调用长期记忆检索工具时允许返回的最大记忆条数。用于限制工具单次召回规模，建议 5-10。</small> |
| `"recall_engine.importance_weight"` | `"float"` | `1` | 最小值：`0`<br>最大值：`10` | 重要性权重<br><small>混合评分中记忆重要性的占比系数。值越大，重要性高的记忆越优先被召回。</small> |
| `"recall_engine.fallback_to_vector"` | `"bool"` | `true` | - | 降级到纯向量检索<br><small>混合检索失败或结果为空时，自动降级为纯向量检索。建议开启。</small> |
| `"recall_engine.injection_routing_mode"` | `"string"` | `"manual"` | 可选：`"manual"` / `"auto"` / `"hybrid"` | 记忆注入路由模式<br><small>manual 严格使用管理员预设；auto 按当前请求信号自动选择；hybrid 自动选择后限制在管理员设定的预设范围内。</small> |
| `"recall_engine.injection_manual_preset"` | `"string"` | `"balanced"` | 可选：`"tool_first"` / `"low_cost"` / `"balanced"` / `"quality"` | 手动模式预设<br><small>纯手动模式固定使用的高级策略预设。</small> |
| `"recall_engine.injection_auto_fallback_preset"` | `"string"` | `"balanced"` | 可选：`"tool_first"` / `"low_cost"` / `"balanced"` / `"quality"` | 自动模式回退预设<br><small>自动路由无法形成可靠建议时使用的高级策略预设。</small> |
| `"recall_engine.injection_hybrid_base_preset"` | `"string"` | `"balanced"` | 可选：`"tool_first"` / `"low_cost"` / `"balanced"` / `"quality"` | 混合模式基础预设<br><small>混合模式的稳定基线，必须位于最小与最大预设之间。</small> |
| `"recall_engine.injection_hybrid_min_preset"` | `"string"` | `"low_cost"` | 可选：`"tool_first"` / `"low_cost"` / `"balanced"` / `"quality"` | 混合模式最小预设<br><small>混合模式自动建议允许采用的最低策略等级。</small> |
| `"recall_engine.injection_hybrid_max_preset"` | `"string"` | `"quality"` | 可选：`"tool_first"` / `"low_cost"` / `"balanced"` / `"quality"` | 混合模式最大预设<br><small>混合模式自动建议允许采用的最高策略等级。</small> |
| `"recall_engine.injection_delivery_override"` | `"string"` | `"auto"` | 可选：`"auto"` / `"extra_user_content"` / `"user_message_before"` / `"user_message_after"` / `"fake_tool_call"` / `"fake_tool_call_deepseek_v4"` | 注入传输覆盖<br><small>auto 由预设和 Provider 能力选择临时注入方式；其余选项强制指定兼容的传输方式。System Prompt 不再是注入目标。</small> |
| `"recall_engine.injection_preset_overrides_enabled"` | `"bool"` | `false` | - | 启用高级预设覆盖<br><small>开启后允许下方预算和内容开关覆盖已解析预设；数值为 0 时仍使用预设默认值。</small> |
| `"recall_engine.injection_decision_retention_days"` | `"int"` | `30` | 可选：`7` / `30` / `90` / `180` / `0` | 注入决策保留天数<br><small>只允许 7、30、90、180 或 0 天；0 表示不按时间删除，但仍受最大行数限制。</small> |
| `"recall_engine.injection_decision_max_rows"` | `"int"` | `100000` | 最小值：`1000`<br>最大值：`1000000` | 注入决策最大行数<br><small>保留的完整脱敏决策记录上限；超过上限后优先删除最旧记录。</small> |
| `"recall_engine.auto_remove_injected"` | `"bool"` | `true` | - | 自动清除旧注入片段<br><small>注入新记忆前自动删除历史中已注入的旧记忆片段，避免重复累积和 token 浪费。建议开启。</small> |
| `"recall_engine.inject_with_recent_context"` | `"bool"` | `false` | - | 启用跨轮次上下文扩展检索<br><small>启用后检索记忆时会自动拼接最近 2 轮对话（当前消息 + Bot 上一条回复 + 用户上一条消息）作为扩展查询，提升召回记忆与当前话题的相关性，减少无关记忆干扰。</small> |
| `"recall_engine.search_cache_enabled"` | `"bool"` | `true` | - | 启用短期检索缓存<br><small>短时间内相同会话和相同查询会复用检索结果，降低连续追问时的 SQLite/FAISS 开销。</small> |
| `"recall_engine.search_cache_ttl_seconds"` | `"float"` | `45` | 最小值：`0`<br>最大值：`600` | 检索缓存 TTL 秒数<br><small>缓存保留时间。写入、更新、删除记忆时会自动失效。设为 0 可关闭缓存。</small> |
| `"recall_engine.search_cache_max_size"` | `"int"` | `256` | 最小值：`0`<br>最大值：`10000` | 检索缓存最大条目数<br><small>限制内存中保留的检索结果数量。超过上限时自动淘汰最久未使用的结果。</small> |
| `"recall_engine.max_chain_hops"` | `"int"` | `3` | 最小值：`0`<br>最大值：`3` | 链式回忆最大跳数<br><small>多跳检索时从命中记忆向外扩展的最大跳数。1=仅直接关联，2=包含间接关联。值越大召回越全但耗时越长。建议 1-3。</small> |
| `"recall_engine.chain_hop_decay"` | `"float"` | `0.7` | 最小值：`0`<br>最大值：`1` | 链式扩展衰减率<br><small>每跳一跳，关联记忆的评分乘以该衰减率。0.7 表示每跳衰减 30%。值越小远距离关联越弱。</small> |
| `"recall_engine.chain_graph_expansion_enabled"` | `"bool"` | `true` | - | 启用图边多跳扩展<br><small>沿知识图谱边扩展到间接关联的实体。关闭可减少检索计算开销。</small> |
| `"recall_engine.chain_topic_expansion_enabled"` | `"bool"` | `true` | - | 启用话题关联多跳扩展<br><small>沿话题相似度扩展到间接关联的记忆。关闭可减少检索计算开销。</small> |
| `"recall_engine.id_cache_size"` | `"int"` | `1000` | - | 向量 ID 缓存大小<br><small>FAISS 向量 ID 映射的内存缓存条数。增大可减少 SQLite 查询但增加内存占用。</small> |
| `"recall_engine.stopwords_path"` | `"string"` | `""` | - | 自定义停用词文件路径<br><small>BM25 分词使用的自定义停用词列表文件路径。留空使用默认中文停用词表。一行一个停用词。</small> |
| `"recall_engine.query_rewrite_enabled"` | `"bool"` | `true` | - | 启用语义查询改写 (R1)<br><small>使用 LLM few-shot 将模糊查询（如"上次那个事"）展开为多角度检索词。关闭后回退到硬编码关键词匹配。需要 LLM Provider 可用。</small> |
| `"recall_engine.privacy_filter_enabled"` | `"bool"` | `true` | - | 启用隐私记忆过滤<br><small>群聊检索时自动过滤来自私聊的机密记忆（privacy_level=confidential），避免私聊秘密在群聊中暴露。</small> |
| `"recall_engine.testing_effect_async"` | `"bool"` | `true` | - | 测试效应：异步模式<br><small>每次成功召回后异步强化记忆（reinforcement_count+1 + TTL×1.05）。开启后不阻塞检索热路径。</small> |
| `"recall_engine.testing_effect_top_k"` | `"int"` | `5` | 最小值：`1`<br>最大值：`50` | 测试效应：强化条数上限<br><small>每次召回最多对前 N 条记忆应用测试效应强化。值越大强化范围越广但写压力也越大。</small> |
| `"recall_engine.injection_budget_chars"` | `"int"` | `0` | 最小值：`0`<br>最大值：`10000` | 注入预算：总字符数上限<br><small>普通记忆注入总字符数硬上限覆盖。0 表示使用已解析高级预设的默认值，不表示无限制。</small> |
| `"recall_engine.injection_memory_max_chars"` | `"int"` | `0` | 最小值：`0`<br>最大值：`2000` | 注入预算：单条记忆最大字符数<br><small>单条记忆 content 的硬上限覆盖。0 表示使用已解析高级预设的默认值，不表示无限制。</small> |
| `"recall_engine.injection_metadata_max_chars"` | `"int"` | `0` | 最小值：`0`<br>最大值：`500` | 注入预算：单条记忆元数据最大字符数<br><small>单条记忆元数据的硬上限覆盖。0 表示使用已解析高级预设的默认值，不表示无限制。</small> |
| `"recall_engine.injection_include_key_facts"` | `"bool"` | `true` | - | 注入：包含 key_facts<br><small>关闭可减少注入内容长度。</small> |
| `"recall_engine.injection_include_topics"` | `"bool"` | `true` | - | 注入：包含 topics<br><small>关闭可减少注入内容长度。</small> |
| `"recall_engine.injection_include_participants"` | `"bool"` | `false` | - | 注入：包含 participants<br><small>群聊场景可开启以提供参与者上下文。默认关闭以节省 token。</small> |
| `"recall_engine.injection_compact_header"` | `"bool"` | `true` | - | 注入：紧凑版头部<br><small>使用精简中文 header/footer 替代英文安全规则，显著减少 token 开销。</small> |
| `"recall_engine.cognitive_context_budget_chars"` | `"int"` | `300` | 最小值：`0`<br>最大值：`2000` | 认知上下文预算<br><small>黑话解释/表达模式/好感度状态的注入字符上限。</small> |
| `"recall_engine.proactive_plan_budget_chars"` | `"int"` | `240` | 最小值：`0`<br>最大值：`1000` | 前瞻提醒预算<br><small>即将触发的 PLANNED 原子提醒的注入字符上限。</small> |
| `"recall_engine.serial_position_enabled"` | `"bool"` | `true` | - | 启用序列位置效应<br><small>对话开头和结尾的消息获得额外的 importance 加成（首 2 条 +0.15，末 2 条 +0.10），模拟真人首因/近因效应。</small> |
| `"recall_engine.session_cache_enabled"` | `"bool"` | `true` | - | 启用请求级检索缓存<br><small>同一 LLM 请求内的重复搜索（Bridge→RecallHandler）复用结果，避免重复检索。TTL 极短（10s），仅在同一请求内生效。</small> |
| `"recall_engine.session_cache_ttl_seconds"` | `"float"` | `10` | 最小值：`0`<br>最大值：`120` | 请求级缓存 TTL 秒数<br><small>同一请求内检索结果的缓存有效期。建议 5-15 秒。</small> |
| `"recall_engine.spontaneous_recall_enabled"` | `"bool"` | `true` | - | 启用自发回忆<br><small>以低概率（默认 6%）在非查询驱动下主动浮现关联记忆，模拟真人"突然想起来"的体验。</small> |
| `"recall_engine.spontaneous_recall_probability"` | `"float"` | `0.06` | 最小值：`0`<br>最大值：`1` | 自发回忆触发概率<br><small>每次 LLM 请求触发自发回忆的概率。建议 0.03-0.10。过高会导致记忆注入过于频繁。</small> |
| `"recall_engine.spontaneous_recall_k"` | `"int"` | `2` | - | 自发回忆返回条数<br><small>自发回忆触发时最多返回的记忆条数。建议 1-3 条。</small> |
| `"recall_engine.prospective_recall_enabled"` | `"bool"` | `true` | - | 启用前瞻记忆<br><small>每次 LLM 请求前扫描 24h 内到期的 PLANNED 原子，主动注入待办提醒。模拟真人"今天要做 X"的记忆浮现。</small> |
| `"recall_engine.prospective_lookahead_hours"` | `"float"` | `24` | - | 前瞻记忆扫描窗口<br><small>扫描未来多少小时内的 PLANNED 原子。默认 24 小时。</small> |
| `"recall_engine.prospective_recall_k"` | `"int"` | `3` | - | 前瞻记忆返回条数<br><small>每次扫描最多返回的 PLANNED 原子条数。建议 1-5 条。</small> |
| `"recall_engine.narrative_coherence_enabled"` | `"bool"` | `true` | - | 启用叙事连贯性<br><small>检索结果按时间线排序并按主题聚类，标注叙事分组和位置。下游格式化器可据此生成过渡短语提升记忆输出的可读性。</small> |
| `"recall_engine.interest_boost_enabled"` | `"bool"` | `true` | - | 启用兴趣记忆显著性<br><small>根据用户画像中的兴趣标签，对匹配话题的记忆给予 importance 加成。依赖用户画像。</small> |

## 检索融合策略

配置域：`"fusion_strategy"`。BM25 和向量检索结果的融合参数（内部使用 RRF 算法）

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"fusion_strategy.rrf_k"` | `"int"` | `60` | 最小值：`1`<br>最大值：`1000` | RRF 融合参数 k<br><small>值越小越强调排名靠前的结果，值越大融合越平滑。推荐 30-120，默认 60。</small> |

## 混合检索评分权重

配置域：`"hybrid_scoring"`。控制 BM25 + 向量混合检索结果的评分权重分配。三个评分权重必须都在 0-1 且总和为 1；修改后需重载插件。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"hybrid_scoring.score_alpha"` | `"float"` | `0.5` | 最小值：`0`<br>最大值：`1` | 检索相关性权重<br><small>BM25+向量检索相似度在混合评分中的占比。值越大，检索结果越偏向字面和语义匹配。</small> |
| `"hybrid_scoring.score_beta"` | `"float"` | `0.25` | 最小值：`0`<br>最大值：`1` | 重要性权重<br><small>记忆自身重要性在混合评分中的占比。值越大，高重要性记忆越容易被召回。</small> |
| `"hybrid_scoring.score_gamma"` | `"float"` | `0.25` | 最小值：`0`<br>最大值：`1` | 时间新鲜度权重<br><small>记忆创建/访问时间在混合评分中的占比。值越大，越新的记忆越容易被召回。</small> |
| `"hybrid_scoring.mmr_lambda"` | `"float"` | `0.7` | 最小值：`0`<br>最大值：`1` | MMR 多样性权衡<br><small>这是 HybridRetriever 候选去重的相关性权重，与 reranker.mmr_lambda 分属两个排序阶段。</small> |

## 记忆隔离设置

配置域：`"filtering_settings"`。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"filtering_settings.use_persona_filtering"` | `"bool"` | `true` | - | 按人格隔离记忆<br><small>开启后只召回与当前人格相关的记忆，不同人格的记忆互不干扰。</small> |
| `"filtering_settings.use_session_filtering"` | `"bool"` | `true` | - | 按会话隔离记忆<br><small>开启后每个会话的记忆独立存储，不同会话之间不共享记忆。</small> |

## 图记忆双路检索

配置域：`"graph_memory"`。启用后会同时维护文档路和图路，两边都支持关键词与向量检索，再统一融合召回结果。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"graph_memory.enabled"` | `"bool"` | `true` | - | 启用图记忆检索<br><small>开启后自动构建图节点、边和图向量索引。建议开启。</small> |
| `"graph_memory.document_route_weight"` | `"float"` | `0.65` | 最小值：`0`<br>最大值：`1` | 文档路权重<br><small>文档路融合分数在最终双路排序中的占比。</small> |
| `"graph_memory.graph_route_weight"` | `"float"` | `0.35` | 最小值：`0`<br>最大值：`1` | 图路权重<br><small>图路融合分数在最终双路排序中的占比。</small> |
| `"graph_memory.cross_route_bonus"` | `"float"` | `0.08` | 最小值：`0`<br>最大值：`0.5` | 双路命中加分<br><small>同一记忆同时被文档路和图路命中时增加的额外分数。</small> |
| `"graph_memory.expansion_limit"` | `"int"` | `24` | 最小值：`1`<br>最大值：`200` | 图邻居扩展上限<br><small>图关键词检索时从命中节点扩展到邻居条目的最大候选数。</small> |
| `"graph_memory.expansion_hops"` | `"int"` | `1` | 最小值：`1`<br>最大值：`2` | 图扩展跳数<br><small>图关键词检索从命中节点向外扩展的跳数。默认 1；设为 2 可召回间接关联，但会增加少量查询开销。</small> |
| `"graph_memory.second_hop_weight"` | `"float"` | `0.4` | 最小值：`0`<br>最大值：`1` | 二跳扩展权重<br><small>二跳图候选的分数权重。值越高，间接关系越容易进入最终召回。</small> |
| `"graph_memory.dynamic_route_weighting"` | `"bool"` | `true` | - | 动态路由权重<br><small>根据查询中的关系、时间、定义类意图，自动调整文档路和图路权重。</small> |
| `"graph_memory.max_topics_per_memory"` | `"int"` | `6` | 最小值：`1`<br>最大值：`20` | 单记忆最大主题数<br><small>从一条记忆中最多提取多少个 topics 节点。</small> |
| `"graph_memory.max_participants_per_memory"` | `"int"` | `8` | 最小值：`1`<br>最大值：`30` | 单记忆最大参与者数<br><small>从一条记忆中最多提取多少个 participants 节点。</small> |
| `"graph_memory.max_facts_per_memory"` | `"int"` | `8` | 最小值：`1`<br>最大值：`30` | 单记忆最大事实数<br><small>从一条记忆中最多提取多少条 key_facts 进入图索引。</small> |
| `"graph_memory.atom_enabled"` | `"bool"` | `true` | - | 启用记忆原子化<br><small>开启后每条 key_fact 独立为一个记忆原子，拥有自己的存活时间和衰减曲线。关闭则完全回退到原来的粗粒度行为。</small> |
| `"graph_memory.atom_maintenance_interval_hours"` | `"float"` | `24` | 最小值：`1`<br>最大值：`168` | 原子维护间隔(小时)<br><small>生命周期管理器每隔多少小时执行一次过期/遗忘检查。</small> |
| `"graph_memory.atom_forget_delay_days"` | `"float"` | `7` | 最小值：`1`<br>最大值：`90` | 原子遗忘延迟(天)<br><small>过期原子在多少天后从检索索引中彻底移除。</small> |
| `"graph_memory.atom_purge_delay_days"` | `"float"` | `30` | 最小值：`1`<br>最大值：`365` | 遗忘原子物理清理延迟(天)<br><small>已从检索索引移除的遗忘原子，在多少天后从数据库物理删除以回收存储空间。</small> |
| `"graph_memory.score_alpha"` | `"float"` | `0.55` | 最小值：`0`<br>最大值：`1` | 图检索：向量相似度权重<br><small>图路向量检索相似度在图检索混合评分中的占比。值越大图向量匹配越重要。范围 0-1。</small> |
| `"graph_memory.score_beta"` | `"float"` | `0.2` | 最小值：`0`<br>最大值：`1` | 图检索：关键词匹配权重<br><small>图路关键词匹配分数在图检索混合评分中的占比。范围 0-1。</small> |
| `"graph_memory.score_gamma"` | `"float"` | `0.15` | 最小值：`0`<br>最大值：`1` | 图检索：时间新鲜度权重<br><small>图节点/边的创建时间在图检索混合评分中的占比。范围 0-1。</small> |
| `"graph_memory.score_delta"` | `"float"` | `0.1` | 最小值：`0`<br>最大值：`1` | 图检索：图结构权重<br><small>图节点度数、边权重等结构特征在图检索混合评分中的占比。范围 0-1。</small> |
| `"graph_memory.temporal_edges_enabled"` | `"bool"` | `true` | - | 启用时序图边<br><small>控制后续写入是否提取 before/after/during 关系。修改后需重载插件并重建图派生数据。</small> |
| `"graph_memory.causal_edges_enabled"` | `"bool"` | `true` | - | 启用因果图边<br><small>控制后续写入是否提取 caused_by/results_in/prevents 关系。修改后需重载插件并重建图派生数据。</small> |

## 重排序模型

配置域：`"reranker"`。检索后对候选记忆进行精细重排序，提升 Top-K 精度

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"reranker.enabled"` | `"bool"` | `true` | - | 启用重排序器<br><small>关闭后跳过所有重排序步骤，减少计算开销。</small> |
| `"reranker.strategy"` | `"string"` | `"mmr"` | 可选：`"mmr"` / `"embedding_similarity"` / `"llm"` / `"hybrid"` | 重排序策略<br><small>mmr: 最大边际相关性(推荐，无额外LLM调用); embedding_similarity: Embedding余弦相似度; llm: LLM打分(高成本); hybrid: 两级排序</small> |
| `"reranker.mmr_lambda"` | `"float"` | `0.7` | 最小值：`0`<br>最大值：`1` | MMR 相关性权重<br><small>值越高越偏向相关性。范围 0-1。</small> |
| `"reranker.embedding_similarity_lambda"` | `"float"` | `0.7` | 最小值：`0`<br>最大值：`1` | Embedding 相似度融合权重<br><small>query-doc 余弦相似度与原始得分的融合比例。</small> |
| `"reranker.llm_batch_size"` | `"int"` | `10` | 最小值：`1`<br>最大值：`50` | LLM 重排序批量大小<br><small>每次处理的候选记忆数量上限。LLM 策略会增加 token 消耗。</small> |

## 索引管理

配置域：`"index_management"`。控制 FAISS 向量索引的增量更新和 IVF 自动切换策略

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"index_management.ivf_switch_threshold"` | `"int"` | `10000` | - | IVF 切换阈值<br><small>向量总数超过该值后建议切换至 IVF 索引以提升大规模检索性能。建议 8000-50000。</small> |
| `"index_management.incremental_rebuild_threshold"` | `"int"` | `500` | - | 增量重建阈值<br><small>自上次全量重建以来新增向量超过该值后触发自动重建。</small> |

返回[配置参考总览](/reference/configuration)。
