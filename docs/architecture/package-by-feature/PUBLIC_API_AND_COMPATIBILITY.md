# Public API and Compatibility

> 状态：阶段 1 契约清单，外部消费者范围待 AST-6 确认
>
> 事实提交：`f1ad662cd56766f090b6699ea81ee164783b78b6`

## 判定原则

当前仓库没有第三方插件消费者注册表，也没有发布文档承诺全部 `core.*` 具体模块
路径。因此本清单分为三类：

1. **必须保持的运行时契约**：AstrBot 插件入口、命令模块身份、配置 schema、Page
   API、事件 hook、序列化数据和已明确的核心门面。
2. **仓库支持的门面候选**：具有显式 `__all__`、根文档或契约测试的包门面。AST-6
   决定它们是否对第三方承诺一个兼容窗口。
3. **内部实现路径**：仅由生产代码、单元测试或脚本直接导入的具体模块。迁移时
   原子更新调用方，不因测试 patch 自动建立生产 shim。

没有证据时不能把入口降级为内部，也不能把所有具体路径升级为永久公共 API。

## 必须保持的运行时契约

| 契约 | 当前入口 | 迁移要求 |
|---|---|---|
| 插件类与 lifecycle | `main.py` / `MemoraPlugin` | 阶段 7 保留 AstrBot 加载路径、hooks、工具注册和关闭语义 |
| 命令 handler 所有权 | `core/command_endpoints.py` | 继续把 handler `__module__` 绑定到 `<plugin>.main`；保留热重载旧 registry 清理 |
| 插件配置 | `_conf_schema.json`、Pydantic 模型、运行时读取 | 结构迁移零字段、默认值、类型和保存语义变化 |
| Page API | `core/page_api.py` + `core/api/*` | 路由、方法、响应 envelope、错误码和 revision 语义不变 |
| 事件 hook | `main.py` decorators + `EventHandler` | hook 名、调用顺序、取消和失败隔离不变 |
| canonical 数据 | SQLite schema、整数 ID、JSON metadata | 不改 schema、ID、source revision、scope、privacy、role |
| conversation JSON | `serialize_to_json` / `deserialize_from_json` | 数据格式兼容；只移动实现 |
| AstrBot package namespace | `data.plugins.astrbot_plugin_memora` | 新门面和关键模块必须在真实命名空间导入 |

AstrBot 技能参考确认 `_conf_schema.json` 是插件配置的加载契约，`object`、`list`、
`template_list` 等类型由 AstrBot 解析；本阶段不修改 schema。技能索引中提到的
`plugin_config/hooks.md` 在本地技能包并不存在，因此 hook 行为以仓库 `main.py`、
测试和 AstrBot 实际代码为准，不依据缺失文档猜测。

## 当前包门面

| 门面 | 显式导出 | 当前结论 |
|---|---|---|
| `core` | 配置异常、conversation/graph model、`ConversationManager`、`GraphMemoryManager`、`MemoryEngine`、主要 processor/validator | 延迟加载的稳定候选；阶段 7 只能指向新领域门面 |
| `core.managers` | `ConversationManager`、`GraphMemoryManager`、`MemoryEngine`、`create_conversation_manager` | 横向门面最终删除；已确认符号转入领域门面 |
| `core.storage` | `AtomStore`、`ConversationStore`、`GraphStore`、injection decision DTO/Store | Store 默认内部；第三方承诺待确认 |
| `core.models` | conversation 与 graph models、JSON helpers | 符号转入 conversation/graph 门面或 contracts |
| `core.retrieval` | RRF/result DTO、BM25/vector/hybrid/graph/dual route retrievers | retrieval 门面候选；graph/evolution 符号归还其领域 |
| `core.identity` | identity Protocol/DTO、OneBot11/QQ Official adapter、resolver | 已有“公开轻量接口”声明，优先保持领域门面 |
| `core.injection` | injection DTO、preset、router、executor、recorder | 已有“稳定公共接口”声明，优先保持领域门面 |
| `core.monitoring` | debug/report/metrics facade 与懒加载类型 | 保留懒加载和低导入成本；拆到 shared/diagnostics 后由明确门面代理 |
| `core.evaluation` | case/report/result、指标、fixture loader、report Store | 仓库内稳定候选；report Store 是否公开待确认 |
| cognition/review 包 | 各自 DTO、Manager/Store/API helper | 转为 cognition/review 领域门面，Store/helper 默认内部 |

逐文件公共符号以 [MODULE_MIGRATION_MATRIX.md](MODULE_MIGRATION_MATRIX.md) 为完整
清单；本表只列需要架构决策的门面，不复制 329 行内容。

## 明确的 MemoryEngine 兼容候选

根协作约束和父架构讨论都把以下三个入口列为验收候选：

```python
from core import MemoryEngine
from core.managers import MemoryEngine
from core.managers.memory_engine import MemoryEngine
```

目标公共入口应收敛为 `core.memory.MemoryEngine`。AST-6 需要决定旧三层入口的支持
范围。保守方案：`core` 延迟门面在阶段 7 保留；两个 `core.managers` 入口只在已
确认存在外部消费者时纯 re-export 一个版本，并在 AST-17 按删除条件移除。

## 字符串 patch 与动态 import

AST 扫描发现 54 个目标模块存在字面量引用。类别如下：

| 类别 | 代表目标 | 动作 |
|---|---|---|
| Page API patch | `core.api.*_api.request/logger` | 与领域接口移动同提交更新测试字符串 |
| handler patch | recall/reflection/auxiliary/event handler | 与 application/orchestration 移动同提交更新 |
| Manager patch | backup、conversation、evolution、memory lifecycle | 更新测试；只有外部消费证据才建 shim |
| Store patch | affection/jargon/social/protocol identity | 更新测试对象位置，验证事务/取消语义 |
| processor/util patch | JSON/text/task/memory formatter | 更新测试；不得为 helper 建永久门面 |
| namespace import | recall trace、injection recorder、perf tracker | 在 AST-7 扩大真实 namespace 测试后迁移 |

每个目标的完整引用文件列在矩阵 `dynamic / patch refs` 字段。复现命令：

```powershell
rg -n --glob '*.py' `
  '(data\.plugins\.astrbot_plugin_memora|mock\.patch|patch\(|monkeypatch)' `
  main.py core tests scripts
```

文本结果必须由 AST 复核，避免把普通对象 `patch.object` 或说明文字误判为模块路径。

## 反射与模块身份

- `core/command_endpoints.py` 写 `handler.__module__`，属于 AstrBot 注册正确性的硬契约。
- `core/monitoring/instrumentation.py` 用 `func.__module__ + __qualname__` 形成观测 FQN。
- `core/utils/cache_manager.py` 用相同字段生成 cache name。
- `core/utils/task_scheduler.py` 用相同字段生成任务标识。
- `tests/test_plugin_init.py` 通过插件类 `__module__` 找到加载模块并 patch 运行时对象。

移动实现会改变默认 `__module__`。任何依赖该值的公共类/函数必须通过显式 facade
测试决定是否保留模块身份；不得全局伪造 `__module__` 来掩盖迁移。

## 序列化模块名

当前生产代码与测试没有 `pickle`、`dill` 或 `cloudpickle` import，未发现把 Python
类全限定模块名写入 SQLite/JSON 的实现。已存在的 JSON 序列化由显式函数和字段
驱动，不以 Python 模块名为协议。结论只适用于当前提交；AST-7 的动态门禁应继续
禁止未评审的模块名序列化。

## 兼容动作决策树

1. 入口是否属于 AstrBot/runtime/data/API 明确契约？若是，保持行为和稳定 facade。
2. 是否有当前仓库之外的已确认消费者？若未知，向 AST-6 提交决策，不擅自永久兼容。
3. 只有测试或仓库内部生产调用方？同一提交原子更新，不建 shim。
4. 必须兼容旧路径？兼容模块只能纯 re-export，不打开数据库、不注册任务、不吞异常。
5. 每个 shim 记录旧路径、允许符号、引入版本、删除版本/条件和负责 issue。
6. AST-17 在全量扫描无消费者、窗口到期且导入测试通过后删除。

## 禁止事项

- 不保留新旧两套业务实现。
- 不在 `__init__.py` 触发 Provider 探测、FAISS/SQLite 初始化、任务创建或 hook 注册。
- 不用模块级 `__getattr__` 隐式暴露所有旧内部模块；仅保留审查后的显式 lazy export。
- 不因测试 monkeypatch 方便而把 repository/mixin/helper 声明为公共 API。
- 不在结构迁移中改变异常类型、响应字段、配置、schema 或序列化格式。
