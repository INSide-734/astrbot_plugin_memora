# 记忆写入门禁配置

记忆写入门禁（`quality.gate`）在候选记忆写入 canonical 之前执行确定性检查与处置路由。页面位置：Dashboard 左侧 System 分组 → 「门禁」（`#/gate`）。保存后即时生效（热重载），无需重启。

::: tip 与质量复核的关系
门禁决定候选的三条出路：隔离（quarantine）、丢弃（discard）、标记写入（mark_write）。其中「隔离」进入管理页面的复核队列，人工批准后才重新取证并写入；「复核队列」是另一套扫描已有 canonical 记忆的独立机制，两者不互相替代。
:::

## 三个配置层

门禁配置由三个层级组成，保存时以 Dashboard 校验与 revision 冲突结果为准：

| 层级 | 内容 | 生效方式 |
|---|---|---|
| 总开关 | `enabled`、`default_profile` | 即时热重载，窗口内评估始终引用同一快照 |
| profile | 检查开关、阈值与算法参数、词表、处置策略、Judge、规则 | 属于 `profiles` 复合分支，由后端 Pydantic 兜底校验 |
| 绑定 | `bindings` 列表决定「哪类会话用哪个 profile」 | 按序首个精确匹配 |

::: warning 复合分支校验
`profiles`/`bindings`/`rules` 是对象数组，Dashboard 的配置页只显示 `enabled` 与 `default_profile` 两个标量叶；完整编辑必须在门禁页进行，复合值由后端模型校验兜底。
:::

## Profile 与绑定

- 每个 profile 独立配置：检查开关（数字冲突、否定极性、群聊主体、低质判定）、确定性/推理/Judge 阈值、词表（否定白名单、否定标记集、低质泛化词、同义替换对）、默认处置与按原因码覆盖、Judge 模板、自定义规则。
- 绑定按列表顺序匹配，**首个精确命中的绑定生效**；`chat_type`、`group_id`、`persona_id` 缺省视为不约束。没有任何绑定命中时回退 `default_profile`。
- 默认 profile 或被绑定引用的 profile 不能删除；profile 名要求 1-32 位小写字母、数字、`-` 或 `_` 且唯一。
- `group_id`/`persona_id` 仅用于 profile 解析，不写入其他业务状态，也不出现在日志或 dry-run 回显中。

## 处置语义

对每一条携带质量原因码的候选，门禁按「规则 `force_disposition` > 原因码 override > profile 默认处置」的优先级得出最终处置，多原因码取最保守结果（quarantine > discard > mark_write）：

| 处置 | 含义 | 之后的行为 |
|---|---|---|
| `quarantine` | 隔离（默认） | 不写 canonical；进入复核队列，人工批准后重读来源窗口、重新验证并重建 Atom 后写入。 |
| `discard` | 丢弃 | 不写任何存储，仅计数观测（`gate_discard_count`）。 |
| `mark_write` | 标记写入 | 写 canonical，但携带 `gate_disposition=mark_write`：默认不参与召回、注入与演化。 |
| `allow` | 放行 | 仅跳过 grounding 失败与低质判定；不绕过 guardrails、硬校验与 scope/role 检查。 |

无任何原因码的候选始终直接放行，不经过规则引擎。

## mark_write 与召回

- 默认召回会过滤 mark_write 记忆，避免低置信内容污染注入与派生证据。
- 显式包含：`/memora search <关键词> [k] true` 的末尾位置参数 `true`；记忆列表 API 使用 `include_mark_write=true`。
- mark_write 记忆不调度 Memory Evolution，但保留在 canonical 中，可随时通过 API 显式读取。

## 自定义规则与动作边界

规则是 AND/OR/NOT 条件树（最多嵌套 2 层），命中后执行动作。规则只接触候选的 `content`/`summary`/`key_facts`/`topics`/`participants`/`importance`/`chat_type` 视图，不接触消息原文、身份或 revision。

六类动作的精确边界：

| 动作 | 效果 | 边界 |
|---|---|---|
| 强制处置（`force_disposition`） | 覆盖原因码 override 与默认处置 | 可选 quarantine/discard/mark_write/allow |
| 重要性增减（`importance_delta`） | 在候选重要性上累加 delta | 累加后 clamp 到 `[0,1]` |
| 设置重要性（`set_importance`） | 直接覆盖重要性 | 覆盖时忽略 delta；同样 clamp 到 `[0,1]` |
| 追加主题（`add_topics`） | 追加主题并去重 | 每行一个，最多 5 项 |
| 覆盖隐私级别（`set_privacy`） | 覆盖候选 `privacy_level` 字段 | 只覆盖该字段；不绕过 scope/role 校验 |
| 跳过原子分类（`drop_atoms`） | 跳过 `classify_atoms` | 文档/FTS/图条目照常写，不跳过任何校验 |

::: danger 动作不是豁免
任何规则动作都只作用于门禁评估链。guardrails、硬校验、scope/privacy/role 校验在任何动作之后仍然生效。
:::

## Judge 与词表

- Judge 是请求级预算保护的可选 LLM 复核，仅在不确路径使用；开启后每次复核消耗 LLM 额度。自定义模板必须包含 `{claim_text}` 与 `{source_text}` 占位符，可追加 `{chat_type}`、`{topics}`、`{importance}`，且不超过 2000 字符，保存时后端再次校验。
- 否定标记集支持「追加」与「替换」两种模式；替换模式下内置标记（不/没/无/未/否/never/not/no）完全由列表接管。

## Dry-run 测试

门禁页的 Dry-run 面板不调用 LLM、不写任何存储，只验证 profile 解析、命中规则与最终处置：

- 显式指定 profile，或按绑定上下文（会话类型/群 ID/人格 ID）解析。
- 响应不含正文、不回显 `group_id`/`persona_id`，也不写日志或诊断。

## 相关页面

- [管理命令](/reference/commands)（`/memora search` 与 `/memora summarize`）
- [配置参考（记忆生命周期）](/reference/configuration/lifecycle)
- [质量门禁（开发）](/development/quality-gates)
