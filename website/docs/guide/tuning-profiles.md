---
pageClass: tuning-profiles-page
---

# 选择质量、均衡或低成本档位

Memora 没有一个能够同时切换全部行为的总开关。要让插件整体偏向高质量、均衡或低成本，需要同时设置**成本模式**和**记忆注入预设**，再按需要调整检索范围。

对于大多数安装，建议从“均衡”开始。运行一段时间后，再根据召回效果、Provider 配额、响应延迟和上下文占用选择其他档位。

## 先理解两套档位

### 成本模式

`cost_control.mode` 管理额外 LLM 调用：

| 值 | 作用 |
|---|---|
| `low_cost` | 禁止所有额外 LLM 路径，优先降低 token 和调用费用。 |
| `balanced` | 默认不执行额外 LLM 调用，但允许管理员逐项放行。 |
| `quality` | 允许 LLM 重排序、两阶段话题分割等高成本路径，但仍受每轮调用上限约束。 |

#### 请求级额度如何计算

成本模式只决定某项功能能否尝试；`cost_control.max_extra_llm_calls_per_turn` 再限制同一轮请求实际可使用的额外 Provider 调用总数。两道门必须同时通过。

计入额度的能力包括 LLM 查询改写、LLM 重排、Strategy D 第一阶段、persona interpretation，以及 Strategy C/D 切出多个反思批次后的第 2 批及后续批次。基础反思抽取负责 canonical 记忆写入，不计入额外额度；额度不足时，剩余反思批次会合并回基础调用，不会静默丢弃消息。

reservation 在 Provider 成功返回后提交；即使返回内容无法解析，该次调用也已经消耗额度。Provider 普通失败或任务取消会释放未提交 reservation。`max_reflection_parallel_llm_calls` 只限制瞬时并发，不能替代每轮总额度。

### 注入预设

`recall_engine.injection_*_preset` 管理每次请求最终交给模型的记忆数量与详细度：

| 预设 | 记忆预算 | 最多条数 | 内容层级 | 适用目标 |
|---|---:|---:|---|---|
| `low_cost` | 800 字符 | 2 | 只保留关键事实 | 最小化上下文占用。 |
| `balanced` | 1200 字符 | 4 | 精简正文、事实和话题 | 默认日常使用。 |
| `quality` | 2400 字符 | 6 | 详细正文、事实、话题和参与者 | 优先上下文完整性。 |

另有 `tool_first` 预设。它不做被动记忆注入，而是尽量让 Agent 在确有需要时主动调用记忆工具，因此不属于上述三档的普通质量梯度。

::: warning 两套档位不会自动同步
把注入预设设为 `quality` 只会增加注入内容，不会自动开放额外 LLM 调用；把 `cost_control.mode` 设为 `quality` 也不会自动提高注入预算。完整档位应同时设置两组字段。
:::

## 快速选择

| 你的优先目标 | 推荐档位 | 主要代价 |
|---|---|---|
| Provider 配额充足，希望尽量提高模糊查询和复杂记忆的召回质量 | 高质量 | 更多 token、更高延迟，可能增加额外 LLM 调用。 |
| 希望日常效果稳定，同时控制上下文和费用 | 均衡 | 极复杂查询可能不会启用最高成本路径。 |
| Provider 配额有限，或更重视响应速度与上下文余量 | 低成本 | 注入记忆更少、更短，间接关联召回能力降低。 |

## 均衡档：推荐起点

均衡档使用本地 MMR 重排序，保留图检索和常规多跳召回，但默认不增加额外 LLM 调用。它最接近 Memora 的安全默认。

在 Dashboard 的 Config 页面设置：

| 配置项 | 值 | 原因 |
|---|---|---|
| `cost_control.mode` | `balanced` | 默认禁止额外 LLM 调用。 |
| `cost_control.max_extra_llm_calls_per_turn` | `0` | 明确保持每轮零次额外调用。 |
| `recall_engine.injection_routing_mode` | `manual` | 每次请求固定使用同一预设，行为最容易理解。 |
| `recall_engine.injection_manual_preset` | `balanced` | 最多注入 4 条、总计 1200 字符的精简记忆。 |
| `recall_engine.injection_delivery_override` | `auto` | 让系统按 Provider 能力选择临时投递方式。 |
| `recall_engine.injection_preset_overrides_enabled` | `false` | 先使用经过约束的内置预算。 |
| `recall_engine.top_k` | `5` | 为最终选择保留适量候选。 |
| `reranker.enabled` | `true` | 保留最终重排序。 |
| `reranker.strategy` | `mmr` | 不调用额外模型，并兼顾相关性与结果多样性。 |

以下默认能力可以继续开启：

- `graph_memory.enabled=true`
- `recall_engine.chain_graph_expansion_enabled=true`
- `recall_engine.chain_topic_expansion_enabled=true`
- `topic_segmentation.strategy=a_b_hybrid`

`memory_evolution` 与基础档位无直接绑定。没有明确需要 Relation 或 Projection 时，继续保持 `enabled=false`、`mode=disabled`。

## 高质量档：优先召回完整度

高质量档适合 Provider 上下文窗口和调用配额充足，并且模糊指代、复杂关系或长跨度历史查询较多的场景。

### 核心设置

| 配置项 | 值 | 原因 |
|---|---|---|
| `cost_control.mode` | `quality` | 允许调用高成本 LLM 能力。 |
| `cost_control.max_extra_llm_calls_per_turn` | `2` | 为查询增强或重排序留出额度，同时限制费用失控。 |
| `cost_control.allow_llm_reranker_in_passive_recall` | `true` | 明确允许被动召回使用 LLM 重排序。 |
| `recall_engine.injection_routing_mode` | `manual` | 固定使用高质量预设，避免自动降档。 |
| `recall_engine.injection_manual_preset` | `quality` | 最多注入 6 条、总计 2400 字符的详细记忆。 |
| `recall_engine.injection_delivery_override` | `auto` | 保持 Provider 兼容降级。 |
| `recall_engine.injection_preset_overrides_enabled` | `false` | 先观察内置质量预设，不立即扩大硬上限。 |
| `recall_engine.top_k` | `8` | 为详细预设提供更宽的候选池。 |
| `recall_engine.inject_with_recent_context` | `true` | 使用最近两轮对话消解“上次那个”等当前话题指代。 |
| `reranker.enabled` | `true` | 对融合后的候选执行最终排序。 |
| `reranker.strategy` | `hybrid` | 先用向量缩小候选，再交给 LLM 精排。 |

`hybrid` 重排序需要向量访问能力和同步文本生成能力；能力不足时运行时会安全降级为 MMR。若 Provider 延迟较高，可先使用 `embedding_similarity`，它只执行 Embedding 调用或本地向量计算，不执行 Cross-Encoder 联合推理或 LLM 精排。

### 可选增强

以下设置不是高质量档的必需条件，建议逐项启用并观察效果：

- 将 `graph_memory.expansion_hops` 从 `1` 调到 `2`，提高间接关系召回；二跳候选仍受 `second_hop_weight` 衰减。
- 保持 `recall_engine.max_chain_hops=3`，并继续启用图边与话题扩展。
- 只有混合话题分割持续不理想时，才把 `topic_segmentation.strategy` 改为 `strategy_d`，并同时设置 `cost_control.allow_llm_topic_strategy_d=true`。Strategy D 第一阶段占用一个额外额度；若切出多个反思批次，第 2 批及后续批次继续共享本轮剩余额度。
- 需要派生关系时，先启用 `memory_evolution.enabled=true` 并使用 `readonly` 模式观察；确认派生质量和复核流程后再评估 `active`。

::: danger 不要只设置 `mode=quality`
`cost_control.max_extra_llm_calls_per_turn=0` 会继续阻止额外调用。高质量档至少需要把该值设为正数；建议从 `2` 开始，而不是直接使用最大值。
:::

## 低成本档：优先费用和速度

低成本档减少自动注入正文并禁止额外 LLM 路径。它不会关闭 canonical 记忆写入，也不会删除已经保存的记忆。

### 核心设置

| 配置项 | 值 | 原因 |
|---|---|---|
| `cost_control.mode` | `low_cost` | 无条件禁止额外 LLM 调用。 |
| `cost_control.max_extra_llm_calls_per_turn` | `0` | 保持明确的零调用预算。 |
| `cost_control.allow_llm_reranker_in_passive_recall` | `false` | 不开放 LLM 重排序。 |
| `cost_control.allow_llm_topic_strategy_d` | `false` | 不执行两阶段 LLM 话题分割。 |
| `recall_engine.injection_routing_mode` | `manual` | 固定使用低成本预设。 |
| `recall_engine.injection_manual_preset` | `low_cost` | 最多注入 2 条、总计 800 字符的事实。 |
| `recall_engine.injection_delivery_override` | `auto` | 避免强制不兼容的传输方式。 |
| `recall_engine.injection_preset_overrides_enabled` | `false` | 防止手工预算意外扩大上下文。 |
| `reranker.enabled` | `true` | 仍保留低成本的本地重排序。 |
| `reranker.strategy` | `mmr` | 不需要额外 Provider 调用。 |

### 进一步节省本地计算

只有在 CPU、内存或响应时间同样紧张时，再追加以下设置：

| 配置项 | 建议值 | 影响 |
|---|---:|---|
| `recall_engine.top_k` | `3` | 缩小候选池，也可能漏掉相关度稍低的记忆。 |
| `recall_engine.query_rewrite_enabled` | `false` | 直接使用原始查询和内置关键词逻辑。 |
| `recall_engine.inject_with_recent_context` | `false` | 减少扩展查询构建和额外上下文。 |
| `recall_engine.max_chain_hops` | `1` | 只保留直接关联扩展。 |
| `recall_engine.chain_graph_expansion_enabled` | `false` | 跳过图边多跳扩展。 |
| `recall_engine.chain_topic_expansion_enabled` | `false` | 跳过话题多跳扩展。 |

不建议仅为节省 token 而关闭 `graph_memory.enabled`。图检索主要消耗本地计算，保留它通常比完全放弃关系召回更划算。

## 保留自动路由但偏向某档

如果希望系统根据请求和上下文余量自动调整，使用 `hybrid`，并通过最小、基础和最大预设限制浮动范围：

下表的“最低预设”“基础预设”和“最高预设”分别对应 `recall_engine.injection_hybrid_min_preset`、`recall_engine.injection_hybrid_base_preset` 和 `recall_engine.injection_hybrid_max_preset`。

| 偏向 | 最低预设 | 基础预设 | 最高预设 |
|---|---|---|---|
| 低成本 | `low_cost` | `low_cost` | `balanced` |
| 均衡 | `low_cost` | `balanced` | `quality` |
| 高质量 | `balanced` | `quality` | `quality` |

同时设置：

```text
recall_engine.injection_routing_mode = hybrid
recall_engine.injection_auto_fallback_preset = balanced
```

Hybrid 必须满足“最小档 ≤ 基础档 ≤ 最大档”。自动路由会在明确历史意图且上下文余量充足时倾向 `quality`，余量不足时倾向 `low_cost`；候选可靠时通常使用 `balanced`。

## `tool_first` 何时适用

`tool_first` 适合已经启用 `recall_long_term_memory` Agent 工具，并希望模型自行判断何时检索长期记忆的场景。

它的行为与其他预设不同：

- Provider 支持工具且记忆工具可用时，跳过被动召回与自动注入。
- Provider 或 ToolSet 不满足条件时，自动降级到 `low_cost`。
- 高级注入预算覆盖对 `tool_first` 不生效。
- 它可以减少无关记忆进入上下文，但模型也可能没有主动调用工具，因此不等同于“最高质量”。

## 应用与观察

1. 在 Dashboard 的 Config 页面按完整点路径搜索并修改字段。
2. 保存后重新加载插件，使重排序器和成本控制等启动期组件使用新配置。
3. 在 Dashboard 的 Injection 页面确认 resolved preset、候选数、最终选择数、预算和降级原因。
4. 使用 `/memora trace <query> [k]` 检查召回阶段，不要只根据一次聊天回复判断效果。
5. 用一组固定的真实问题比较修改前后的相关性、延迟和 Provider 用量；避免同时修改过多阈值。

出现响应明显变慢、Provider 限流或注入内容过多时，先恢复均衡档。修改配置不会重写 canonical 记忆，回退档位也不需要重建数据库。

完整字段说明见[召回、注入与索引配置](/reference/configuration/retrieval)和[运维、可靠性与安全配置](/reference/configuration/operations)。
