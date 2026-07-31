# 诊断与评测

诊断回答“系统当前哪里降级”，评测回答“召回结果是否足够好”。两者都必须遵守隐私观测 allowlist。

## 运行时诊断

管理员可以使用：

```text
/memora health
/memora diagnostics
```

`health` 提供健康评分、异常领域和固定排障建议；`diagnostics` 提供 Provider、召回、任务、索引和写入的实时安全摘要。

## 召回追踪

```text
/memora trace 喜欢的音乐 5
```

trace 用于查看当前会话的召回阶段、候选数量和评分路径。聊天输出不回显记忆正文，观测数据也不应记录 query、Prompt、记忆 ID 列表或原始身份。

## 离线评测

Memora 支持：

- Recall@K
- MRR
- nDCG@K
- p95 延迟
- variant 与消融对比
- 安全反馈排序 shadow

评测数据可以从当前记忆生成自召回样本，也可以导入人工标注 JSONL。人工数据集仍应使用匿名标识并避免真实用户内容。

## 安全观测范围

- 注入决策只持久化 allowlist 标量。
- 模型可见 Projection 只包含 `type`、`summary` 和 `confidence`。
- Provider 密钥、请求头、API 地址、数据库路径和原始堆栈不得进入日志、指标或 trace。

## 排障顺序

1. 运行 `/memora status` 确认初始化状态。
2. 使用 `/memora health` 定位降级领域。
3. 使用 `/memora diagnostics` 查看对应安全摘要。
4. 仅在检索问题明确时使用 trace 或离线评测。
5. 索引损坏时按[备份与恢复](/operations/backup-recovery)中的权威边界重建。

按具体故障现象执行检查和最小修复时，使用[故障排除](/operations/troubleshooting)中的分诊流程。
