# 故障排除

本页面面向 Memora 管理员，按“确认状态、缩小范围、执行最小修复、验证恢复”的顺序处理常见故障。不要从删除数据、覆盖数据库或反复重装插件开始排障。

## 快速分诊

先以 AstrBot 管理员身份依次运行：

```text
/memora status
/memora health
/memora diagnostics
```

三个命令分别回答：

1. 插件和核心组件是否完成初始化；
2. Provider、召回、写入、调度和索引中哪个领域正在降级；
3. 当前领域的实时状态、计数与安全摘要是什么。

只有问题明确指向某次召回时，再运行：

```text
/memora trace <query> 5
```

::: warning 先记录现象，再执行维护操作
索引重建、恢复和更新会改变运行状态。执行前记录故障时间、命令结果和最近一次配置变更；生产实例先创建并验证备份。
:::

## 插件一直等待 Provider

### 现象

- `/memora status` 显示尚未完成初始化；
- `/memora health` 将 Provider 标记为观察或严重状态；
- 记忆写入、语义检索或反思能力不可用。

### 检查与处理

1. 在 AstrBot 中确认已经配置并启用 Embedding Provider 与 LLM Provider。
2. 确认所选 Provider 的上游服务当前可访问，而不只是配置项存在。
3. 修正 Provider 后重新加载 Memora，等待后台初始化完成。
4. 再次运行 `/memora status` 和 `/memora health`。

Provider 尚未就绪时，Memora 会在后台等待，不阻塞聊天主链路。这种等待本身不表示插件文件损坏；在确认 Provider 配置前反复重装通常不能解决问题。

## 没有生成新记忆

先区分“没有写入”和“已经写入但没有被当前请求召回”。

1. 使用 `/memora diagnostics` 检查 Provider 与写入领域。
2. 在 Dashboard Memory 页面查看近期是否存在新记录。
3. 使用 `/memora search <keyword> 5` 检查 canonical 记忆是否存在。
4. 如果写入领域降级，先检查存储可用性和 Provider 状态；不要通过重建索引尝试制造缺失的 canonical 数据。
5. 如果记录已经存在，继续按下一节检查召回与注入。

记忆抽取和反思受当前配置、会话内容与安全边界约束。单条消息没有形成长期记忆不一定是故障，应结合连续现象和诊断状态判断。

## 记忆存在但没有在对话中使用

按以下顺序缩小范围：

1. 使用 `/memora search <query> 5` 确认基础查询是否能找到目标记忆。
2. 使用 `/memora trace <query> 5` 查看召回阶段、过滤数量、评分和交付路由。
3. 在 Dashboard Recall 页面检查召回阶段，在 Injection 页面检查当前路由模式、预设和脱敏决策历史。
4. 核对目标记忆是否符合当前会话的 scope、privacy、validity 和 role 约束。
5. 仅当诊断明确指向索引不一致时执行索引重建。

过滤器有意拒绝跨用户、跨群组、过期或角色不匹配的记忆。不要为了提高命中率绕过这些边界，也不要把动态记忆改写到 System Prompt。

## 索引状态异常

SQLite canonical memory 是唯一权威数据。FTS5、FAISS、图、Relation 和 Projection 都是可重建的派生层。

当 `/memora health` 或 `/memora diagnostics` 明确报告索引异常时：

```text
/memora rebuild-index
/memora rebuild-graph
```

先重建全文与向量索引，再重建图索引。命令完成后重新运行状态、健康和原查询检查。阶段失败只应降级对应派生能力，不应删除 canonical 记录。

::: danger 不要手工删除或替换运行中数据库
不要通过删除 SQLite、复制索引目录或覆盖运行中文件来“修复”一致性。需要恢复权威数据时使用受支持的[备份与恢复](/operations/backup-recovery)流程。
:::

## Dashboard 无法打开或保存

### 无法打开

1. 确认当前用户具有 AstrBot 管理权限。
2. 从 AstrBot 插件页面打开 Memora，或运行 `/memora webui` 获取宿主提供的访问方式。
3. 使用 `/memora status` 确认插件已完成初始化。
4. 确认安装包类型；`-source.zip`、仓库源码或其他非 runtime 包会先显示 Dashboard 构建引导页。
5. 在 Memora 插件配置中临时设置 `dashboard.allow_runtime_build=true`，重新打开引导页。
6. 依次点击“安装依赖”和“构建页面”，每一步都等待页面显示成功；不需要手工输入 npm 命令。
7. 构建成功后点击“刷新页面”，再将 `dashboard.allow_runtime_build` 恢复为 `false`。
8. 仍失败时展开引导页的“输出日志”，根据安装或构建阶段的错误检查 Node.js/npm 是否存在、网络是否可访问依赖源以及构建超时设置，再查看 `/memora health` 与 `/memora diagnostics`。

runtime 包已经包含 Dashboard 生产产物，不应在普通安装流程中重复安装或构建。非 runtime 包的页面操作流程和安全边界见[快速开始](/guide/getting-started#从非-runtime-包安装)。

Page API 位于 AstrBot 宿主认证边界内，不要把 `/astrbot_plugin_memora/page/*` 直接暴露为普通用户可访问的公网 API。

### 配置无法保存

- 先阅读页面显示的字段校验错误，不要重复提交相同无效值；
- 出现 revision 冲突时，保留本地草稿，重新加载远端最新配置后再合并；
- Provider 选择项为空时，先确认 AstrBot 已经注册对应 Provider；
- 保存顶层 `debug` 开关会立即作用于当前插件进程，问题复现后应关闭。

## 备份或恢复失败

1. 在 Dashboard System 页面查看当前备份或恢复状态，确认是否已有维护操作占用。
2. 根据页面返回的稳定错误码处理无效备份、冲突、缺失 canonical 文件或不允许取消等情况。
3. 恢复界面明确要求手动重启时，按 AstrBot 的插件管理方式重启或重新加载，不要伪造恢复成功。
4. 恢复后按 canonical、全文与向量索引、图、Relation 与 Projection 的顺序确认派生层。

不要直接解压归档覆盖插件数据目录。完整边界见[备份与恢复](/operations/backup-recovery)。

## 在线更新失败

1. 先运行 `/memora update check`，确认更新功能可用且存在新版本。
2. 检查 AstrBot 使用的 HTTP、HTTPS 或 SOCKS5 代理是否可访问更新源。
3. 配置镜像失败时，Memora 会回退 GitHub；两者都失败时保留当前版本并稍后重试。
4. SHA-256 校验失败时立即停止，不要手工应用未验证的 runtime 包。
5. 宿主不支持安全重载时，使用已校验的下载结果按 AstrBot 插件管理流程完成安装。

更新前先备份，更新或重载失败时保留回滚状态。详细流程见[在线更新](/operations/update)。

## 召回变慢或后台任务降级

- `health` 报告召回降级时，检查检索、重排和 Provider 响应耗时；
- 调度领域处于观察状态时，检查后台任务安全日志并重试失败的维护任务；
- 使用 trace 判断延迟位于查询、检索、过滤、重排还是交付阶段；
- 不要仅因为响应较慢就重建索引，除非索引校验同时报告异常。

性能问题应记录可重复的时间范围、配置变化和安全计数，不要在问题报告中附带原始 query、Prompt 或记忆正文。

## 收集安全的问题报告

常规诊断不足以定位问题时，可以在 Dashboard Config 页面临时开启顶层“调试模式（问题报告）”：

1. 开启 `debug`，确认设置已经作用于当前进程；
2. 只复现一次目标问题；
3. 从 AstrBot 日志筛选 `[MemoraDebug]`，或取得插件数据目录下的 `diagnostics/memora-debug.jsonl`；
4. 复现完成后立即关闭 `debug`。

该模式只接受固定事件、阶段、状态、原因码和非负标量，不应记录对话、query、Prompt、模型回复、记忆正文、原始身份、Provider 配置或异常堆栈。诊断文件仍属于管理员信任边界，分享前应检查内容并只发送与问题时间范围相关的部分。

报告问题时同时提供：

- Memora 与 AstrBot 版本；
- 可重复的最小操作步骤和大致发生时间；
- `/memora status`、`health` 和 `diagnostics` 的安全摘要；
- 最近一次相关配置、Provider、恢复或更新操作；
- 是否能够稳定复现，以及重载后是否仍然存在。

不要附带完整配置文件、数据库、备份归档、真实对话、Provider 凭据或未经检查的原始日志。

## 仍未恢复

在执行最小修复后，重新运行 `/memora status`、`/memora health` 和原始操作。若问题仍存在，保留当前数据和回滚状态，停止重复执行有副作用的命令，再使用上节中的脱敏材料提交问题。

命令完整参数见[管理命令](/reference/commands)，诊断指标与召回评测见[诊断与评测](/operations/diagnostics-evaluation)。
