[根目录](../../CLAUDE.md) > [core](../) > [core/](../CLAUDE.md) > **social**

## 模块职责

`core/social/` 是 Memora 的社交关系类型化子系统。它在现有图记忆系统 (GraphMemoryManager + RelationshipTracker) 之上叠加了一层显式的关系类型标注，引入六大类 20 种关系类型定义、按关系类型划分的强度更新难度系数（难度门控），以及关系变化的集中协调逻辑。

## 入口与启动

- **模块入口**: `core/social/__init__.py` -- 导出 `RelationManager`, `RelationStore`, `SocialRelation`, `RelationChange`, 以及常量映射
- **核心流程**:
  ```
  关系更新
    -> RelationManager.get_or_create(from_user, to_user, group_id)
    -> RelationManager.apply_delta(from_user, to_user, group_id, delta, reason)
      -> 或 RelationManager.update_relation(RelationChange)
    -> _apply_delta: delta * (1 - difficulty) -> 裁剪到 [0, 1]
    -> RelationStore.upsert_relation()
  ```

## 对外接口

### RelationManager

| 方法 | 签名 | 职责 |
|------|------|------|
| `get_or_create` | `(from_user, to_user, group_id, relation_type) -> SocialRelation` | 获取或创建默认关系 |
| `apply_delta` | `(from_user, to_user, group_id, delta, reason, relation_type) -> SocialRelation` | 应用强度变化（带难度门控） |
| `update_relation` | `(change: RelationChange) -> SocialRelation` | 基于 RelationChange 对象更新 |
| `get_relations_by_group` | `(group_id) -> list[SocialRelation]` | 获取群组全部关系 |
| `get_user_network` | `(user_id) -> list[SocialRelation]` | 获取用户的全部关系网 |
| `get_user_relations_in_group` | `(user_id, group_id) -> list[SocialRelation]` | 获取群内用户关系 |
| `delete_relation` | `(from_user, to_user, relation_type, group_id) -> bool` | 删除单条关系 |
| `update_tags` | `(from_user, to_user, relation_type, group_id, tags) -> SocialRelation \| None` | 更新关系标签 |
| `list_all` | `() -> list[SocialRelation]` | 列出全部关系（调试/迁移用） |
| `list_group_ids` | `() -> list[str]` | 列出所有群组 ID |

### RelationStore (持久化层)

| 方法 | 职责 |
|------|------|
| `upsert_relation(rel)` | INSERT ON CONFLICT 更新 |
| `get_relation(from, to, type, group)` | 精确查询 |
| `get_group_relations(group_id)` | 群组关系（按强度降序） |
| `get_user_network(user_id)` | 用户全网关系 |
| `get_user_relations_in_group(user_id, group_id)` | 群内用户关系 |
| `delete_relation(from, to, type, group)` | 删除 |
| `delete_user_relations(user_id, group_id)` | 删除群内用户全部关系 |
| `count()` | 总记录数 |

## 关键依赖与配置

- **RelationStore**: 继承 `BaseStore`，管理 `social_relations` 表
- **配置**: 无外部配置，所有参数内置于数据模型中

## 数据模型

### SocialRelation (dataclass)
单向社交关系记录：
| 字段 | 类型 | 说明 |
|------|------|------|
| from_user | str | 关系发起方 |
| to_user | str | 关系目标方 |
| relation_type | str | 关系类型（如 "classmate", "best_friend"） |
| strength | float | 关系强度 [0.0, 1.0] |
| frequency | int | 累计互动次数 |
| last_interaction | float | 最后互动时间戳 |
| group_id | str | 所属群组 |
| tags | list[str] | 标签列表 |

### RelationChange (dataclass)
关系更新请求：
| 字段 | 类型 | 说明 |
|------|------|------|
| from_user | str | 发起方 |
| to_user | str | 目标方 |
| relation_type | str | 关系类型 |
| delta | float | 原始建议变化值（门控前） |
| new_strength | float | 裁剪后的新强度 |
| reason | str | 变化原因 |

### RELATION_CATEGORIES (六大类 20 种关系)

| 大类 | 关系类型 | 说明 |
|------|---------|------|
| **blood** (血缘) | parent_child, siblings, relatives | 几乎不可变 |
| **geographic** (地缘) | neighbor, fellow_town, fellow_passenger | 中度可变 |
| **career** (职业/学业) | colleague, mentor_mentee, classmate | 中度可变 |
| **emotional** (情感) | lover, best_friend, ambiguous, rival | 高度可变（除 lover/best_friend） |
| **interest** (兴趣) | board_game_friend, gaming_teammate | 高度可变 |
| **intimacy** (亲密度层级) | core_intimate, daily_normal, stranger | 混合可变 |

### RELATION_DIFFICULTY (难度门控系数)

实际强度变化量 = `delta * (1 - difficulty)`

| difficulty 范围 | 示例 | 特点 |
|----------------|------|------|
| 0.90-0.98 | parent_child (0.98), siblings (0.95), relatives (0.90) | 几乎不可变（血缘） |
| 0.80-0.90 | lover (0.85), best_friend (0.80), core_intimate (0.90) | 很慢变化（深度情感） |
| 0.50-0.70 | colleague (0.60), mentor_mentee (0.65), classmate (0.50) | 中等速度（职业） |
| 0.20-0.50 | neighbor (0.45), ambiguous (0.30), daily_normal (0.30) | 较快变化 |
| 0.05-0.20 | fellow_passenger (0.05), stranger (0.10), gaming_teammate (0.20) | 高度易变（临时关系） |

辅助函数:
- `get_relation_category(relation_type) -> str | None` -- 通过关系类型反查分类
- `get_difficulty(relation_type) -> float` -- 查询难度系数（未定义类型回退到 0.40）

## 难度门控机制 (_apply_delta)

```
actual_delta = delta * (1.0 - difficulty)
new_strength = max(0.0, min(1.0, old_strength + actual_delta))
```

- `difficulty -> 1.0`: 几乎不可变，delta=10 时实际变化仅 0.2
- `difficulty -> 0.0`: 高度易变，delta 全部生效
- 默认关系类型为 `stranger` (difficulty=0.10)

## 存储层 (RelationStore)

继承 `BaseStore`，使用 `_connect()` 上下文管理 aiosqlite 连接。表名通过白名单 `_ALLOWED_TABLES` 校验后引用（防 SQL 注入）。

### social_relations 表

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | 自增主键 |
| from_user | TEXT | 关系发起方 |
| to_user | TEXT | 关系目标方 |
| relation_type | TEXT | 关系类型 |
| strength | REAL | 关系强度 [0, 1] |
| frequency | INTEGER | 互动次数 |
| last_interaction | REAL | 最后互动时间 |
| group_id | TEXT | 群组 ID |
| tags_json | TEXT | JSON 序列化标签 |

唯一约束: `UNIQUE(from_user, to_user, relation_type, group_id)`
索引: `(from_user, group_id)`, `(to_user, group_id)`, `(group_id)`

### INSERT ON CONFLICT 语义

`upsert_relation` 使用 `INSERT ... ON CONFLICT DO UPDATE SET`，同一条关系（相同的四元组）重复写入时更新 `strength`, `frequency`, `last_interaction`, `tags_json`，实现幂等性。

## 与图记忆系统的关系

本模块是现有 `GraphMemoryManager` + `RelationshipTracker` 的类型化增强层：
- 图记忆系统负责发现和追踪用户之间的互动关系
- social 模块在此基础上标注关系类型（如从"用户 A 和 B 经常互动"推断为"classmate"或"colleague"）
- 难度门控确保不同类型关系的变化速度符合现实逻辑

## 测试与质量

- **测试文件**: `tests/test_api_social.py` -- REST API 端点测试
- **代码质量**: Type Annotations, white-listed SQL identifier, JSON tags 序列化容错（`from_row` 处理字符串/列表）

## 常见问题 (FAQ)

**Q: 为什么关系是单向的？**
A: 关系总是有方向的（from_user -> to_user）。双向关系（如互为 classmate）需要创建两条记录，分别从各自的视角维护。

**Q: 强度值为什么会受难度门控？**
A: 不同的关系类型在现实中可变的速率不同。血缘关系几乎不变，临时关系（如 fellow_passenger）极易变化。难度门控确保这种差异在系统中得到反映。

**Q: 如何添加新的关系类型？**
A: 在 `models.py` 的 `RELATION_CATEGORIES` 中添加新类型到大类，并在 `RELATION_DIFFICULTY` 中设置相应的难度系数。如果未设置难度系数，系统会使用默认值 0.40。

## 相关文件清单

| 文件 | 职责 |
|------|------|
| `__init__.py` (26 行) | 模块导出 |
| `models.py` (164 行) | 数据模型：6 大分类, 20 种关系, 难度系数, SocialRelation, RelationChange |
| `relation_manager.py` (202 行) | 核心管理器：关系 CRUD, 难度门控, 标签管理 |
| `relation_store.py` (275 行) | SQLite 持久化：BaseStore 子类, white-listed SQL, ON CONFLICT upsert |

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 深度扫描 | 完整读取 4 文件，生成 `core/social/CLAUDE.md` |
