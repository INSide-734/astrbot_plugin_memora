[根目录](../../CLAUDE.md) > [core](../) > **extractors**

## 模块职责

`core/extractors/` 从 AstrBot 消息事件中提取标准化的文本内容，将不同类型的消息组件（文本、图片、语音、视频、文件、表情、@、转发、引用）转换为统一的纯文本表示。1 个源文件 + `__init__.py`。

## 入口与启动

- **对外导出**: `MessageContentExtractor`
- **调用方**: `RecallHandler`, `ReflectionHandler`, 及其他需要提取消息文本的模块

## 对外接口

### MessageContentExtractor

| 方法 | 职责 |
|------|------|
| `extract_message_content(event, req)` | 按组件原始顺序拼接消息内容 |
| `get_event_message_str(event)` | 获取标准化的原始消息文本 |
| `_safe_unknown_component_text()` | 从未知组件中提取白名单字段（text, content, message, name, url） |

**组件映射表**：

| AstrBot 组件 | 提取格式 |
|-------------|---------|
| `Plain` | 直接文本（strip） |
| `Image` | `[图片: <caption>]` 或 `[图片]` |
| `Record` | `[语音]` |
| `Video` | `[视频]` |
| `File` | `[文件: <name>]` |
| `Face` | `[表情:<id>]` |
| `At` | `[At:<qq>]` 或 `[At:全体成员]` |
| `Forward` | `[转发消息]` |
| `Reply` | `[引用: <message_str[:30]>]` 或 `[引用消息]` |
| 未知组件 | 安全提取白名单字段（text/content/message/name/url），限制 300 字符 |

**图片描述队列**：
- 从 `req.extra_user_content_parts` 中提取 `<image_caption>...</image_caption>` 标签
- 按提取顺序分配给 Image 组件，未匹配则显示 `[图片]`

**未知组件安全提取**：
- 白名单字段: `text`, `content`, `message`, `name`, `url`
- URL 字段格式化为 `[链接: <url>]`
- 所有值限制 300 字符
- 避免泄露完整对象内容（防止 `__repr__` 信息泄露）

## 关键依赖与配置

- **AstrBot 框架**: `astrbot.api.event.AstrMessageEvent`, `astrbot.core.message.components.{Plain, Image, Record, Video, File, Face, At, Forward, Reply}`
- **内部依赖**: `astrbot.api.logger`

## 数据模型

无独立数据模型。返回的提取结果为拼接后的字符串 `str`。

## 测试与质量

- 对应测试文件: `tests/test_extractors.py`
- 所有方法为静态方法，无状态副作用
- 未知组件字段白名单机制防止敏感信息泄露

## 常见问题 (FAQ)

**Q: 为什么消息提取后丢失了格式信息？**
A: 提取器将所有组件展平为纯文本，这是有意设计的——BM25/向量召回和 LLM 输入需要标准化的文本表示。

**Q: 如何支持新的消息组件类型？**
A: 在 `extract_message_content()` 中添加新的 `isinstance` 检查分支即可。未知组件会走 `_safe_unknown_component_text()` 回退。

## 相关文件清单

- `message_content_extractor.py` -- 消息内容提取器（139 行）
- `__init__.py` -- 公共导出

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 初始文档 | 完整读取源文件，生成模块级 CLAUDE.md |
