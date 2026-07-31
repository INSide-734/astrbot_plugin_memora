# Dashboard

Dashboard 是 Memora 的可视化管理入口，面向需要浏览数据、调整配置、诊断召回和执行维护操作的 AstrBot 管理员。

## 打开方式

从 AstrBot 插件页面打开 Memora，或以管理员身份发送：

```text
/memora webui
```

## 安装包与构建方式

Dashboard 是否需要手工构建取决于安装包类型：

| 安装方式 | 是否需要安装 Node 依赖 | 是否需要手工构建 |
|---|---|---|
| `astrbot_plugin_memora-<version>-runtime.zip` | 不需要 | 不需要，包内已包含生产 WebUI。 |
| `-source.zip`、仓库源码或其他非 runtime 包 | 安装机器需要 Node.js/npm | 不需要输入命令；在引导页点击“安装依赖”和“构建页面”。 |

非 runtime 包的完整步骤见[快速开始：从非 runtime 包安装](/guide/getting-started#从非-runtime-包安装)。首次构建前需要临时设置 `dashboard.allow_runtime_build=true`，让引导页按钮可以调用安装和构建接口；构建成功并刷新页面后必须恢复 `false`。关闭运行时 npm 命令入口不会影响已构建 Dashboard 的加载和使用。

::: warning 运行时构建不是 Dashboard 总开关
配置项“Dashboard 运行时构建”只授权后端 Page API 执行依赖安装和构建命令。它应只在引导页首次构建期间临时开启；WebUI 构建完成后应关闭，不要为了日常打开 Dashboard 而将它长期启用。
:::

## 功能地图

| 分组 | 页面 | 主要任务 |
|---|---|---|
| 概览 | Preview | 查看核心指标、运行摘要和快捷入口。 |
| 记忆 | Graph、Memory、Timeline、Recall、Injection | 浏览和编辑记忆，查看关系与时间线，调试召回和注入策略。 |
| 内容 | Knowledge、Notes | 维护知识库与长期笔记。 |
| 智能 | Intelligence、Learning、Jargon | 运行诊断与评测，查看学习状态，复核群聊黑话。 |
| 关系 | Profiles、Affection、Social | 管理用户画像，查看好感度、Bot 情绪和社交关系。 |
| 系统 | System、Config | 查看健康、任务、备份和更新状态，校验并写回配置。 |

## 数据表与编辑

Dashboard 数据表支持排序、筛选、分页、列视图和批量操作。实体编辑保留 revision 冲突检测、脏状态保护和显式错误反馈。

配置保存采用候选配置校验和 revision 保护。出现冲突时，页面不会丢弃本地草稿；管理员需要明确重新加载或基于最新 revision 重试。

## 召回与注入调试

- Recall 页面显示召回阶段、候选数量和允许公开的评分信息。
- Injection 页面提供策略概览、配置和脱敏决策历史。
- 页面不得展示 query、Prompt、记忆正文列表、原始身份或 Provider 凭据。

## 移动端

移动端保留导航、搜索、保存和维护操作。宽表格在受控容器内滚动，页面本身不应出现横向溢出。

继续阅读[Page API](/features/page-api)和[诊断与评测](/operations/diagnostics-evaluation)。
