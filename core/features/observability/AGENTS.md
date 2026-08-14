# 运行时可观测性

**最后核对：** 2026-08-14  
**导航：** [项目根级](../../../AGENTS.md) / [`core`](../../AGENTS.md) / [`features`](../AGENTS.md) / `observability`

## 职责边界

`core/features/observability/` 提供低开销运行时门面、Prometheus 指标、函数插桩、召回性能环形缓冲、记忆质量统计评分和隐私安全调试事件。它不保存原始 trace/query/正文，不负责业务重试，也不替代 diagnostics feature 的健康事件历史。

- `application/runtime.py`：禁用时轻量 no-op，启用后懒加载插桩与调试 reporter。
- `application/perf_tracker.py`：有界内存样本、序号、均值/方差和百分位。
- `application/quality_scorer.py`：无需 LLM 的五维记忆原子统计评分。
- `application/memory_write_timing.py`：canonical 写阶段计时辅助。
- `domain/recall_timing.py`：召回样本允许字段与规范化函数。
- `infrastructure/metrics.py`：独立 registry；无 `prometheus_client` 时使用兼容 stub。
- `infrastructure/instrumentation.py`：`@monitored` 与 ContextVar 调用树。
- `infrastructure/debug_reporter.py`：轮转 JSONL sink 与严格事件/字段 allowlist。

## 关键不变量

1. 默认关闭调试插桩；`runtime.monitored` 必须让装饰发生在启动前的函数也能响应运行时开关，关闭时不加载重依赖。
2. `sanitize_recall_sample()` 是性能样本唯一投影边界，只接受固定 timing/count/bool/status 标量；任意额外字段丢弃。
3. `PerfTracker` 容量有界，返回样本副本且不暴露内部序号以外的关联信息；百分位仅基于已保留的有限值。
4. Prometheus label 必须固定低基数。不得把 session/user/persona、query、memory ID、Provider URL 或异常消息用作 label。
5. metrics 模块缺少可选依赖时降级 stub，不能让聊天主链因监控依赖缺失而失败。
6. debug reporter 只接收 `EVENTS` 和 `ALLOWED_FIELDS` 中的字段；文本进一步受固定枚举/安全正则约束，异常只记录类型和无路径调用位置。
7. 调试文件有大小与备份数上限；配置目录/时区由 composition 注入，不把绝对路径写入事件。
8. ContextVar 每个消息操作结束后必须 reset；取消和异常不能把调用树泄漏到下一请求。
9. `MemoryQualityScorer` 是统计观测，不是 canonical 质量门或自动配置发布信号。

## 依赖方向

各运行时模块 → observability runtime/domain/infrastructure。observability 可以依赖 shared 纯工具，但不得依赖 Page API、MemoryEngine 或业务 Manager。diagnostics/API 读取安全快照；它们不能要求 observability 返回原始业务数据。

## 修改联动

- 新增指标：同步独立 registry 导出、固定 label 集合、可选依赖 stub 和监控测试。
- 新增召回样本字段：先更新 domain allowlist，再同步 producer、PerfTracker/API 和隐私 canary。
- 新增 debug 事件：同步 `EVENTS`、字段 allowlist、数值/枚举验证和所有 producer；禁止先写任意 payload 再过滤。
- 修改运行时开关：覆盖装饰前/后启用、同步/异步函数、reset 与关闭。
- 修改质量评分：同步纯统计阈值、告警模型和对应 scorer 测试，不自动接入生产配置。

## 最窄验证入口

```bash
python -m pytest -q tests/test_observability_runtime.py
python -m pytest -q tests/test_monitoring_metrics.py tests/test_monitoring_instrumentation.py
python -m pytest -q tests/test_monitoring_debug_reporter.py tests/test_p0_observability_privacy.py
python -m pytest -q tests/test_perf_tracker.py tests/test_quality_scorer.py
```
