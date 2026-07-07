[根目录](../../CLAUDE.md) > [core](../) > [core/](../CLAUDE.md) > **affection**

## 模块职责

`core/affection/` 是 Memora 的好感度子系统，负责追踪和评估用户与 Bot 之间的情感亲密度。它通过 LLM 辅助的交互分类、情绪门控和好感度分数动态调整，为 Bot 赋予类似于"感情"的行为反应能力。该子系统覆盖从消息分析、情感评分、Bot 情绪状态模拟到好感度持久化的全生命周期。

## 入口与启动

- **模块入口**: `core/affection/__init__.py` -- 导出 `AffectionManager`, `AffectionStore`, 以及模型/枚举类型
- **初始化**: `AffectionManager(store, llm_adapter)` -- 传入 SQLite 存储实例与 LLM 适配器即可使用
- **核心流程**:
  ```
  ProcessInteraction (process_interaction)
    -> 确保群组当前情绪 (_ensure_mood)
    -> LLM + 关键词回退分类 (_classify)
    -> 情绪门控检查 (_check_mood_gate) -- 不符合条件的交互不计分
    -> 计算变化量 (_calculate_delta) -- 基础值 x 情绪修正
    -> 持久化写入 (store.upsert_affection)
    -> 总量超限重分配 (_maybe_redistribute)
    -> 情绪级联更新 (_apply_mood_cascade)
  ```

## 对外接口

| 方法 | 签名 | 职责 |
|------|------|------|
| `process_interaction` | `(user_id, group_id, message, bot_response) -> dict` | 主入口：分类交互、门控、更新分数、级联情绪 |
| `get_mood` | `(group_id) -> BotMood` | 返回群组当前 Bot 情绪 |
| `set_mood` | `(group_id, mood_type, intensity, duration_hours) -> BotMood` | 手动设置 Bot 情绪（同时持久化） |
| `get_group_affection_status` | `(group_id) -> dict` | 返回群组好感度概览（总分、用户数、Top5、当前情绪） |
| `get_user_affection` | `(group_id, user_id) -> UserAffection` | 返回单个用户的好感度记录 |
| `close` | `() -> None` | 关闭 store 连接，清理资源 |

## 关键依赖与配置

- **LLM 适配器**: 实现 `LLMAdapter` Protocol 接口（`async def chat_completion(prompt, temperature) -> str`），用于交互类型分类。若未提供，回退到关键词规则匹配
- **AffectionStore**: 继承 `BaseStore`，管理 `user_affection` 和 `bot_mood` 两张 SQLite 表
- **配置参数** (通过构造函数):
  - `max_affection` (`int`, 默认 100): 单用户好感度上限
  - `min_affection` (`int`, 默认 -100): 单用户好感度下限
  - `max_total_affection` (`int`, 默认 5000): 群组总好感度软上限
  - `affection_decay_rate` (`float`, 默认 0.5): 超限重分配衰减率

## 数据模型

### AffectionLevel (IntEnum)
离散化的好感度八级，从 `HOSTILE (-75)` 到 `INTIMATE (100)`：
```
HOSTILE(-75) < DISLIKED(-50) < COLD(-25) < NEUTRAL(0) < WARM(25) < FRIENDLY(50) < CLOSE(75) < INTIMATE(100)
```
- `from_score(score)` -- 根据分数返回等级
- `name_for(score)` -- 返回中文名（敌对/不喜/冷淡/中立/温暖/友好/亲密/挚友）

### BotMood (dataclass, slots=True)
Bot 情绪快照：
- `mood_type` (MoodType): 10 种情绪（happy/sad/excited/calm/angry/anxious/playful/serious/nostalgic/curious）
- `intensity` (float, 0.1-1.0): 情绪强度
- `duration_hours` (float, 默认 4.0): 情绪持续时间
- `get_mood_modifier()`: 返回对好感度变化的乘数修正（结合情绪类型 + 强度）

### InteractionType (str, Enum)
17 种交互类型（13 正向/中性 + 4 负向）：
- 正向: CHAT, COMPLIMENT, PRAISE, ENCOURAGE, SUPPORT, FLIRT, COMFORT, HELP, THANKS, APOLOGY, TEASE, CARE, GIFT
- 负向: INSULT, HARASSMENT, ABUSE, THREAT

### INTERACTION_RULES
每种交互类型对应一条 `_InteractionRule`：
- `base_change: int` -- 基础好感度变化值（范围 -12 到 +8）
- `mood_sensitive: bool` -- 是否受 Bot 当前情绪修正
- `mood_effect: float` -- 对 Bot 情绪的影响程度（负向 -0.7 到正向 +0.4）
- `mood_requirements: list[MoodType] | None` -- 情绪门控条件（不符合时不计分）
- `positive_mood_boost` / `negative_mood_trigger` -- 是否触发情绪级联

### UserAffection (dataclass, slots=True)
单用户好感度记录：
- `affection_score: int` -- 当前分数
- `interaction_count: int` -- 累计交互次数
- `level: AffectionLevel` -- 当前等级（计算属性）
- `level_name: str` -- 中文等级名（计算属性）

## 情绪级联机制

### 负向级联 (_cascade_negative)
INSULT/ABUSE/THREAT/HARASSMENT 触发即时情绪覆盖：
- THREAT -> ANXIOUS, ABUSE -> ANGRY, INSULT -> SAD, HARASSMENT -> ANXIOUS
- 持续 2 小时，强度 = min(0.9, abs(effect))

### 正向级联 (_cascade_positive)
GIFT/PRAISE/ENCOURAGE 触发：
- GIFT -> EXCITED, PRAISE/ENCOURAGE -> HAPPY
- 持续 4 小时，强度 = min(0.8, effect)

### 微调级联 (_cascade_adjust)
对当前情绪做小幅强度调整（需 effect >= 0.05，强度变化 >= 0.1）

## 好感度总量管理

群组总好感度超出 `max_total_affection` 时，触发 **按比例重分配**：
1. 计算超额量
2. 按好感度降序排列"其他用户"
3. 按 `decay_rate` 比例逐轮削减高分用户，直到超额消除（最多 3 轮）

## 关键词回退分类

当 LLM 不可用时，使用 `classify_by_keywords()` 基于规则匹配。关键词从最长到最短排序，优先匹配更长、更具体的模式。覆盖 compliment, thanks, threat, insult, care 五种类型。

## 存储层

### user_affection 表
| 列 | 类型 | 说明 |
|----|------|------|
| user_id | TEXT | 用户 ID (PK) |
| group_id | TEXT | 群组 ID (PK) |
| affection_score | INTEGER | 好感度分数 |
| interaction_count | INTEGER | 交互次数 |
| last_interaction | REAL | 最后交互时间戳 |

### bot_mood 表
| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | 自增主键 |
| group_id | TEXT | 群组 ID |
| mood_type | TEXT | 情绪类型 |
| intensity | REAL | 情绪强度 |
| description | TEXT | 中文描述 |
| start_time | REAL | 起始时间戳 |
| duration_hours | REAL | 持续时长 |

### 核心存储方法
- `upsert_affection` -- 原子化 INSERT OR REPLACE，带上下限裁剪
- `get_top_users` -- 按分数降序 Top-K
- `get_total_affection` -- 群组总分
- `get_active_mood` -- 获取未过期情绪
- `save_bot_mood` -- 保存新情绪记录

## 测试与质量

- **测试文件**: `tests/test_affection_manager.py` -- 覆盖交互分类、分数更新、情绪门控、级联逻辑
- **关键词映射测试**: 可通过 `classify_by_keywords()` 单独验证
- **代码质量**: Type Annotations, frozen dataclass 用于不可变模型, Protocol 定义 LLM 接口

## 常见问题 (FAQ)

**Q: LLM 分类失败了会发生什么？**
A: 回退到 `classify_by_keywords()` 关键词规则匹配。如果关键词也匹配不到，默认归类为 `InteractionType.CHAT`（好感度 +1）。

**Q: 情绪门控的"门控"是什么意思？**
A: 某些交互类型（如 FLIRT 需要 Bot 处于 HAPPY/PLAYFUL/EXCITED 情绪）有情绪前置条件。如果当前情绪不满足，该交互将不计入好感度变化，但消息仍会被分类。

**Q: 好感度分数是否无上限累加？**
A: 单用户分数受 `max_affection` / `min_affection` 约束（默认 ±100）。群组总分数超出 `max_total_affection`（默认 5000）时触发自动重分配，削减高分用户的分数。

## 相关文件清单

| 文件 | 职责 |
|------|------|
| `__init__.py` | 模块导出 |
| `models.py` (269 行) | 数据模型、枚举、交互规则、关键词映射 |
| `affection_manager.py` (576 行) | 核心管理器：交互分类、门控、分数更新、情绪级联 |
| `affection_store.py` (240 行) | SQLite 持久化：好感度 CRUD、情绪记录 |

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 深度扫描 | 完整读取 4 文件，生成 `core/affection/CLAUDE.md` |
