[根目录](../../CLAUDE.md) > [core](../) > **dedup**

## 模块职责

`core/dedup/` 提供消息级去重缓存，基于消息 ID 或内容指纹防止同一条消息被重复处理（如重复触发记忆抽取）。1 个源文件 + `__init__.py`。

## 入口与启动

- **对外导出**: `DedupManager`
- **调用方**: `core/event_handler.py` 在消息处理前检查去重

## 对外接口

### DedupManager

| 方法 | 职责 |
|------|------|
| `build_dedup_key(event, session_id, content)` | 构建去重键：优先 message_id，缺失时退化为内容指纹 |
| `is_duplicate(dedup_key)` | 检查消息是否已处理（惰性过期 + TTL 检查） |
| `mark_processed(dedup_key)` | 标记消息已处理（超限时淘汰最早条目） |

**去重键构建策略**：
1. **主策略（message_id）**: `id:{platform_scope}:{session_id}:{message_id}`
   - 优先使用 `event.message_obj.message_id`
   - `platform_scope` 从 `get_platform_name()` 或 `message_obj.platform` 等属性获取
2. **回退策略（内容指纹）**: `fallback:{sha1(session_id|sender_id|timestamp|content)}`
   - 当 `message_id` 不可用时使用

**缓存特性**：
- 最大容量: 1000 条（可配置）
- TTL: 300 秒（可配置）
- 惰性过期: 仅在 `is_duplicate()` 检查时清理
- 溢出淘汰: 超过 max_size 时淘汰最早插入的条目（基于时间戳）

## 关键依赖与配置

- **无外部依赖**: 纯 Python 实现（`hashlib`, `time`）
- **AstrBot API**: `event.message_obj`, `event.get_sender_id()`, `get_platform_name()` / `get_platform()`

## 数据模型

无独立数据模型。内部使用 `dict[str, float]`（key → 插入时间戳）。

## 测试与质量

- 对应测试文件: `tests/test_dedup.py`
- `is_duplicate` 在过期检查失败时删除过期条目（防御性清理）
- `mark_processed` 在超过 max_size 时自动淘汰最早条目

## 常见问题 (FAQ)

**Q: 什么情况下会使用内容指纹回退？**
A: 当 `event.message_obj.message_id` 为空或不存在时。某些 AstrBot 适配器可能不提供 message_id。

**Q: 去重缓存会占用多少内存？**
A: 每个条目约 100 字节（key + float timestamp），最大 1000 条约 100KB，非常轻量。

## 相关文件清单

- `dedup_manager.py` -- 消息去重管理器（83 行）
- `__init__.py` -- 公共导出

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取源文件，生成模块级 CLAUDE.md |
