# Dependency Rules

> 状态：AST-7 自动门禁的阶段 1 规范

## 总规则

```text
interfaces -> application -> domain
infrastructure -> application ports + domain
platform/bootstrap -> facades + concrete infrastructure
shared -> stdlib/third-party only
```

跨领域只允许导入目标领域 `__init__.py` 或显式 `contracts.py`。任何领域不得访问
另一领域的 `application` 内部模块、`infrastructure`、repository、mixin、Page API
实现或下划线私有模块。

## 允许矩阵

| 导入方 | 可导入 | 条件 |
|---|---|---|
| `<domain>/domain` | stdlib、同领域 domain、shared/kernel 中纯类型 | 不得有框架或 I/O |
| `<domain>/application` | 同领域 domain、同领域 ports、其他领域 facade/contracts、shared | 不构造 concrete infrastructure |
| `<domain>/infrastructure` | 同领域 domain/application ports、shared 技术 primitive、第三方驱动 | 不调用其他领域 infrastructure |
| `<domain>/interfaces` | 同领域 application/domain、平台接口类型 | 不维护第二份业务状态 |
| `platform/bootstrap` | 所有领域 facade/ports/concrete infrastructure | 唯一 composition root |
| `platform/events/page/commands` | 领域 facade/application public use case | 不导入 repository/helper |
| `shared/*` | stdlib、第三方库、其他 shared 子包 | 不依赖 platform 或业务领域 |
| tests | 所属领域公开/内部实现 | 跨领域集成优先通过 facade；patch 字符串也受扫描 |
| scripts | 稳定 facade 或明确受测 benchmark support | scripts 不是生产 API |

## 禁止边

- `domain -> astrbot/quart/aiosqlite/sqlalchemy/faiss/httpx`。
- `shared -> memory/conversation/.../operations`。
- `<domain A> -> <domain B>/infrastructure`。
- `<domain A> -> <domain B>/_private` 或具体 mixin/helper。
- `application -> concrete Store/Provider`，除 composition root 或明确 legacy exception。
- `infrastructure -> interfaces`。
- `core/* -> main.py`。
- 业务模块 -> `tests` 或 `scripts`。
- 包门面通配符导出、import-time I/O、任务启动、hook 注册或重依赖探测。
- 顶层绝对 `from core...` 破坏 AstrBot package namespace；包内默认使用正确相对导入。

## Shared 准入

一个能力进入 shared 必须同时满足：

1. 没有 memory、graph、conversation、profile 等业务语义；
2. 至少被两个领域稳定复用，或属于不可分割的 composition primitive；
3. API 小且命名明确，不以 `utils.py`、`helpers.py`、`common.py` 作为新杂项桶；
4. 不依赖业务 DTO；若需要业务 DTO，能力应返回所属领域；
5. 有独立测试或被两个领域的契约测试覆盖。

当前提案将 provenance/temporal 放入 shared/kernel，将 SQLite connection/transaction
primitive 放入 shared/persistence，将 cache/task/text 放入具名子包。AST-6 可调整，
但不得复制到多个领域。

## Domain 门面规则

- 使用显式 import 和 `__all__`。
- 只导出跨域所需 DTO、Enum、Protocol、异常和 facade/use case。
- `__init__.py` 建议不超过 100 行。
- 懒加载只为避免真实循环或重依赖，且有 import-cost/namespace 测试。
- 门面不能导出具体 SQLite Store、FAISS adapter、Page API mixin 或测试 helper，除非
  AST-6 明确批准其为外部公共契约。

## Composition Root 规则

只有 `platform/bootstrap` 可以：

- 同时导入多个领域 concrete implementation；
- 构造 SQLite/FAISS Store、Provider adapter、Recorder、Scheduler 和 Worker；
- 把 infrastructure 注入 application ports；
- 决定初始化和关闭顺序；
- 发布供 events/page/commands 共享的实例。

领域、请求处理器和 Page API 不得按请求创建第二套 runtime 组件。

## 测试依赖

单元测试可以导入所属领域内部实现，以测试算法和失败边界；这不把该路径升级为
生产公共 API。integration/stress 测试跨领域时优先从 facade 或 composition fixture
装配。全局 `tests/conftest.py` 是阶段 7 独占文件，领域任务不得提前改动。

测试中的以下引用同样受门禁：

- `patch("core....")`；
- `monkeypatch.setattr("core....")`；
- `importlib.import_module("...")`；
- `sys.modules[...]` 的插件命名空间；
- `spec_from_file_location` 的模块名；
- 基于 `__module__` 的查找。

## 自动门禁要求

AST-7 应提交一个受测的结构检查器，至少完成：

1. 解析生产、测试和脚本的 `Import`/`ImportFrom`，相对 import 归一化为绝对模块。
2. 解析字面量 patch/import/module spec，并报告 source、target、kind 和行号。
3. 根据当前迁移阶段同时理解 legacy path 与 target path，避免迁移中误报全部旧代码。
4. 拒绝 shared 反向依赖、domain 框架依赖、跨域 infrastructure/private import。
5. 检测生产模块循环依赖，并输出最短可读 cycle。
6. 检查每个目标业务文件只有一个领域所有者。
7. 检查 facade 的显式 `__all__` 与 import-time side effect allowlist。
8. 在真实 `data.plugins.astrbot_plugin_memora` 命名空间导入所有新 facade。
9. 对无法解析、未知领域或规则配置漂移返回非零退出码。

门禁必须有正例和负例测试，不能只扫描当前树然后永远返回成功。规则配置属于架构
契约，修改时同步本文件、目标结构、矩阵和测试。

## 迁移期规则

每个阶段只允许两类跨结构边：

- 已批准的旧 facade 纯 re-export 到新领域门面；
- platform composition root 在接入清单中明确的临时装配边。

临时边必须写入兼容清单，包含负责 issue、移除阶段和验证命令。新实现不得反向导入
旧实现；旧 facade 只能单向导出新实现，防止循环和双轨。

## 评审阻塞条件

以下任一项阻塞阶段推进：新循环依赖；shared 引入业务语义；domain 引入框架；跨域
访问 infrastructure/private；未知目标领域；facade import-time I/O；兼容层没有删除
条件；为通过门禁扩大 allowlist 或忽略整个目录。
