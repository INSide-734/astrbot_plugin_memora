# 管理命令

以下 `/memora` 命令要求 AstrBot 管理员权限。命令行为以当前注册代码和可执行测试为准。

| 命令 | 说明 |
|---|---|
| `/memora status` | 查看插件初始化与核心组件状态。 |
| `/memora health` | 查看运行时健康评分、异常领域与排障建议。 |
| `/memora diagnostics` | 查看 Provider、召回、任务、索引和写入的诊断快照。 |
| `/memora search <query> [k] [true/false]` | 搜索记忆；`k` 默认是 `5`；末尾位置参数 `true` 显式包含低置信 mark_write 记忆（默认排除）。 |
| `/memora trace <query> [k]` | 追踪当前会话的召回阶段与评分，不回显记忆正文。 |
| `/memora forget <doc_id>` | 删除指定 canonical 记忆。 |
| `/memora rebuild-index` | 重建向量与 BM25/FTS 索引。 |
| `/memora rebuild-graph` | 重建图记忆索引。 |
| `/memora webui` | 输出 Dashboard 访问信息。 |
| `/memora summarize [confirm-abandon]` | 默认立即入队当前会话的固定总结窗口；`confirm-abandon` 仅在管理员确认数据丢失时跳过无 canonical 证据的阻塞窗口。 |
| `/memora reset` | 重置当前会话的长期记忆上下文。 |
| `/memora cleanup [preview 或 exec]` | 清理历史消息中的记忆注入片段，默认预演。 |
| `/memora update [check、download 或 apply]` | 检查、下载或安装经校验的 runtime 包，默认检查。 |
| `/memora help` | 查看命令帮助。 |

## 示例

```text
/memora health
/memora search 喜欢的音乐 5
/memora search 低置信线索 5 true
/memora summarize
/memora summarize confirm-abandon
/memora cleanup preview
/memora update check
```

## 有副作用的命令

- `forget` 删除 canonical 记忆，执行前确认目标 ID。
- `summarize` 默认只返回 `queued/duplicates/active/target` 即时确认；最终写入、隔离、丢弃、mark_write 和失败累计通过 diagnostics 观察。`confirm-abandon` 是不可逆的数据丢失确认，只处理当前 session epoch 中无 canonical 证据的 blocked/unknown 窗口。
- `cleanup exec` 修改历史消息；先运行 `cleanup preview` 查看范围。
- `update apply` 切换插件 runtime；生产实例应先备份。
- 索引重建只重建派生数据，不应删除 canonical SQLite 记录。

更多维护背景见[故障排除](/operations/troubleshooting)、[备份与恢复](/operations/backup-recovery)和[在线更新](/operations/update)。
