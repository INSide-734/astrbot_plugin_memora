# `core/platform/provider` Provider 能力适配

**最后核对：** 2026-08-14  
**上级：** [platform AGENTS.md](../AGENTS.md) / [core AGENTS.md](../../AGENTS.md)

## 职责边界

本目录把 AstrBot 的 LLM/Embedding Provider 不稳定入口冻结成 Memora 内部可验证 adapter。`adapters.py` 只负责入口探测、调用模式选择、返回结构规范化、向量数量/维度/有限性校验和能力快照；Provider 选择、等待和失败重试属于 [`../composition/AGENTS.md`](../composition/AGENTS.md)，业务 prompt、检索、成本策略和安全清洗属于调用方/对应 feature。

公开导出唯一来自 `core.platform.provider.__init__`。不要恢复已移除的 `core.provider_adapters` 兼容模块，也不要让业务代码直接猜测 Provider 的私有方法或返回对象形状。

## Adapter 契约

### LLM

`LLMProviderAdapter.from_provider()` 只接受存在可调用 `text_chat` 的对象，并冻结该 callable。`generate()` 保持纯文本契约；`generate_result()` 返回不可变 `LLMGenerationResult(text, prompt_tokens, completion_tokens)`。只有 Provider 明确给出非负整数 token 用量时才填充 token 字段；非法、布尔或缺失值保持 `None`。

调用必须是异步且返回带字符串 `completion_text` 的响应；非 awaitable 或缺少字符串正文抛 `AdapterResponseError(reason_code="adapter_response_invalid")`。Adapter 不负责判断文本是否安全，生成结果仍须经过 [`../security/AGENTS.md`](../security/AGENTS.md) 所述保护链。

### Embedding

`EmbeddingProviderAdapter.from_provider()` 按固定优先级选择：

1. `get_embeddings` → `NATIVE_BATCH`；
2. `get_embeddings_batch` → `COMPAT_BATCH`，构造时探测是否接受 `batch_size/tasks_limit/max_retries`；
3. `get_embedding` → `SINGLE`；
4. 三者均缺失 → `UnsupportedAdapterCapability`。

入口选择后不可在每次请求中重新探测。`embed(contents)` 对空列表返回空列表；单项模式逐项调用，扩展 batch 模式只传固定安全参数，否则使用基础 batch 调用。结果必须与输入数量相同、每项可转换为非空有限 float 列表、所有向量维度一致。

不满足约束时使用稳定 reason code：`embedding_result_invalid`、`embedding_count_mismatch`、`embedding_non_finite` 或 `embedding_dimension_mismatch`。不得把 NaN/Inf、异维向量、字符串整体结果或数量错配传给 FAISS/检索器。

## 能力与生命周期不变量

- adapter 是 `frozen` dataclass；构造后调用入口和能力模式不变，避免 Provider 在运行中热替换导致语义漂移。
- `adapter_capabilities` 必须与实际模式一致：LLM 原生文本生成、调用方取消；Embedding 原生 embedding，batch 能力按模式声明，取消由调用方保证。
- adapter 不拥有 Provider 生命周期，不关闭宿主对象；组合根负责等待、发布、取消和关闭。
- Provider 缺失/不支持时应在组合根 readiness 阶段稳定失败或继续有界等待，不能在领域调用深处静默返回空 embedding。
- `CancelledError` 不得被 adapter 的普通响应校验捕获吞掉；普通类型错误可映射为 `AdapterResponseError`，并保留安全 reason code，不把 Provider 原始响应或凭据放进异常/日志。
- adapter 不绕过成本控制、隐私过滤、Prompt protection 或写保护；它只验证调用边界和结果形状。

## 依赖方向与修改联动

依赖方向为 `composition/provider_loader.py → provider/adapters.py → shared/adapter_capabilities.py`，feature 的 LLM/Embedding 客户端只依赖 adapter 公共契约。修改入口名、调用参数、返回字段或能力分类时，必须同步：

- `ProviderLoader` 的候选过滤、等待器和 `ComponentFactory` 注入；
- `shared/adapter_capabilities.py` 的 native/caller-enforced/unsupported 语义；
- embedding singleflight、重排、索引重建和所有直接消费 adapter 的调用方；
- `core/platform/provider/__init__.py` 导出及旧 owner 移除契约；
- Provider 配置选择和最窄 adapter/provider contract tests。

不要在本目录新增“万能 Provider fallback”或复制宿主 SDK 类型；以当前 AstrBot 公开 `Provider` 接口和能力探针为准。

## 最窄验证入口

本轮只新增文档，按任务要求跳过 formatter、lint、测试和项目级验证。Provider 源码变更时优先：

```bash
python -m pytest tests/test_platform_provider_contracts.py tests/test_adapter_capabilities.py -q
python -m pytest tests/test_platform_composition_contracts.py tests/test_embedding_singleflight.py -q
```

测试应覆盖入口优先级、扩展 batch 参数探测、取消传播、数量/维度/有限性校验和稳定错误码，而不是 Provider 私有实现细节。
