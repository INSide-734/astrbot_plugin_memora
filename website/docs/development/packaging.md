# 打包与发布

本页说明插件 ZIP 的生成、产物校验与发布门禁。脚本完整语义见 [`scripts/AGENTS.md`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/scripts/AGENTS.md)。

## 生成安装包

从仓库根目录执行：

```powershell
uv run --locked python scripts/package_plugin.py
uv run --locked python scripts/package_plugin.py --mode source
uv run --locked python scripts/package_plugin.py --mode both --from-git
```

- 默认生成可安装的 runtime 包；`--mode both` 同时生成 runtime 与 source 包。
- runtime/both 会在 `pages/dashboard/` 执行 `npm run build`，但不会自动执行 `npm ci`；依赖缺失时命令非零退出并提示。
- `--from-git` 从 Git 归档生成，避免把未跟踪文件带入包内。
- 产物写入被忽略的 `dist/` 或 `--output-dir` 指定目录，不提交进仓库。

## Dashboard 产物契约

Vite 对宿主生成 IIFE classic script，使用 `inlineDynamicImports` 把懒加载页面合入单 bundle，并移除 `type="module"`/`crossorigin`。`check_dashboard_build_artifacts.py` 要求：

- `index.html` 与 `assets/` 存在，`.vite-build/` 已清除；
- HTML 不引用 `/src/main`，不含 `type="module"` 或 `crossorigin`；
- HTML 恰好引用一个本地 JS 和一个本地 CSS，`assets/` 中恰好各有一个且引用文件存在。

该命令只验证已有构建产物，不执行构建；不要通过修改检查器掩盖错误产物。

## SHA-256 与发布说明

`generate_release_notes.py` 从 `metadata.yaml` 读取包名与版本，要求 SHA-256 清单同时包含 runtime/source 两个 ZIP 并重新计算哈希；任一产物缺失、清单格式错误或哈希不匹配都以非零退出。生成正文从 `CHANGELOG.md` 提取当前版本段落注入模板，缺少当前版本段落时拒绝生成。

## 发布门禁

打包门禁固定 Node/npm 版本，`pages/dashboard` 先 `npm ci`，再执行：

```text
npm run test
npm run build
npm run check:artifacts
npm run smoke:runtime
npm run smoke:browser
```

后端与仓库门禁：

```text
uv run --locked python -m pytest tests -q
uv run --locked python scripts/run_smoke.py -q
uv run --locked python scripts/check_all.py
uv lock --check
git diff --check
```

发布前记录 HEAD、每个 ZIP、Dashboard JS/CSS 和 manifest 的 SHA-256；解包断言资源、根 metadata、schema fallback 与兼容入口存在，runtime data、凭据和 `node_modules` 不存在。浏览器 smoke 必须人工复核截图。

## 回滚约定

发布保留上一版 runtime ZIP；契约、权限、隐私、资源、重载或打包失败时停止发布并恢复上一版已验证 ZIP，不重写 canonical SQLite/FTS/FAISS/graph 的 ID、revision 或目录。

继续阅读[质量门禁](/development/quality-gates)与[在线更新](/operations/update)。
