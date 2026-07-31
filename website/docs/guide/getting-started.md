# 快速开始

本页面面向首次安装 Memora 的 AstrBot 管理员，目标是完成安装、最小配置和首次状态验证。

## 环境要求

- Python `>=3.12,<3.13`
- AstrBot `>=4.24.2`
- 可用的 Embedding Provider，用于向量化与语义检索
- 可用的 LLM Provider，用于记忆抽取、反思等智能处理

普通插件安装不需要 Node.js。Node.js 只用于 Dashboard 和文档站开发，具体版本要求见[开发环境](/development/setup)。

## 安装插件

### 从 AstrBot 插件市场安装

1. 打开 AstrBot 管理面板的“插件市场”。
2. 搜索 `Memora`。
3. 打开插件详情并点击“安装”。

### 从 Release 安装包安装

1. 打开 [Memora Releases](https://github.com/INSide-734/astrbot_plugin_memora/releases/latest)。
2. 下载最新的 `astrbot_plugin_memora-<version>-runtime.zip`。
3. 在 AstrBot 插件管理页面选择从安装包安装并上传 ZIP，无需提前解压。

::: warning 使用 runtime 包
普通安装应选择文件名以 `-runtime.zip` 结尾的包。`-source.zip` 面向源码审阅和开发，不是精简运行时包。
:::

## 从非 runtime 包安装

如果安装的是 `-source.zip`、仓库源码压缩包或其他不包含 Dashboard 生产产物的非 runtime 包，插件后端可以存在，但 WebUI 不能直接使用。此时需要在安装机器上自行安装 Dashboard 依赖并完成生产构建。

安装机器需要具备满足 Dashboard 要求的 Node.js 和 npm，但用户不需要打开终端或手工输入 npm 命令。按以下顺序操作：

1. 在 AstrBot 的 Memora 插件配置中找到“Dashboard 运行时构建”。
2. 临时开启“允许运行时安装/构建 Dashboard”，即设置 `dashboard.allow_runtime_build=true`，保存配置。
3. 从 AstrBot 插件页面打开 Memora。未构建的静态引导页会显示“安装依赖”和“构建页面”两个按钮。
4. 先点击“安装依赖”，等待页面明确显示依赖安装成功。
5. 再点击“构建页面”，等待页面明确显示页面构建成功。
6. 点击引导页出现的“刷新页面”，进入完整 Dashboard。
7. 构建完成后回到 Memora 插件配置，关闭“允许运行时安装/构建 Dashboard”，恢复 `dashboard.allow_runtime_build=false`。

“安装依赖”和“构建页面”按钮通过 AstrBot 插件页面桥接调用受保护的 Page API。后端会在正确的 `pages/dashboard/` 目录依次执行锁定依赖安装和生产构建，并将生成的 `index.html` 与哈希资源同步到 Dashboard 页面目录。

构建成功后的最终配置应为：

```text
dashboard.allow_runtime_build = false
```

该选项只控制插件运行期间是否允许 Page API 安装依赖和构建 Dashboard，关闭它不会关闭已经构建好的页面。构建期间临时开启，完成后立即关闭，可以避免生产运行时继续暴露 npm 命令执行能力。

::: danger 不要混淆两种构建方式
非 runtime 包应通过引导页的两个按钮完成首次构建，不需要用户手工输入命令。“Dashboard 运行时构建”默认关闭；首次构建前临时开启，构建成功并刷新页面后必须再次关闭。
:::

## 配置 Provider

安装后在 AstrBot 中选择 Embedding Provider 和 LLM Provider，然后重启 AstrBot：

- Embedding Provider 负责生成记忆向量和支持语义检索。
- LLM Provider 负责记忆抽取、反思及启用后的智能增强。

Provider 尚未就绪时，Memora 会在后台等待并重试，不阻塞 AstrBot 的聊天主链路。此时插件尚未完成运行时初始化，但不代表安装文件损坏。

## 验证状态

以 AstrBot 管理员身份发送：

```text
/memora status
```

成功结果应显示插件已初始化，并且核心存储、检索和 Provider 组件可用。如果仍在等待 Provider，请先检查 AstrBot 的模型配置，再重新加载插件。

## 打开 Dashboard

从 AstrBot 插件页面进入 Memora，或发送：

```text
/memora webui
```

Dashboard 可以查看运行摘要、记忆、召回、配置、备份、更新和诊断状态。

## 下一步

1. 阅读[配置入门](/guide/configuration)，确认注入路由和常用配置域。
2. 了解[记忆生命周期](/concepts/memory-lifecycle)和[稳定身份](/concepts/identity)。
3. 保存重要数据前阅读[备份与恢复](/operations/backup-recovery)。

完整字段由仓库中的 [`_conf_schema.json`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/_conf_schema.json) 定义。
