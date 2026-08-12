[`根级 AGENTS.md`](../../AGENTS.md) / [`core`](../AGENTS.md) / **identity**

# 协议稳定身份模块

**最后更新：** 2026-08-13
**源码范围：** `core/features/identity/`（模型、Store、解析器、服务、运行时、会话同步与记忆增强）；`core/identity/` 仅保留单实现 re-export 兼容边界
**上游装配：** `core/platform/composition/component_factory.py`、`core/event_handler.py`

## 职责与边界

`core/features/identity/` 拥有协议身份模型、服务、Store、解析器，以及会话同步、记忆增强和运行时编排；`core/identity/` 仅保留三个运行时协作层的单实现 re-export，供尚未切换到 feature 路径的历史调用方与契约测试使用。两者共同把平台事件解析为稳定、不可变的用户身份，并尽力维护当前显示名称、作用域别名、历史会话名称和召回时的只读身份说明。它们不负责修改 canonical memory、检索排序、平台事件模型或管理员画像。

当前内置 OneBot 11 与 QQ 官方机器人适配器；其他协议通过 `IdentityProtocolAdapter` 接口与固定注册表扩展，不在事件处理器或记忆处理器中增加协议分支。

## 模块导航

| 文件 | 职责 |
|---|---|
| `../features/identity/domain/models.py` | `ResolvedIdentity`、信任状态、名称字段状态与解析器协议 |
| `../features/identity/infrastructure/protocols/onebot11.py` | OneBot 11 严格识别、QQ/group 规范化、名称清理和匿名 opaque sender |
| `../features/identity/infrastructure/protocols/qq_official.py` | QQ 官方 WebSocket/Webhook 场景识别、OpenID/实例隔离、冲突校验和 RFC3339 时间 |
| `../features/identity/infrastructure/protocols/registry.py` | 固定内置 manifest 与调用方解析器追加入口 |
| `../features/identity/infrastructure/protocols/resolver.py` | 唯一选择、冲突隔离与 unsupported 降级 |
| `../features/identity/application/service.py` | 当前昵称/群名片合并和旧名称别名计划 |
| `../features/identity/infrastructure/store.py` | 三张身份目录表及查询实现 |
| `../features/identity/application/conversation_sync.py` | 按可信作用域同步 user 消息显示名称并失效受影响缓存 |
| `../features/identity/application/runtime.py` | 解析、尽力持久化、同步、只读 Enricher 与 Store 关闭边界 |
| `../features/identity/application/enricher.py` | 稳定记忆参与者 metadata、固定 Prompt 约束和历史别名只读增强 |

持久化实现只维护 `identity_users`、`identity_scope_members`、`identity_aliases` 三张独立表；已迁移的旧模型、服务、Store 与协议解析器路径不再提供兼容导出，所有调用方必须使用 feature owner。`core/identity/{runtime.py,memory.py,conversation_sync.py}` 只保留单实现 re-export，禁止复制实现，P5 阶段统一清理。

## 核心契约

### 解析与主键

- OneBot 11 仅接受 `aiocqhttp` 的私聊/群聊 message 事件；QQ 与群号必须是正 int64 的 ASCII 十进制值，并规范化为无前导零字符串。
- 可信 OneBot 用户的 `stable_user_id` 与 `canonical_user_id` 都是 QQ 号，模型标签为 `QQ:<id>`。昵称、群名片和 AstrBot 包装层显示名不得充当主键。
- QQ 官方同时接受 `qq_official` 与 `qq_official_webhook`：C2C 使用 `author.user_openid`，QQ 群使用 `author.member_openid`，频道/频道私信使用 `author.id`。OpenID 保持 opaque；namespace/canonical 加入平台实例 ID 的 96-bit SHA-256 摘要，模型标签为 `QQ官方:<实例摘要>:<OpenID>`，不能与 OneBot QQ 或其他机器人实例合并。
- `union_openid` 可缺失且需特殊配置，本模块固定不把它用于 stable/canonical。未来跨应用归并必须使用显式、可审计的 identity link 设计，不得在无迁移路径中切换既有主键。
- sender/wrapper 证据冲突、非法字段和匿名事件不得写用户目录。匿名 sender 只在群内 opaque，不可跨群关联；未注册协议保留 AstrBot 原有 sender 行为。
- 名称执行 NFKC、控制字符清理、首尾空白清理和 128 码点上限。群名片优先于昵称，管理员画像备注只读优先于两者。

### 名称目录与会话同步

- 新观察按时间更新当前名称；被替换的有效名称成为作用域别名。旧事件只能增加别名，不能回滚当前名称；同时间后到观察获胜。
- 群名片与历史群显示名只在原群作用域同步；OneBot 私聊昵称只跨 OneBot 私聊同步；QQ 官方 C2C/频道私信只同步当前完整 UMO。assistant 消息、其他协议、其他实例和其他群不得被改写。
- 同步普通失败不阻断聊天；缓存失效逐 session 尽力执行。`asyncio.CancelledError` 始终传播。
- Store 初始化仅执行 `CREATE TABLE/INDEX IF NOT EXISTS`，不得 `ALTER TABLE`、扫描业务表、迁移、回填或重写历史记录。

### 记忆与只读召回

- 只有 trusted user 消息且 sender/canonical/label/source 一致时，才能生成 `stable-identity-v1` 参与者 metadata。新记录携带内部 `participant_identity_sources` 映射；同一 canonical 来源冲突时整名参与者被拒绝。参与者按首次出现排序，批次内名称快照取最新值，最多 32 人。
- `MemoryProcessor` 的固定身份约束不可被自定义模板取消；它要求“当前名称（稳定标签）”，禁止猜测、交换 ID 或把改名视为新用户。最终参与者 metadata 由系统覆盖模型输出。
- Enricher 只能在 `_safe_candidates()` 之后处理候选副本；必须保持 content、score、canonical ID、revision、顺序和全部索引不变，也不得写回 canonical memory。
- 证据顺序固定为通用可信稳定来源、旧 OneBot 稳定来源、原群精确别名、同群成员唯一全局别名、同一私聊当前用户。通用来源的 namespace 必须与当前可信事件一致；匹配执行 NFKC 后精确相等，同名多候选或跨 session legacy memory 必须拒绝。
- 临时 `identity_reference_lines` 最多 8 条，只能包含当前名称、必要的单个历史名称和适配器稳定标签。formatter 将其计入 metadata 与总注入预算；`ContentLevel.NONE` 不输出任何内容。
- 五种 DeliveryMode 复用 `InjectionExecutor` 的单一受保护 payload。身份说明不得进入 System Prompt、普通日志、指标、Recall Trace、注入决策记录或异常文本。

## 初始化、关闭与依赖

`ComponentFactory` 在 `memora.db` 可用后从 `core.features.identity` 构造 Store、Resolver 和 Service，再组合 Synchronizer、Enricher 与 Runtime，并把 Runtime 挂到 `ConversationManager`。`EventHandler` 复用该实例；不得为请求重新创建 Store 或注册表。

身份 Store 初始化普通失败时关闭部分连接并返回 resolver-only Runtime；取消继续传播。关闭由 Runtime 幂等释放 Store，不启动独立 worker，也不在关闭期同步名称。

依赖方向为 `event_handler/composition` → `core/features/identity`（运行时、会话同步与记忆增强已迁入 feature 的 application 层）。feature owner 不得反向导入旧适配层；`core/identity/` 的 re-export 只能指向 feature，两者都不得导入 Page API、命令、handler 或 `main.py`，enricher 对 Store 只使用类型边界与构造注入。

## 验证入口

```powershell
python -m pytest tests/test_protocol_identity_resolver.py tests/test_protocol_identity_store.py tests/test_protocol_identity_service.py -q
python -m pytest tests/test_conversation_identity_sync.py tests/test_identity_runtime_wiring.py tests/test_memory_identity.py -q
python -m pytest tests/test_memory_identity_enricher.py tests/test_identity_delivery_modes.py tests/test_memory_formatter.py -q
python -m pytest tests/test_event_handler.py tests/test_handlers.py tests/test_plugin_init.py tests/integration/test_pipeline_identity.py -q
```

验证必须额外确认 `git diff --check`、本轮修改文件长度、无 `ALTER TABLE`/历史扫描，以及身份 canary 未进入日志、指标或 trace。
