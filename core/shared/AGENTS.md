[根级 `AGENTS.md`](../../AGENTS.md) > [core](../AGENTS.md) > **shared**

# `core/shared` 共享基础契约

**最后核对：** 2026-08-14  
**稳定入口：** `core/shared/__init__.py`  
**子包：** [`contracts/AGENTS.md`](contracts/AGENTS.md)

## 职责边界

`core/shared/` 放置被多个 feature 复用、且不属于某个业务 feature 的窄契约和基础原语。依赖主方向固定为 `feature/platform -> shared`；`shared` 不得反向导入 `platform`、`features`、组合根、Page API 或命令层。

- `contracts/`：跨 feature DTO、事件与 `Protocol` 端口，详见子文档。
- `adapter_capabilities.py`：Provider、Store、Retriever 的不可变能力快照；未知 adapter 必须按 `unsupported` 处理，不得凭方法名猜能力。
- `constants.py`：记忆注入边界与伪工具调用名，是模型上下文协议，不是展示文案。
- `cost_control.py`、`extra_llm_budget.py`：额外 LLM 功能许可和请求级 reservation 预算。
- `temporal.py`、`number_utils.py`、`text_utils.py`、`mmr.py`：无 I/O 的时间、数值、分词和本地 MMR 原语。
- `sql.py`、`list_sorting.py`：固定 SQLite 表/FTS/PRAGMA 与白名单排序片段；不拥有连接或事务生命周期。
- `domain_provenance.py`、`entity_editing.py`：跨画像、知识、笔记等领域复用的来源证据、revision/CAS 和编辑异常。
- `json_utils.py`、`data_helpers.py`、`cache_manager.py`：LLM JSON 清理、旧 metadata 容错和进程内缓存；这些模块依赖 AstrBot logger，不应被当作纯标准库契约层。
- `errors.py`：Memora 稳定异常 owner；`core` 根门面只做惰性恒等导出。
- `default_stopwords.py`、`recall_strategy.py`：共享后备停用词与召回请求枚举/DTO。

不要把只服务单一 feature 的业务规则、Store、Provider adapter 或生命周期对象上提到这里。共享并不意味着可以绕过领域校验；调用方仍拥有授权、事务和失败策略。

## 稳定入口与所有权

`core/shared/__init__.py` 只聚合最常用的注入常量和 `contracts` 中的核心类型。其他能力从具体模块导入，例如：

- `core.shared.adapter_capabilities.AdapterCapabilityContract`
- `core.shared.cost_control.CostControl`
- `core.shared.extra_llm_budget.ExtraLlmBudget`
- `core.shared.domain_provenance.DomainProvenance`
- `core.shared.entity_editing.compute_entity_revision`
- `core.shared.list_sorting.parse_sort_query`
- `core.shared.temporal.normalize_reference_time`
- `core.shared.sql.apply_perf_pragmas`

不要假设具体模块的全部 `__all__` 都会从包根转发。新增稳定导出时先确认至少两个真实生产消费者，再同步包级 `__all__` 和直接导出契约测试。

旧 `core/base` 与 `core/models` 门面已删除。仍保留的低层转发仅限无法与 owner 生命周期拆开的既有入口，例如 `core.features.memory.infrastructure.base.apply_perf_pragmas` 复用 `core.shared.sql`；不得复制实现或创建第二套类型。

## 核心不变量

### 能力与成本

- `AdapterCapabilityContract`、`ScoreSemantics` 均不可变；同一能力不能同时属于 `native` 与 `caller_enforced`。
- 未显式声明的 adapter 返回保守空契约；不支持时只暴露固定 kind/capability/reason code，不能带 query、Provider ID 或实例信息。
- `CostControl` 是许可策略，不是计费器。`quality` 允许额外能力，`low_cost` 拒绝，`balanced` 仅允许显式开关。
- `ExtraLlmBudget` 先原子 reserve；成功调用 commit，普通异常或取消 release。缺少预算、额度耗尽或功能门拒绝都不得偷偷发起 Provider 调用。
- 预算观测仅允许 `feature/allowed/used/remaining/reason_code`；未知文本归一到固定枚举，禁止记录 query、prompt、正文、ID、身份或连接配置。

### 来源、时间与编辑

- canonical memory 的整数 ID、revision、scope、privacy、role 是唯一来源证据。`DomainProvenance` 的 derived 对象必须有且仅有一个 primary source；所有来源同 scope、ID 不重复，人工 authority 优先。
- `DomainProvenance.to_dict()` 故意不保存 canonical 正文。新增字段不得把 evidence content 或业务查询带入持久化。
- 时间统一规范化为 UTC；读取链的 `reference_time` 必须由上游贯穿并进入缓存键，不得在多个下游各读墙钟。
- `compute_entity_revision()` 使用排序、无 NaN 的规范 JSON 计算 SHA-256；revision 是不透明 CAS token，不是可编辑字段。
- 列表排序键和 SQL 表达式必须来自固定 allowlist；值仍使用参数绑定。`sql.py` 只保存固定标识符和语句，不接受用户提供的表名。

### 文本、JSON 与缓存

- `safe_parse_llm_json()` 只返回 `dict/list/None`；修复模型输出不等于信任内容，领域层仍须验证字段、范围和权限。
- `json_utils.py` 当前会在 debug 日志包含失败输入前 200 字符；敏感热路径调用前必须先评估日志边界，不要把该 helper 当作脱敏器。
- `data_helpers.py` 对非法 metadata 返回空字典，是旧数据容错，不是授权成功证明。
- `CacheManager` 的命名缓存、装饰器和全局单例仅用于进程内加速。缓存键必须包含会改变结果的 scope、identity、privacy、revision 和 reference time；不得缓存可变候选后跨请求原地改分。
- `apply_mmr()` 是基于分词集合 Jaccard 的本地重排，不执行 embedding，也不替代最终隐私过滤。

## 修改联动

| 修改位置 | 必须联动核对 |
|---|---|
| `contracts/` DTO、事件、端口 | 子包文档、所有实现与消费者、`tests/test_shared_contracts.py` 及对应 feature 契约 |
| `adapter_capabilities.py` | platform provider adapter、retrieval/injection 调用方、`tests/test_adapter_capabilities.py` |
| 注入常量 | injection formatter/cleaner、Agent 工具名、`tests/test_shared_constants.py` 与注入测试 |
| 成本许可或预算 | platform config、query rewrite/reranker/反思与 proposal 调用点、shared 预算测试 |
| provenance/revision/sort | Profile/Knowledge/Note/Cognition Store 与 Page API、并发/CAS 测试 |
| 时间原语 | retrieval cache、Evolution source/projection、temporal 语义测试 |
| SQL/PRAGMA | memory/conversation/cognition Store、持久化安全测试 |
| 包级导出或异常 | `core/__init__.py` 等兼容门面、恒等导出测试、轻量导入测试 |

修改 shared API 时必须迁移所有调用方；不要新增别名或兼容双轨。若旧路径是明确的单实现 re-export，则保持对象 identity，直到专门的清理任务删除全部消费者和契约测试。

## 最窄验证入口

纯文档修改不运行测试。修改代码时按触点选择最窄命令：

```bash
python -m pytest tests/test_shared_contracts.py tests/test_shared_derived_metadata.py -q
python -m pytest tests/test_adapter_capabilities.py tests/test_shared_cost_control.py tests/test_shared_extra_llm_budget.py -q
python -m pytest tests/test_shared_temporal.py tests/test_temporal_semantics.py -q
python -m pytest tests/test_shared_entity_editing.py tests/test_shared_list_sorting.py -q
python -m pytest tests/test_shared_constants.py tests/test_shared_error_contracts.py tests/test_base.py -q
python -m pytest tests/test_cache_manager.py tests/test_mmr_reranker.py tests/test_utils.py -q
```

涉及 SQL 标识符或 provenance 时，再运行对应 Store/API 的直接测试；涉及包根导出或导入成本时加 `tests/test_plugin_package_imports.py`。
