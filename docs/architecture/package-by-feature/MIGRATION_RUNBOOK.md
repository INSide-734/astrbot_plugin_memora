# Domain Migration Runbook

> 状态：后续领域任务的执行规范
>
> 本文件不授权提前启动 backlog 阶段

## 前置条件

迁移者开始一个领域前必须确认：

- AST-5 已将对应阶段从 `backlog` 提升为 `todo`；
- 前一阶段屏障已关闭，AST-6 对目标路径的修改项已合入；
- 工作树无不明改动，当前分支与目标 PR 范围明确；
- [MODULE_MIGRATION_MATRIX.md](MODULE_MIGRATION_MATRIX.md) 中该领域 source、target、
  importer、dynamic refs、side effects 和验证键均已复核；
- 对应源/目标目录的 `AGENTS.md` 已读取；
- 共享入口接入不需要提前修改阶段 7 独占文件。

## 1. 建立行为基线

1. 运行领域精确测试和真实 AstrBot namespace import。
2. 数据敏感领域增加或确认事务、失败、取消、rollback 和重建测试。
3. 记录命令、退出码、通过/失败数和环境，不用 collection 成功代替行为成功。
4. 若基线已失败，先定位是否为现有缺陷；结构 PR 不静默修改算法来“顺便修复”。

精确命令见 [TEST_AND_ACCEPTANCE.md](TEST_AND_ACCEPTANCE.md)。

## 2. 创建最小目标包

- 只创建该领域实际需要的层级。
- 先定义稳定 facade/contracts 和 application ports，再移动 concrete implementation。
- `__init__.py` 使用显式 `__all__`，不做 I/O、注册或任务启动。
- 保留现有类、函数、异常、参数、返回类型和异步语义。
- 新文件建议不超过 600 行，硬上限 800 行；按职责拆分，不使用 `part1/part2`。

## 3. 原子移动实现

按一个可回滚领域批次完成：

1. 移动 domain model/policy；
2. 移动 application use case/coordinator；
3. 移动 infrastructure repository/adapter；
4. 移动 interfaces Page API/command/tool/event adapter；
5. 更新同领域生产 import；
6. 更新跨领域调用为 facade/contracts；
7. 更新测试、脚本、文档和 AGENTS 导航；
8. 更新全部字符串 patch/dynamic import 和模块身份测试。

包内使用相对 import，确保既能在仓库测试环境导入，也能在
`data.plugins.astrbot_plugin_memora` 命名空间导入。不要把包写死为顶层 `core`。

## 4. 兼容处理

默认动作是原子更新内部调用方，不建 shim。只有 AST-6 确认的外部公共路径可建立
纯 re-export：

```python
"""Deprecated compatibility facade; remove under the approved AST-17 condition."""

from ...target_domain import PublicType

__all__ = ["PublicType"]
```

兼容 facade 不复制实现、不捕获异常、不做 I/O、不创建任务。PR 说明旧路径、允许
符号、兼容期限、删除条件和验证。对 test patch 字符串只更新测试，不保留 shim。

## 5. 共享入口接入

阶段 4 至 6 的领域任务不得修改：

- `main.py`；
- `core/__init__.py`；
- `core/plugin_initializer.py` 与 `core/initializer/*` 的平台聚合部分；
- `core/page_api.py`；
- `tests/conftest.py`；
- Dashboard 根路由、Provider、启动入口和 bridge 注册。

领域 PR 应提交接入清单：新 facade、要构造的 port/concrete implementation、生命周期
顺序、Page/command/tool 注册项、旧入口删除条件。AST-15 在阶段 7 串行消费清单。

## 6. 数据与行为审计

结构迁移 diff 中以下变化一律拆出：

- SQL、schema、table/index/PRAGMA、migration；
- API route/method/field/error code；
- 配置键、默认值、校验或保存行为；
- FAISS/FTS/canonical ID、排序、阈值、过滤或降级；
- 事务边界、lock、timeout、retry、cancel、rollback；
- prompt、privacy、日志、指标或 trace 字段；
- 备份路径、hash、quick check、恢复 journal；
- 算法和业务行为重构。

发现真实缺陷时另开修复任务；除非修复是安全完成移动的必要条件并经 Lead 批准，
否则不混入结构 PR。

## 7. 验证顺序

1. 目标文件 Ruff check/format/check；
2. 领域最窄单测；
3. 相关 integration/stress；
4. 真实 AstrBot namespace import；
5. 架构依赖门禁；
6. 本 PR 文件 pre-commit；
7. `git diff --check` 与相对 Markdown 链接检查；
8. 按阶段要求扩大到 `scripts/check_all.py` 和 benchmark。

自动修复或 hook 改写文件后必须审查 diff 并重新运行。不得使用 `--no-verify`、
`SKIP`、宽泛 noqa 或扩大 exclude 伪造通过。

## 8. 提交与 PR

- 一个领域一个独立提交或 PR；大领域可按 domain/application/infrastructure 分提交，
  但 PR 仍保持单一领域和可整体 revert。
- PR 标题、正文或分支必须含对应 AST 标识，例如 `AST-13`，使 Multica 可建立链接。
- 需要合并后关闭任务时在 PR title/body 使用相邻 close intent，例如
  `Closes AST-13`；普通 `Related AST-13` 只作参考且可能不显示在 issue PR 列表。
- PR 说明 source/target、公共符号、动态引用、行为零变化审计、测试证据、兼容窗口、
  接入清单和回滚。
- 不提交 `.venv`、`node_modules`、dist、缓存、测试数据库、截图或临时报表。

## 9. 回滚

正常回滚是 revert 该领域 PR：

1. 停止阶段推进；
2. revert 领域提交和同提交兼容/导入调整；
3. 恢复旧 facade 和旧模块路径；
4. 运行相同领域测试、namespace import 和数据一致性检查；
5. 确认不需要数据库回滚，因为结构 PR 不改变 schema/data；
6. 在 AST-5 记录触发条件、影响和重新进入阶段的标准。

若结构 PR 需要数据库恢复，说明它已经越过本 runbook 的范围，应阻塞合并并拆分。

## 领域专项核对

| 领域 | 迁移前后必须相同 |
|---|---|
| Graph | CRUD/query/delete/subgraph、transaction、source_memory_id、0..2 hop、canvas snapshot |
| Memory | canonical ID、write coordinator、journal/repair、FTS/FAISS/Graph 重建、soft delete |
| Backup | path containment、SHA-256、quick check、staged/apply/rollback/cancel、write guard |
| Conversation | message/session JSON、ordering、identity scope、dedup、reset 和 close |
| Evolution | source revision、job idempotency、relation/projection validity、mode 语义 |
| Injection | dynamic memory 不进 System Prompt、原子 request mutation、allowlist telemetry |
| Identity | protocol namespace、canonical ID、ambiguous alias fail-closed、隐私日志 |

详细不变量见 [SECURITY_AND_DATA_SAFETY.md](SECURITY_AND_DATA_SAFETY.md)。

## 完成定义

领域文件全部落在矩阵目标或经 AST-6 批准的新目标；门面是唯一跨域入口；没有新循环
或非法依赖；精确测试和阶段门禁通过；兼容/接入/回滚证据完整；diff 不含行为、数据
或无关重构。只有这些条件满足后，AST-5 才能计入阶段屏障。
