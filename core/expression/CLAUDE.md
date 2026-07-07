[根目录](../../CLAUDE.md) > [core](../) > [core/](../CLAUDE.md) > **expression**

## 模块职责

`core/expression/` 是 Memora 的表达模式学习子系统。它通过零 LLM 成本、基于规则的对话对抽取引擎，从群组聊天中的用户消息 -> Bot 回复相邻消息对中自动学习表达模式，为 Bot 提供"学习用户说话风格"的能力。核心机制包括：对话对抽取、权重累积、二次衰减、容量淘汰、以及 Prompt 格式化注入。

## 入口与启动

- **模块入口**: `core/expression/__init__.py` -- 导出 `ExpressionPatternLearner`, `ExpressionPatternStore`, 及数据模型
- **核心流程**:
  ```
  消息输入
    -> buffer_message() 或 process_messages()
    -> _extract_dialog_pairs() -- 抽取相邻 [用户->Bot] 消息对
    -> store.upsert() -- 插入或权重递增
    -> _apply_decay() -- 二次衰减
    -> _evict_if_needed() -- 超出容量淘汰最低权重
    -> get_patterns_for_injection() -- 高频模式注入 LLM 上下文
  ```

**学习触发方式**:
- 手动: `await learner.process_messages(messages, group_id, persona_id)`
- 批量缓冲: `learner.buffer_message()` + `await learner.maybe_learn()` (达到阈值自动触发)

## 对外接口

| 方法 | 签名 | 职责 |
|------|------|------|
| `process_messages` | `(messages, group_id, persona_id, user_id) -> list[ExpressionPattern]` | 批量处理消息并学习模式 |
| `get_patterns_for_injection` | `(group_id, persona_id, user_id, limit) -> list[ExpressionPattern]` | 获取高权重模式用于上下文注入 |
| `format_patterns_for_prompt` | `(group_id, persona_id, user_id, limit) -> str` | 将模式格式化为可注入 LLM 的 Prompt 字符串 |
| `buffer_message` | `(group_id, sender_id, content, timestamp)` | 将消息加入缓冲区 |
| `maybe_learn` | `(group_id, persona_id, user_id, min_messages=5) -> list[ExpressionPattern]` | 缓冲区达到阈值时自动学习 |
| `get_or_create_state` | `(group_id) -> GroupState` | 获取或创建群组学习状态 |

## 关键依赖与配置

- **ExpressionPatternStore**: 独立的 aiosqlite 持久化层（不从 BaseStore 继承，使用自己的连接管理）
- **jieba**: 不依赖（纯文本匹配，不做分词）
- **配置参数** (通过构造函数):
  - `bot_id` (`str`, 默认 "bot"): Bot 的发送者 ID，用于识别 Bot 回复
  - `max_patterns_per_scope` (`int`, 默认 300): 每个三维作用域的模式上限
  - `decay_days` (`int`, 默认 15): 衰减窗口天数
  - `min_message_length` (`int`, 默认 3): 内容最小字符数（过滤过短消息）

## 数据模型

### ExpressionPattern (dataclass, slots=True)
一条已学习的表达模式：
| 字段 | 类型 | 说明 |
|------|------|------|
| situation | str | 触发情境（用户消息，截断到 50 字符） |
| expression | str | Bot 回复（截断到 100 字符） |
| group_id | str | 来源群组 ID |
| persona_id | str | Bot 人设 ID |
| user_id | str \| None | 用户级作用域（None = 群组级） |
| weight | float | 权重（重复出现时 +1.0 递增） |
| usage_count | int | 该模式被使用的次数 |
| created_at | float | 创建时间戳 |
| last_used_at | float | 最后使用时间戳 |
| decayed_at | float | 最后衰减时间戳 |
| pattern_id | int | 内部主键（插入后回填） |

### PatternScope (frozen dataclass)
三维作用域键 `(group_id, persona_id, user_id)`：
- `user_id = None` 表示群组级模式
- `to_key()` 渲染为 `"{group_id}:{persona_id}:{user_id or 'group-level'}"`

### GroupState (dataclass)
群组级学习状态：
- `message_buffer: list[dict]` -- 缓存的消息队列
- `message_count_since_last_learn: int` -- 自上次学习后的消息计数
- `last_learning_at: float` -- 上次学习时间戳

## 对话对抽取逻辑 (_extract_dialog_pairs)

纯确定性规则，零 LLM 成本：

1. 按时间顺序遍历消息列表
2. 找出相邻的 "用户消息 (非 bot) -> Bot 回复 (sender == bot_id)" 配对
3. 过滤条件:
   - 双方内容非空
   - 双方内容长度 >= `min_message_length` (默认 3)
   - 过滤系统风格消息（以 `[` 开头、以 `http` 开头、以 `@` 开头）
4. 对每条配对: `situation = content[:50]` (截断), `expression = next_content[:100]` (截断)
5. 权重初始 = 1.0

## 衰减机制 (_apply_decay)

**二次衰减公式**:
```
decay_factor = (days_elapsed / decay_days)^2
new_weight = weight * (1.0 - decay_factor)
```

- `days_elapsed > decay_days` 时 `decay_factor` 截断为 1.0（即完全清零）
- `new_weight <= DECAY_MIN (0.01)` 时触发批量删除低于该阈值的记录
- 只对 `abs(new_weight - old_weight) > 0.001` 的条目执行更新

## 容量管理

- 每个作用域上限: `MAX_PATTERNS_PER_SCOPE = 300`
- 超出上限时触发 `delete_lowest_weight(scope, excess)`，从最低权重开始淘汰
- `upsert()` 时若已存在相同 `(situation, expression, group_id, persona_id, user_id)`，则 weight += 1.0

## 存储层 (ExpressionPatternStore)

独立 aiosqlite 管理（使用 `apply_perf_pragmas` 共享 PRAGMA 配置）：

### expression_patterns 表
| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | 自增主键 |
| situation | TEXT | 触发情境 |
| expression | TEXT | Bot 回复 |
| group_id | TEXT | 群组 ID |
| persona_id | TEXT | 人设 ID |
| user_id | TEXT (nullable) | 用户 ID |
| weight | REAL | 权重 |
| usage_count | INTEGER | 使用次数 |
| created_at | REAL | 创建时间 |
| last_used_at | REAL | 最后使用时间 |
| decayed_at | REAL | 最后衰减时间 |

索引: `(group_id, persona_id, user_id)` 普通索引 + 带 `weight DESC` 的复合索引

### 核心方法
- `upsert(pattern)` -- 去重插入或权重递增
- `get_top_by_weight(scope, limit)` -- 按权重降序获取
- `get_all_for_decay(scope)` -- 获取全部模式供批衰减
- `delete_below_weight(scope, threshold)` -- 删除低于阈值的模式
- `delete_lowest_weight(scope, count)` -- 删除最少权重的 N 条
- `update_weight(pattern_id, new_weight)` -- 直接设置权重
- `mark_used(pattern_id)` -- usage_count++ + 更新 last_used_at

## Prompt 格式化 (format_patterns_for_prompt)

输出格式:
```
[学习到的表达习惯]
- 当遇到类似「{situation}」的情境时，可以回复「{expression}」
```

可直接注入到 LLM 系统提示词中，使 Bot 在回复时参考已学习的表达模式。

## 测试与质量

- **测试文件**: `tests/test_expression_*.py` -- 覆盖对话对抽取、权重累积、衰减、淘汰
- **代码质量**: slots dataclass 减少内存占用，frozen dataclass 用于不可变模型

## 常见问题 (FAQ)

**Q: 为什么不使用 LLM 来学习表达模式？**
A: 设计为纯规则引擎，零 LLM 成本。对话对抽取基于确定性规则（相邻消息对 + 发送者过滤），衰减和淘汰也是数学公式。这样避免了 LLM 调用延迟和成本，适合高频消息场景。

**Q: 作用域的三维 key 是什么意思？**
A: `(group_id, persona_id, user_id)` 三维隔离。同一个群组、同一个人设、同一个用户是一个独立的作用域，模式互不污染。user_id 为 None 表示群组级别的模式。

**Q: 模式会永久存在吗？**
A: 不会。`decay_days` (默认 15 天) 内未被重复触发的模式会经二次衰减逐渐降权，降至 `DECAY_MIN (0.01)` 以下时被批量清理。超出 300 条上限时也会淘汰低权重模式。

## 相关文件清单

| 文件 | 职责 |
|------|------|
| `__init__.py` | 模块导出 |
| `models.py` (52 行) | 数据模型：ExpressionPattern, PatternScope, GroupState |
| `pattern_learner.py` (280 行) | 核心学习引擎：对话对抽取、衰减、淘汰、Prompt 格式化 |
| `pattern_store.py` (276 行) | aiosqlite 持久化：upsert、查询、衰减更新、批量删除 |

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 深度扫描 | 完整读取 4 文件，生成 `core/expression/CLAUDE.md` |
