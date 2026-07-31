# 管理命令

以下 `/memora` 命令要求 AstrBot 管理员权限。命令行为以当前注册代码和可执行测试为准。

| 命令 | 说明 |
|---|---|
| `/memora status` | 查看插件初始化与核心组件状态。 |
| `/memora health` | 查看运行时健康评分、异常领域与排障建议。 |
| `/memora diagnostics` | 查看 Provider、召回、任务、索引和写入的诊断快照。 |
| `/memora search <query> [k]` | 搜索记忆；`k` 默认是 `5`。 |
| `/memora trace <query> [k]` | 追踪当前会话的召回阶段与评分，不回显记忆正文。 |
| `/memora forget <doc_id>` | 删除指定 canonical 记忆。 |
| `/memora rebuild-index` | 重建向量与 BM25/FTS 索引。 |
| `/memora rebuild-graph` | 重建图记忆索引。 |
| `/memora webui` | 输出 Dashboard 访问信息。 |
| `/memora summarize` | 立即触发当前会话的记忆总结。 |
| `/memora reset` | 重置当前会话的长期记忆上下文。 |
| `/memora cleanup [preview 或 exec]` | 清理历史消息中的记忆注入片段，默认预演。 |
| `/memora update [check、download 或 apply]` | 检查、下载或安装经校验的 runtime 包，默认检查。 |
| `/memora help` | 查看命令帮助。 |

## 示例

```text
/memora health
/memora search 喜欢的音乐 5
/memora trace 喜欢的音乐 5
/memora cleanup preview
/memora update check
```

## 有副作用的命令

- `forget` 删除 canonical 记忆，执行前确认目标 ID。
- `cleanup exec` 修改历史消息；先运行 `cleanup preview` 查看范围。
- `update apply` 切换插件 runtime；生产实例应先备份。
- 索引重建只重建派生数据，不应删除 canonical SQLite 记录。

更多维护背景见[故障排除](/operations/troubleshooting)、[备份与恢复](/operations/backup-recovery)和[在线更新](/operations/update)。
