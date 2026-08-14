---
aside: false
pageClass: config-reference-page
---

# 记忆生命周期配置

本页逐项说明记忆质量、衰减、演化、压缩、再巩固和自动清理配置。这些能力作用于记忆形成后的不同阶段：质量过滤决定是否接纳，演化与压缩生成派生信息，衰减和清理决定长期保留强度。

多数能力都有独立开关。关闭增强能力不会删除 canonical 记忆；涉及 Relation、Projection、聚类或压缩的结果属于可重建派生数据，不能替代 SQLite 中的权威记录。

## 重要性衰减

配置域：`"importance_decay"`。随时间逐步降低旧记忆的重要性，防止旧信息长期占据高权重

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"importance_decay.decay_rate"` | `"float"` | `0.01` | 最小值：`0`<br>最大值：`1` | 每日衰减率<br><small>每天对记忆重要性的衰减比例。0.01 表示每天降低 1%。设为 0 可禁用衰减。</small> |
| `"importance_decay.access_decay_window_days"` | `"float"` | `30` | 最小值：`1`<br>最大值：`3650` | 访问强化窗口(天)<br><small>在该窗口内被访问过的记忆，会根据访问次数降低有效衰减率。</small> |
| `"importance_decay.access_decay_max_count"` | `"int"` | `10` | 最小值：`1`<br>最大值：`10000` | 访问强化次数上限<br><small>达到该访问次数时获得最大衰减保护；超过后不会继续增强。</small> |
| `"importance_decay.access_count_decay_multiplier"` | `"float"` | `0.5` | 最小值：`0`<br>最大值：`1` | 访问次数保留比例<br><small>每次每日衰减后访问次数按该比例回落，避免旧热点记忆永久不衰减。</small> |

## 类人记忆增强

配置域：`"human_like_memory"`。让记忆检索行为更接近人类记忆的自然浮现过程，包括近因效应、季节性召回、情感加权、类人格式化及类型感知衰减。该配置在插件重载后生效。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"human_like_memory.recency_bump_enabled"` | `"bool"` | `true` | - | 近因爆发效应<br><small>0-7天记忆获得1.5倍近因加成，模拟人类近期记忆更鲜明的特点</small> |
| `"human_like_memory.seasonal_recall_enabled"` | `"bool"` | `true` | - | 季节性召回<br><small>周年/季节性时间点附近的相关记忆获得检索加成</small> |
| `"human_like_memory.emotion_scoring_mode"` | `"string"` | `"enhanced"` | 可选：`"enhanced"` / `"basic"` / `"disabled"` | 情感加权模式<br><small>enhanced: Jaccard+情绪一致性+强度三维计分; basic: 简单标签重叠; disabled: 关闭情感加权</small> |
| `"human_like_memory.human_like_formatter_mode"` | `"string"` | `"rule"` | 可选：`"rule"` / `"disabled"` | 类人格式化模式<br><small>rule: 基于规则生成自然语言记忆片段; disabled: 仅返回结构化原始结果。当前没有未经评测的 LLM 格式化模式。</small> |
| `"human_like_memory.type_aware_decay_enabled"` | `"bool"` | `true` | - | 类型感知衰减<br><small>不同记忆类型以不同速度衰减: EPISODIC 1.5倍速/ FACTUAL 0.5倍速/ PREFERENCE 0.7倍速/ RELATIONAL 0.6倍速</small> |

## 记忆演化

配置域：`"memory_evolution"`。控制后台记忆演化、派生关系写入和一跳检索扩展。默认关闭，不影响现有 canonical 记忆链路。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"memory_evolution.enabled"` | `"bool"` | `false` | - | 启用记忆演化<br><small>开启后才会创建演化 worker 和派生关系读取扩展。</small> |
| `"memory_evolution.mode"` | `"string"` | `"disabled"` | 可选：`"disabled"` / `"shadow"` / `"readonly"` / `"active"` | 记忆演化模式<br><small>disabled: 关闭；shadow: 仅观察；readonly: 允许读取派生关系；active: 允许写入通过校验的派生对象。</small> |
| `"memory_evolution.trigger_threshold"` | `"float"` | `0.7` | 最小值：`0`<br>最大值：`1` | 演化触发阈值<br><small>记忆重要性或候选关系置信度低于此值时不触发。</small> |
| `"memory_evolution.batch_size"` | `"int"` | `16` | 最小值：`1`<br>最大值：`100` | 演化批量大小<br><small>单个任务读取的候选记忆数量。</small> |
| `"memory_evolution.candidate_limit"` | `"int"` | `16` | 最小值：`1`<br>最大值：`100` | 关系候选上限<br><small>单个种子允许扩展的关系候选数量。</small> |
| `"memory_evolution.max_pending_jobs"` | `"int"` | `100` | 最小值：`0`<br>最大值：`10000` | 待处理任务上限<br><small>超过上限后暂缓新增演化任务。设为 0 可完全阻止入队。</small> |
| `"memory_evolution.max_attempts"` | `"int"` | `3` | 最小值：`1`<br>最大值：`20` | 最大重试次数<br><small>单个后台任务失败后的最大尝试次数。</small> |
| `"memory_evolution.lease_seconds"` | `"int"` | `120` | 最小值：`1`<br>最大值：`3600` | 任务租约时长（秒）<br><small>worker 领取任务后保持租约的时间。</small> |
| `"memory_evolution.retry_base_delay_seconds"` | `"int"` | `10` | 最小值：`1`<br>最大值：`3600` | 重试基础延迟（秒）<br><small>后台任务指数退避的基础延迟。</small> |
| `"memory_evolution.consolidation_debounce_seconds"` | `"int"` | `60` | 最小值：`1`<br>最大值：`86400` | 演化去抖窗口（秒）<br><small>同一记忆在窗口内的重复演化信号会合并。</small> |
| `"memory_evolution.max_input_chars"` | `"int"` | `12000` | 最小值：`1`<br>最大值：`100000` | 演化输入字符上限<br><small>发送给演化模型的证据总字符数上限。</small> |
| `"memory_evolution.max_output_relations"` | `"int"` | `16` | 最小值：`0`<br>最大值：`64` | 关系输出上限<br><small>单次演化最多生成的关系数量。</small> |
| `"memory_evolution.max_output_projections"` | `"int"` | `4` | 最小值：`0`<br>最大值：`16` | 投影输出上限<br><small>单次演化最多生成的投影数量。</small> |
| `"memory_evolution.max_query_expansions"` | `"int"` | `8` | 最小值：`0`<br>最大值：`100` | 查询扩展上限<br><small>单次查询最多追加的派生记忆数量。</small> |
| `"memory_evolution.projection_budget_chars"` | `"int"` | `1600` | 最小值：`1`<br>最大值：`10000` | 投影摘要字符预算<br><small>单个投影摘要允许使用的最大字符数。</small> |
| `"memory_evolution.require_review_for_high_impact"` | `"bool"` | `true` | - | 高影响关系需要复核<br><small>开启后高影响关系不会自动激活。</small> |
| `"memory_evolution.auto_active_relation_types"` | `"list<string>"` | `["same_episode","supports","related"]` | - | 自动激活的关系类型<br><small>达到阈值后允许自动激活的低影响关系类型。</small> |

## 语义压缩

配置域：`"semantic_compression"`。把同边界旧 canonical 记忆按 topic 聚合为带 source revision 的 `semantic_summary` Projection；原记忆不会被删除或替换。该功能要求同时启用 Memory Evolution，配置变更后需重启。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"semantic_compression.enabled"` | `"bool"` | `false` | - | 启用语义压缩<br><small>开启并启用 Memory Evolution 后，每日生成派生摘要；关闭后已有语义摘要不再进入召回，canonical 保持可用。</small> |
| `"semantic_compression.age_days"` | `"float"` | `60` | - | 压缩年龄阈值<br><small>记忆创建超过此天数后才进入压缩候选。建议 45-90 天。</small> |
| `"semantic_compression.similarity_threshold"` | `"float"` | `0.85` | - | 聚类相似度阈值<br><small>同 scope、privacy 和 role 的记忆 topic Jaccard 重叠达到该值才生成摘要。建议 0.75-0.92。</small> |

## 情景聚类

配置域：`"episode_clustering"`。按时间窗口和主题重叠将碎片化记忆聚合为 episode

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"episode_clustering.enabled"` | `"bool"` | `true` | - | 启用情景聚类<br><small>开启后同一事件的多条记忆会在后台自动聚合为 episode。</small> |
| `"episode_clustering.time_window_hours"` | `"float"` | `24` | - | 时间窗口（小时）<br><small>该时间窗口内的记忆可能属于同一 episode。建议 12-48 小时。</small> |
| `"episode_clustering.topic_overlap_threshold"` | `"float"` | `0.5` | - | 主题重叠阈值<br><small>两条记忆 topic Jaccard 重叠超过该值才归为同一 episode。</small> |

## 人格调制遗忘率

配置域：`"persona_decay"`。不同人格的遗忘速度不同。decay_modifier > 1 健忘，< 1 记忆深刻。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"persona_decay.enabled"` | `"bool"` | `true` | - | 启用人格调制遗忘率<br><small>开启后根据 decay_modifier 调整 TTL 计算。</small> |
| `"persona_decay.default_modifier"` | `"float"` | `1` | - | 默认遗忘倍率<br><small>1.0=标准。2.0=两倍速遗忘。0.5=半速遗忘。建议 0.3-3.0。</small> |

## 记忆再巩固

配置域：`"reconsolidation"`。召回时只生成可审阅候选，不直接修改 canonical；人工确认后按来源 revision CAS 应用，并可回滚旧正文。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"reconsolidation.enabled"` | `"bool"` | `false` | - | 是否启用记忆再巩固候选。开启后，被反复召回的候选记忆经 LLM 修订生成 pending 候选，等待人工 CAS 确认；不会自动改写 canonical。 |
| `"reconsolidation.min_recall_count"` | `"int"` | `5` | - | 记忆进入再巩固前必须达到的最低召回次数。值越低触发越积极，值越高越偏向只整理长期反复使用的记忆。 |

## 话题分割

配置域：`"topic_segmentation"`。将 LLM 返回的混合多话题记忆自动拆分为独立记忆原子。支持四种策略：Prompt Engineering(A)、Embedding Clustering(B)、Topic-aware Pre-chunking(C)、Two-stage LLM(D)。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"topic_segmentation.enabled"` | `"bool"` | `true` | - | 启用话题分割<br><small>开启后自动将 LLM 输出的混合记忆按话题拆分为独立 MemoryAtom。建议开启。</small> |
| `"topic_segmentation.strategy"` | `"string"` | `"a_b_hybrid"` | 可选：`"a_b_hybrid"` / `"strategy_a"` / `"strategy_b"` / `"strategy_c"` / `"strategy_d"` | 话题分割策略<br><small>a_b_hybrid(推荐): A做主分割+B做安全网回退; strategy_a: 仅Prompt Engineering; strategy_b: 仅Embedding Clustering; strategy_c: 仅Topic-aware Pre-chunking; strategy_d: 仅Two-stage LLM</small> |
| `"topic_segmentation.strategy_b.similarity_threshold"` | `"float"` | `0.5` | 最小值：`0`<br>最大值：`1` | 相似度阈值<br><small>两条 key_fact 的 cosine 相似度超过此值时归为同一话题。值越高话题越细粒度。</small> |
| `"topic_segmentation.strategy_b.min_cluster_size"` | `"int"` | `1` | 最小值：`1`<br>最大值：`100` | 最小簇大小<br><small>每个话题至少包含的 key_fact 数量。低于此值的簇将被合并或丢弃。</small> |
| `"topic_segmentation.strategy_b.max_clusters"` | `"int"` | `5` | 最小值：`1`<br>最大值：`50` | 最大簇数量<br><small>单次分割最多产生的话题数量上限。超出后合并最相似的簇。</small> |
| `"topic_segmentation.strategy_c.topic_shift_threshold"` | `"float"` | `0.3` | 最小值：`0`<br>最大值：`1` | 话题切换阈值<br><small>相邻消息的语义相似度低于此值时判定为话题边界。值越低切换越频繁。</small> |
| `"topic_segmentation.strategy_c.min_chunk_size"` | `"int"` | `2` | 最小值：`1`<br>最大值：`100` | 最小分块消息数<br><small>每个话题块至少包含的消息条数。低于此值的块与相邻块合并。</small> |
| `"topic_segmentation.strategy_d.stage1_max_topics"` | `"int"` | `5` | 最小值：`1`<br>最大值：`50` | 第一阶段最大话题数<br><small>Stage 1 LLM 最多识别的话题数量。超出后按相关性截断。</small> |
| `"topic_segmentation.strategy_d.enable_parallel_stage2"` | `"bool"` | `true` | - | 启用并行 Stage 2<br><small>开启后多个话题的 Stage 2 LLM 调用并行执行，加速处理但增加 API 并发压力。</small> |
| `"topic_segmentation.hybrid_fallback_fact_threshold"` | `"int"` | `3` | 最小值：`1`<br>最大值：`100` | 混合策略回退阈值<br><small>A+B Hybrid 模式下，当单条记忆的 key_fact 数量达到此阈值时触发策略B二次分割。值越小越容易触发回退。</small> |
| `"topic_segmentation.legacy_backfill.enabled"` | `"bool"` | `true` | - | 启用存量回填<br><small>开启后可通过 API 手动触发后台回填任务，对旧版记忆重新话题分割。</small> |
| `"topic_segmentation.legacy_backfill.batch_size"` | `"int"` | `50` | 最小值：`1`<br>最大值：`1000` | 回填批量大小<br><small>每批处理的旧版记忆条数。值越大回填越快但内存峰值越高。</small> |
| `"topic_segmentation.legacy_backfill.max_backfill_per_run"` | `"int"` | `500` | 最小值：`1`<br>最大值：`10000` | 单次回填上限<br><small>每次回填任务最多处理的记忆总数。超出后下次任务继续。</small> |

## 原子分类器设置

配置域：`"atom_classifier"`。控制记忆原子分类器的行为

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"atom_classifier.negation_detection_enabled"` | `"bool"` | `true` | - | 启用否定检测<br><small>开启后记录否定极性，并避免把带否定动作的未来表达误判为已确认计划。</small> |

## 闪光灯记忆设置

配置域：`"flashbulb"`。高情感强度记忆永久保留，不受衰减影响

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"flashbulb.enabled"` | `"bool"` | `true` | - | 启用闪光灯记忆保护<br><small>开启后，emotional_intensity >= 阈值的记忆将永不过期。</small> |
| `"flashbulb.intensity_threshold"` | `"float"` | `0.9` | 最小值：`0`<br>最大值：`1` | 闪光灯记忆强度阈值<br><small>emotional_intensity 达到此值的记忆将完全跳过衰减。0.90 表示极高情感强度的记忆。</small> |

## 自动清理设置

配置域：`"forgetting_agent"`。定期清理时间久远且重要性低的旧记忆，控制记忆库规模

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"forgetting_agent.auto_cleanup_enabled"` | `"bool"` | `true` | - | 启用每日自动清理<br><small>每日重要性衰减后自动清理符合条件的旧记忆。</small> |
| `"forgetting_agent.cleanup_days_threshold"` | `"int"` | `30` | 最小值：`1`<br>最大值：`3650` | 清理天数阈值（天）<br><small>记忆创建超过该天数后进入清理候选。需同时满足重要性低于阈值才会被删除。</small> |
| `"forgetting_agent.cleanup_importance_threshold"` | `"float"` | `0.3` | 最小值：`0`<br>最大值：`1` | 清理重要性阈值<br><small>重要性低于该值的旧记忆才会被清理。0.3 表示重要性低于 30% 的记忆。</small> |

## 记忆写入门禁

配置域：`"quality.gate"`。候选记忆写入 canonical 之前按质量原因码执行确定性检查与处置路由（隔离/丢弃/标记写入）。保存后即时热重载生效，无需重启；处置语义、规则引擎与门禁页操作见[门禁配置](/features/gate-configuration)。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"quality.gate.enabled"` | `"bool"` | `true` | - | 门禁总开关<br><small>开启后按绑定解析 profile，评估规则树与处置优先级；关闭后规则引擎与 profile 处置不再参与，携带原因码的候选一律回退到隔离（quarantine），不产生 discard 或 mark_write。</small> |
| `"quality.gate.default_profile"` | `"string"` | `"private"` | 必须存在于 `profiles` | 默认 profile<br><small>没有任何绑定命中时回退到该 profile。内置 `private` 与 `group` 两个 profile，默认分别绑定私聊与群聊。</small> |

:::: warning 复合分支不逐叶进入 Schema
`profiles`（检查开关、阈值、词表、处置、Judge、规则）与 `bindings`（会话类型/群 ID/人格 ID → profile）是对象数组，Schema 只表达 `enabled` 与 `default_profile` 两个标量叶；复合值由后端 Pydantic 兜底校验，只能在 Dashboard 门禁页（System → 「门禁」，`#/gate`）编辑，通用配置页不显示这些叶。
::::

### 调优建议

- **处置策略**：默认 `quarantine` 最保守，适合冷启动；确定某类原因码纯属噪声时，用该 profile 的 `disposition_overrides` 改为 `discard` 省去人工复核；想保留低置信但有价值的候选时改用 `mark_write`——写入 canonical 但默认不参与召回、注入与演化，可用 `/memora search <关键词> [k] true` 或记忆列表 API 的 `include_mark_write=true` 显式读取。
- **阈值**：`min_deterministic_score`（默认 `0.42`）是确定性检查通过线，达到即直接放行；调低让更多候选直接通过、减少进入 Judge 与隔离的数量，调高更严格——更多候选落入 Judge（若仍不低于 `min_judge_score`）或被拒绝。`min_judge_score` 必须不高于 `min_deterministic_score`。
- **Judge**：默认关闭。开启后仅在确定性检查不足以判定的路径调用 LLM，每次复核消耗额度；自定义模板必须包含 `{claim_text}` 与 `{source_text}` 占位符，可追加 `{chat_type}`、`{topics}`、`{importance}`，不超过 2000 字符。
- **绑定**：按列表顺序首个精确命中生效，未声明字段视为不约束。为高频群或指定人格建独立 profile 时，先放更具体的绑定，再保留兜底绑定；否则未命中会话回退 `default_profile`。
- **词表**：否定标记集 `append` 模式在内置标记之上追加，`replace` 模式完全接管；否定白名单始终在内置项（「不错」「没问题」「没准」）之上追加，用于豁免含否定词但语义肯定的表述。

## 原子质量过滤 (v2.6)

配置域：`"atom_quality_filter"`。控制记忆原子创建时的质量门槛，减少无关联短期聊天记录的存储。六层防线：置信度/长度/重要性阈值 + 信息量预检 + 试用期 TTL + 同批去重 + 冷存储

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"atom_quality_filter.atom_quality_filter_enabled"` | `"bool"` | `true` | - | 启用质量过滤<br><small>质量过滤总开关。关闭后所有过滤逻辑跳过，等同于 v2.5 行为。</small> |
| `"atom_quality_filter.atom_min_confidence"` | `"float"` | `0.65` | 最小值：`0`<br>最大值：`1` | 最小置信度阈值<br><small>低于此置信度的原子不保存。UNKNOWN 类型默认置信度为 0.68，仍需同时通过信息量与重要性检查。</small> |
| `"atom_quality_filter.atom_min_importance"` | `"float"` | `0.3` | 最小值：`0`<br>最大值：`1` | 最小重要性阈值<br><small>低于此重要性的原子不保存。LLM 评分为 0.0-0.2 的对话通常无需记忆。</small> |
| `"atom_quality_filter.atom_min_content_length"` | `"int"` | `5` | 最小值：`1`<br>最大值：`10000` | 最小内容长度（字符）<br><small>过短的内容（如单个词/表情）不保存为原子。</small> |
| `"atom_quality_filter.atom_info_check_enabled"` | `"bool"` | `true` | - | 启用信息量预检<br><small>过滤寒暄/纯应答/纯表情等无信息量内容。</small> |
| `"atom_quality_filter.atom_probationary_enabled"` | `"bool"` | `true` | - | 启用试用期机制<br><small>新创建且未被访问的 UNKNOWN/EPISODIC 低重要性原子使用缩短的 TTL（默认 3 天），被检索访问后自动恢复标准 TTL。</small> |
| `"atom_quality_filter.atom_probationary_ttl_days"` | `"float"` | `3` | 最小值：`1`<br>最大值：`365` | 试用期 TTL（天）<br><small>试用期内原子在此天数后过期。</small> |
| `"atom_quality_filter.atom_dedup_enabled"` | `"bool"` | `true` | - | 启用同批次去重<br><small>同一批次内 Jaccard 相似度 >= 阈值的原子合并为一条（保留置信度更高的）。</small> |
| `"atom_quality_filter.atom_dedup_threshold"` | `"float"` | `0.7` | 最小值：`0`<br>最大值：`1` | 去重相似度阈值<br><small>Jaccard 相似度达到此阈值时视为重复。</small> |
| `"atom_quality_filter.atom_cold_storage_enabled"` | `"bool"` | `true` | - | 启用冷存储分层<br><small>长期未被访问的低重要性原子迁移到 COLD 状态，不参与常规检索。</small> |
| `"atom_quality_filter.atom_cold_days_threshold"` | `"float"` | `14` | 最小值：`1`<br>最大值：`3650` | 冷存储迁移天数阈值<br><small>原子最后一次被访问距今超过此天数且重要性低于冷存储最大重要性时，自动标记为 COLD。</small> |
| `"atom_quality_filter.atom_cold_max_importance"` | `"float"` | `0.4` | 最小值：`0`<br>最大值：`1` | 冷存储最大重要性<br><small>仅重要性低于此值的原子参与冷存储迁移。</small> |

返回[配置参考总览](/reference/configuration)。
