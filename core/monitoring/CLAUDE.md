[根目录](../../CLAUDE.md) > [core](../) > **monitoring**

## 模块职责

`core/monitoring/` 是 Memora 的可观测性模块，提供 Prometheus 指标收集、函数级性能插桩、召回管线性能追踪与记忆原子质量评分。4 个源文件 + `__init__.py`。

所有重依赖均为懒加载，导入此包耗时小于 1ms。

## 入口与启动

- **对外导出**: `monitored`, `PerfTracker`, `MemoryQualityScorer`, `QualityScore`, `QualityAlert`, `AlertLevel`, `set_debug_mode`, `reset_trace_context`
- **调用方**: 全项目通过 `@monitored` 装饰器或直接导入指标/追踪器使用

### 懒加载机制

```python
# 默认：zero-overhead stub
from core.monitoring import monitored  # no-op decorator, <1ms

# 启用调试模式：懒加载真实 instrumentation
from core.monitoring import set_debug_mode
set_debug_mode(True)  # 首次调用时加载 prometheus_client + instrumentation
```

## 对外接口

### Metrics (`metrics.py`)

独立 Prometheus `CollectorRegistry`，不污染进程级默认注册表。`prometheus_client` 不可用时降级为 no-op stub。

**召回管线指标**：

| 指标 | 类型 | 标签 | 含义 |
|------|------|------|------|
| `memora_recall_duration_seconds` | Histogram | `stage` (bm25/vector/graph/hybrid/rerank) | 各阶段延迟 |
| `memora_recall_requests_total` | Counter | -- | 召回请求总数 |
| `memora_recall_cache_hits_total` | Counter | -- | 缓存命中 |
| `memora_recall_cache_misses_total` | Counter | -- | 缓存未命中 |

**记忆写入指标**：

| 指标 | 类型 | 标签 | 含义 |
|------|------|------|------|
| `memora_memory_write_duration_seconds` | Histogram | -- | 写入延迟 |
| `memora_memory_atoms_stored_total` | Counter | -- | 原子存储总数 |
| `memora_memory_write_failures_total` | Counter | `stage` | 按阶段失败计数 |

**写协调器指标**：

| 指标 | 类型 | 标签 | 含义 |
|------|------|------|------|
| `memora_write_operations_total` | Counter | -- | 协调写入操作总数 |
| `memora_write_lock_retries_total` | Counter | -- | SQLite 锁冲突重试 |
| `memora_write_failures_total` | Counter | `reason` | 按原因失败计数 |

**插桩指标**（调试模式）：

| 指标 | 类型 | 标签 |
|------|------|------|
| `memora_instrumented_latency_seconds` | Histogram | `function` |
| `memora_instrumented_calls_total` | Counter | -- |
| `memora_instrumented_errors_total` | Counter | -- |

### Instrumentation (`instrumentation.py`)

`@monitored` 装饰器，支持同步/异步函数：

- **零开销模式**（默认）：`@monitored` 为 no-op，原样返回函数
- **调试模式**：记录调用次数、延迟直方图（带全限定函数名标签）、错误计数
- **调用树追踪**：可选的嵌套调用树日志（`_trace_depth` contextvar），每次 `reset_trace_context()` 清理
- 指标对象预缓存，避免热点路径重复字典查找

### PerfTracker (`perf_tracker.py`)

召回链路使用的环形缓冲区性能跟踪器：

- **容量**: 200 条（默认）
- **追踪维度**: `total_ms`, `bm25_ms`, `vector_ms`, `graph_ms`, `rerank_ms`
- **统计方法**: Welford 在线算法（mean, variance, std）
- **分位数**: 线性插值计算任意分位数（`get_percentile(key, p)`）
- **查询**: `get_perf_data(recent_limit)` 返回 avg/std + 最近 N 条

### MemoryQualityScorer (`quality_scorer.py`)

纯统计记忆原子质量评分器，不依赖 LLM：

**五维评分**（各 0.0-1.0）：

| 维度 | 权重 | 评分逻辑 |
|------|------|---------|
| `consistency` (一致性) | 0.25 | Jaccard 与现有原子重叠度；有向量则用余弦相似度 |
| `coherence` (连贯性) | 0.25 | 长度惩罚 + 连接词检测 + 矛盾情感检测 + 段落结构 |
| `relevance` (相关性) | 0.20 | 60% 来源先验 + 40% Jaccard 匹配近期上下文 |
| `freshness` (新鲜度) | 0.15 | TTL 剩余比例，半衰期后加速衰减 |
| `accuracy` (准确性) | 0.15 | 来源可靠性表（admin 0.95, private 0.75, group 0.55） + URL/验证加分 |

**告警系统**：
- 4 级: `CRITICAL (<0.30)`, `HIGH (<0.45)`, `MEDIUM (<0.60)`, `INFO`
- 每维度带中文处理建议

**自动暂停**：
- 连续 5 次综合分低于 0.30 → 暂停
- 1 小时内 2 次 CRITICAL 告警 → 暂停
- `get_stats()` 返回状态、平均分、告警分布、最近 10 条

**辅助统计函数**（模块私有）：
- `_tokenize()` -- 委托给 `text_utils.tokenize_bigrams()`
- `_cosine_similarity()` -- 向量余弦相似度
- `_text_to_simple_embedding()` -- 字符 n-gram 哈希伪向量（64 维，兜底用）
- `_count_connectors()` -- 三类逻辑连接词计数
- `_has_contradictory_sentiment()` -- 正负情感共存检测
- `_has_url()` -- URL 检测

## 关键依赖与配置

- **外部库**: `prometheus_client`（可选，降级为 no-op stub）, `numpy`, `faiss`
- **内部依赖**: `core.utils.text_utils.tokenize_bigrams`, `core.storage.base.apply_perf_pragmas`, `astrbot.api.logger`

## 数据模型

| 数据类 | 文件 | 字段 |
|--------|------|------|
| `QualityScore` | `quality_scorer.py` | atom_id, consistency, coherence, relevance, freshness, accuracy, overall, timestamp |
| `QualityAlert` | `quality_scorer.py` | level(AlertLevel), dimension, score, threshold, message, suggestion, timestamp |

## 测试与质量

- 对应测试文件: `tests/test_monitoring.py` (推测)
- 所有重依赖为懒加载（模块导入 <1ms）
- Metrics 降级为 no-op stub，不会因缺少 prometheus_client 而崩溃
- `@monitored` 默认零开销，仅在 `set_debug_mode(True)` 后激活

## 常见问题 (FAQ)

**Q: 如何在生产环境启用 Prometheus 指标？**
A: 安装 `pip install prometheus_client` 即可。无需代码修改，metrics 模块自动检测可用性。

**Q: 如何启用函数级性能追踪？**
A: 调用 `set_debug_mode(True)` 即可。注意这会产生每个函数的直方图统计开销，适合调试环境。

**Q: 质量评分的权重可以调整吗？**
A: 修改 `MemoryQualityScorer._WEIGHTS` 字典即可。当前权重：consistency 0.25, coherence 0.25, relevance 0.20, freshness 0.15, accuracy 0.15。

## 相关文件清单

- `__init__.py` -- 懒加载入口 + stub 管理（113 行）
- `instrumentation.py` -- `@monitored` 装饰器 + 调用树追踪（196 行）
- `metrics.py` -- Prometheus 指标 + 降级 stub（151 行）
- `perf_tracker.py` -- 环形缓冲区 Welford 追踪器（135 行）
- `quality_scorer.py` -- 五维统计评分 + 告警系统（646 行）

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取 4 个源文件，生成模块级 CLAUDE.md |
