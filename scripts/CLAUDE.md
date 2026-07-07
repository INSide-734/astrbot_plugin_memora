[根目录](../CLAUDE.md) > **scripts**

## 模块职责

`scripts/` 目录包含 Memora 项目的 CI/CD 质量门禁脚本和本地开发辅助工具。所有脚本均为独立运行（非库模块），通过命令行调用。

## 入口与启动

| 脚本 | 执行方式 | 职责 |
|------|---------|------|
| `check_all.py` | `python scripts/check_all.py` | 统一本地质量门禁：顺序运行所有检查步骤 |
| `run_smoke.py` | `python scripts/run_smoke.py [-q]` | 运行集成冒烟测试套件 (5 个 pipeline) |
| `check_dashboard_build_artifacts.py` | `python scripts/check_dashboard_build_artifacts.py [dashboard_dir]` | 验证 Dashboard 构建产物的兼容性 |

## 关键依赖与配置

- **Python**: 所有脚本使用 Python 3.12+ 标准库 (`subprocess`, `pathlib`, `shutil`, `sys`, `time`, `html.parser`)
- **pytest**: `run_smoke.py` 和 `check_all.py` 的后端测试依赖 pytest
- **npm**: `check_all.py` 的 Dashboard 步骤依赖 npm
- **uv** (可选): `run_smoke.py` 优先使用 `uv run pytest` 加速启动

## 脚本详解

### 1. check_all.py -- 统一质量门禁

位置: `scripts/check_all.py` (119 行)

**执行顺序**:

| 步骤 | 命令 | 目录 | 说明 |
|------|------|------|------|
| 0 | `python scripts/validate_conf_schema.py` | 项目根 | 验证配置 schema (文件存在时) |
| 1 | `pytest tests -q` | 项目根 | 后端回归测试 |
| 2 | `python scripts/run_smoke.py -q` | 项目根 | 冒烟测试 |
| 3 | `npm run build` | `pages/dashboard/` | Dashboard 生产构建 |
| 4 | `python scripts/check_dashboard_build_artifacts.py` | 项目根 | Dashboard 产物检查 |
| 5 | `npm run test` | `pages/dashboard/` | Dashboard 前端测试 (Vitest) |
| 6 | `npm run smoke:runtime` | `pages/dashboard/` | Dashboard 运行时冒烟 |
| 7 | `npm run smoke:browser` | `pages/dashboard/` | Dashboard 浏览器冒烟 (Playwright) |

**失败策略**: 任一步骤失败立即退出并返回该步骤的退出码，不继续执行后续步骤。

**命令解析**: `_resolve_command()` 自动处理 Windows 平台的 `.cmd`/`.exe` 后缀和 Unix 的 PATH 查找。

**执行样式**: 每步打印标题、工作目录、完整命令、耗时和 PASSED/FAILED 状态。

### 2. run_smoke.py -- 集成冒烟测试

位置: `scripts/run_smoke.py` (68 行)

**覆盖的 5 个 Pipeline**:
| Pipeline | 测试文件 | 说明 |
|----------|---------|------|
| Ingest | `tests/integration/test_pipeline_ingest.py` | 消息接入管线 |
| Event | `tests/integration/test_pipeline_event.py` | 事件处理管线 |
| Retrieval | `tests/integration/test_pipeline_retrieval.py` | 检索管线 |
| Graph | `tests/integration/test_pipeline_graph.py` | 图记忆管线 |
| Lifecycle | `tests/integration/test_pipeline_lifecycle.py` | 生命周期管线 |

**执行方式**: 每个 pipeline 独立运行，不允许单个失败影响其他 pipeline 的执行。最后汇总通过/失败计数。

**pytest 调用策略**:
1. 优先使用 `uv run pytest <target>` (如果 uv 可用)
2. 回退到 `python -m pytest <target>`

**文件检查**: 运行前检查所有 SMOKE_TARGETS 是否存在，缺失则返回退出码 2。

### 3. check_dashboard_build_artifacts.py -- Dashboard 产物验证

位置: `scripts/check_dashboard_build_artifacts.py` (112 行)

**检查项目** (全部失败则返回错误列表):

| 检查项 | 说明 |
|--------|------|
| `index.html` 存在 | 构建产物必须包含 index.html |
| `assets/` 目录存在 | 静态资源目录 |
| 无 `.vite-build/` 残留 | 临时构建目录应被清理 |
| 无 `/src/main` 引用 | index.html 不应引用开发入口文件 |
| 无 `type="module"` 脚本 | 生产构建应为 legacy 脚本 |
| 无 `crossorigin` 属性 | 生产构建不需要此属性 |
| JS bundle 数量 = 1 | assets 目录应只有一个 .js 文件 |
| CSS bundle 数量 = 1 | assets 目录应只有一个 .css 文件 |
| 所有引用的资源文件存在 | index.html 中的 src/href 链接可解析 |

**实现**: 使用自定义 `HTMLParser` 子类 `_AssetParser` 解析 index.html，收集脚本、样式表和 crossorigin 标签信息。

## 测试与质量

脚本本身是测试基础设施的一部分，用于 CI/CD 流水线自动验证。它们不包含自身的单元测试，但通过以下方式验证正确性：
- `check_all.py` 在 CI 环境中每日运行
- `run_smoke.py` 的 SMOKE_TARGETS 列表在修改集成测试结构时需同步更新
- `check_dashboard_build_artifacts.py` 与 Vite 构建配置耦合，构建配置变更时需相应更新检查逻辑

## 常见问题 (FAQ)

**Q: 如何只运行后端的质量门禁？**
A: 直接运行 `python -m pytest tests -q` 和 `python scripts/run_smoke.py -q`，跳过 Dashboard 步骤。

**Q: smoke 测试某个 pipeline 失败怎么办？**
A: `run_smoke.py` 会继续运行其他 pipeline，最后汇总所有结果。查看失败 pipeline 的 pytest 输出定位具体原因。

**Q: check_all.py 在第 0 步提示找不到 validate_conf_schema.py？**
A: 这是正常的，该脚本仅在项目包含配置 schema 验证器时才执行。缺少时不阻塞后续步骤。

**Q: 能否在本地跳过 Dashboard 步骤？**
A: 可以手动运行单独的步骤而非使用 `check_all.py`。`check_all.py` 目前没有命令行选项来跳过步骤。

## 相关文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `check_all.py` | 119 | 统一质量门禁入口 |
| `run_smoke.py` | 68 | 冒烟测试执行器 |
| `check_dashboard_build_artifacts.py` | 112 | Dashboard 构建产物兼容性检查 |

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 深度扫描 | 完整读取 3 文件，生成 `scripts/CLAUDE.md` |
