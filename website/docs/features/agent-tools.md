# Agent 工具

核心组件就绪后，Memora 可以按 `agent_tools` 配置向 AstrBot Agent 注册最多 15 个工具。工具是否出现取决于配置、组件能力和伴侣插件委托状态。

## 工具清单

| 能力 | 工具 | 默认与边界 |
|---|---|---|
| 记忆 | `recall_long_term_memory`、`memorize_long_term_memory` | 召回默认开启；主动写入默认关闭，只在用户明确要求长期记忆时调用。 |
| 笔记 | `note_search`、`note_read`、`note_write` | 读取默认开启；写入默认关闭。 |
| 知识库 | `knowledge_search`、`knowledge_read` | 知识库组件可用时注册。 |
| 用户画像 | `profile_lookup` | self lookup 使用可信发送者身份；查询其他用户需要授权。 |
| 好感度与情绪 | `check_affection`、`check_bot_mood` | 组件可用且未委托给伴侣插件时注册。 |
| 群聊黑话 | `explain_jargon`、`list_group_jargon` | 按当前群组作用域查询。 |
| 表达模式 | `recall_expressions` | 返回当前场景适用的已学习表达。 |
| 社交关系 | `lookup_relations`、`list_group_relations` | 查询用户关系和群组关系图。 |

## 安全边界

工具结果会进入模型上下文，因此只返回完成任务所需的稳定字段。结果不得包含 Provider 配置、凭据、数据库路径、原始身份、内部 revision 证据或异常堆栈。

主动写入工具不能根据模型猜测自行开启。管理员应只在明确需要时启用，模型也只应在用户明确要求长期保存内容时调用。

## 配置生效

工具开关在插件初始化时读取。运行中修改后需要重新加载插件，不能只根据 Dashboard 已保存就假设 Agent 工具列表已经更新。

配置域说明见[配置参考](/reference/configuration)。
