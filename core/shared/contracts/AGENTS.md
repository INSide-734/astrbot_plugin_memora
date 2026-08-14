[根级 `AGENTS.md`](../../../AGENTS.md) > [core](../../AGENTS.md) > [shared](../AGENTS.md) > **contracts**

# `core/shared/contracts` 跨 Feature 契约

**最后核对：** 2026-08-14  
**稳定入口：** `core/shared/contracts/__init__.py`

## 职责边界

本包定义组合根和多个 feature 共同使用的最小 DTO、事件与结构化 `Protocol`。它只依赖标准库及 `core.shared.temporal`，不得导入 AstrBot、SQLite、Provider 实现、任何 feature 或 platform 组合层。

- `canonical_source.py`：canonical 来源快照、fail-closed 读取请求/结果、拒绝枚举、只读端口与取消检查。
- `events.py`：canonical 事务提交后的无正文事件 `CanonicalMemoryCommitted`。
- `ports.py`：实时发布、成本门、canonical CRUD、派生发布、召回、反思写入、embedding、连续性、身份会话、最终可见性和提示词保护的窄端口。
- `conversation.py`：`Message`、`Session`、`MemoryEvent` 及 JSON 转换；当前由 conversation feature 的领域路径恒等转发。
- `derived_metadata.py`：派生注解可保存的最小 canonical source provenance。
- `identity.py`：稳定身份 metadata schema 版本。
- `prompt_protection.py`：请求作用域在 AstrBot event extra/attribute 中使用的固定键。

契约只描述边界，不创建实现、不执行 I/O、不持有生命周期，也不决定具体 feature 的业务降级。若类型只被一个 feature 使用，应留在该 feature 的 domain 层。

## 稳定入口

`core.shared.contracts` 公开聚合当前跨 feature 常用类型：

- source：`MemorySourceRef`、`SourceReadRequest`、`SourceReadResult`、`SourceReadDenyReason`、`CanonicalSourceReaderPort`；
- commit/event：`CanonicalMemoryCommitted`、`DerivedWorkPublisher`；
- ports：`CanonicalMemoryPort`、`RecallPort`、`ReflectionWritePort`、`CostControlPort`、`IdentityConversationPort`、`FinalVisibilityPort`、`PromptProtectionPort`、`RealtimePublisher`；
- metadata/identity：`DerivedMetadataSourceRef`、`IDENTITY_SCHEMA_VERSION`；
- helpers：`raise_if_cancelled()`、`to_derived_metadata_source()`。

`EmbeddingPort`、`ContinuityPort` 和长名称别名 `CanonicalSourceReadRequest/Result` 存在于具体模块，但当前未从包级 `__init__.py` 聚合。调用方不得假设“模块中定义”就等于“包根稳定导出”；改变聚合范围必须同步消费者和导出测试。

## Canonical Source 不变量

### `MemorySourceRef`

- `memory_id` 必须是正整数且不能是 `bool`；`revision_token`、`scope_key` 必须非空。
- privacy 只允许 `public/shared/confidential`，source role 只允许 `primary/supporting`。
- `occurred_at` 必须存在并规范化为 UTC；validity 区间不能倒置，时间来源与精度必须使用 shared 的固定枚举。
- 授权后的本地 `content` 上限为 4000 字符；持久化 provenance 和派生 metadata 默认不得保存正文。
- topic 去重并限制数量/长度；`stable_user_id` 可为空仅为兼容缺少旧证据的来源，读取授权请求本身仍要求稳定身份。

### `SourceReadRequest` / `SourceReadResult`

- 请求必须携带精确非空 scope、privacy clearance、stable user ID、合法 user/source role、受限正文预算和每个 memory ID 的 expected revision。
- 非法或缺失授权信息必须 fail closed。拒绝结果只能包含稳定 `SourceReadDenyReason`，不能同时携带任何 source 或正文。
- `read_many()` 中单条拒绝不能泄露其他请求的正文；实现仍须逐条核对 source metadata、scope、identity、privacy、role、revision 和 validity。
- `raise_if_cancelled()` 同时识别任务取消和常见显式 token；`CancelledError` 必须传播，不能变成空成功。
- `to_derived_metadata_source()` 只复制 memory/revision/scope/privacy/role/stale 等受限证据，不复制 content。

## 事件与端口不变量

- `CanonicalMemoryCommitted` 只能在 canonical 事务成功后发布。事件含单个 memory ID、revision、scope/privacy/identity/role、变更字段、正文 SHA-256 摘要和 UTC 时间；不得加入 query、prompt、正文、ID 列表或任意 metadata。
- `event_revision_key` 只是事件与 revision 组合；派生 outbox 的完整幂等键还必须包含 consumer。
- `CanonicalMemoryPort` 操作 canonical 权威记录；实现必须保留 revision/CAS 和持久化边界。
- `DerivedWorkPublisher.publish_committed()` 只有派生工作已可靠发布才返回成功；取消不能 ack。
- `RecallPort` 的返回值必须已完成身份和隐私过滤；`FinalVisibilityPort` 是注入前的最终本地过滤边界。
- `ReflectionWritePort` 的质量门失败不得推进 canonical 窗口。
- `EmbeddingPort` 只在显式能力可用时调用，且输入必须已授权。
- `IdentityConversationPort` 连接协议身份与会话，不授权 feature 自建第二个 runtime。
- `PromptProtectionPort` 的 scope 必须由调用方显式管理和释放；动态记忆仍不得写入 System Prompt。
- 所有 `@runtime_checkable` 协议用于窄结构检查，不替代输入验证、授权或生命周期所有权。

## Conversation 与固定键

- `conversation.py` 只依赖标准库。`Message.content_to_text()` 负责把常见消息段变成存储/LLM 文本；只有媒体时使用稳定占位文本。
- `from_dict()` 对 JSON 字符串 metadata/participants 容错，但缺失必填字段仍会失败；空字典回退不是可信授权证明。
- `Message.format_for_llm()` 会生成模型可见文本，改动 sender、时间或 role 语义时必须同步 conversation formatter 与消息管线测试。
- `PROMPT_PROTECTION_*` 四个键连接事件 extra/attribute 与安全服务，是跨层固定协议；重命名必须一次迁移生产写入、读取和清理调用方。

## 依赖方向与修改联动

```text
feature application/domain --> core.shared.contracts <-- platform adapters
platform/composition --------^                  ^------ tests/fakes
```

- 新增或修改字段：同时更新构造点、序列化/反序列化、实现端、消费者、隐私 allowlist 和契约测试。
- 修改 `MemorySourceRef` 或 source-reader：联动 memory source reader、Evolution/Projection、Profile/Knowledge/Note proposal、temporal 与 P0 source integrity 测试。
- 修改 commit event/派生端口：联动 canonical 写入 hooks、outbox/worker、重建与幂等测试。
- 修改 recall/reflection/final visibility：联动 handler、retrieval/injection、质量门和隐私 canary。
- 修改 conversation 模型：联动 `core/features/conversation/` Store/Manager/formatter 和旧模型恒等 re-export。
- 修改包级 `__all__`：同步 `core/shared/__init__.py`（若需要根转发）及所有 import contract；不要为方便引入通配的第二层门面。

清理旧路径必须一次迁移全部调用方；除了当前代码明确保留的同对象 re-export，不新增 shim、别名类型或重复 dataclass。

## 最窄验证入口

纯文档修改不运行测试。契约代码改动按范围选择：

```bash
python -m pytest tests/test_shared_contracts.py tests/test_p0_source_revision_integrity.py -q
python -m pytest tests/test_shared_derived_metadata.py tests/test_memory_domain_authority.py -q
python -m pytest tests/test_identity_conversation_port.py tests/test_conversation_store.py tests/test_conversation_formatter.py -q
python -m pytest tests/test_memory_evolution_hooks.py tests/test_memory_evolution_gate.py -q
python -m pytest tests/test_privacy_safe_pipeline_events.py tests/test_injection_executor.py -q
```

如果改变公开 import 面，再加 `tests/test_plugin_package_imports.py`；如果改变模型可见字段或 source provenance，再加 `tests/test_p0_observability_privacy.py` 和直接消费该契约的 feature 测试。
