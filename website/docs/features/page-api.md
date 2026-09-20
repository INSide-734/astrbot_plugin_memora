# Page API

Page API 是 AstrBot 插件页面与 Memora 后端之间的管理边界，主要服务内置 Dashboard。它不是面向普通聊天用户的公开匿名 API。

## 路由前缀

页面路由位于：

```text
/astrbot_plugin_memora/page/*
```

具体处理器由 `PluginPageApi` 的多个聚焦 mixin 组合，源代码位于 [`core/platform/transport/page_api/`](https://github.com/INSide-734/astrbot_plugin_memora/tree/main/core/platform/transport/page_api)。

## 能力范围

- memory、atom、knowledge、notes、profile、jargon 和 relationships 的查询与维护；
- backup、learning、maintenance、更新和恢复等运行维护；
- injection、诊断、评测、配置和数据预览；
- realtime SSE 事件流。

## 响应与错误

调用方必须保留标准响应 envelope 和显式错误信息。请求字段按固定模型校验，未知字段在要求严格的入口会被拒绝。

内部异常可以记录安全摘要，但原始异常、敏感请求内容和第三方响应不得直接返回浏览器。

## Revision 与分页

- 配置类写回（`/config/apply` 与 topic segmentation 配置更新）发送 `base_revision` 与最小 `changes`，服务端比较 revision 后应用，冲突返回 `config_conflict` 与当前 revision；topic segmentation 的 `base_revision` 缺省时取当前 revision。
- 实体写回（affection、profile、jargon、social 等）发送 `expected_revision`，冲突返回 `edit_conflict`，并附当前实体与 revision 供草稿恢复。
- 隔离与派生复核使用各自的 `expected_revision` 协议：quarantine 缺失或非法 revision 返回 `quarantine_revision_required`、过期返回 `quarantine_revision_conflict`；memory evolution review 过期返回 `derived_review_conflict`；learning 发布/回滚过期返回 `config_revision_conflict`。这三类冲突附带专有错误码而不返回当前实体，客户端必须重新拉取详情或列表后再重试。
- memory、knowledge 与 notes 的更新尚未携带 `expected_revision`，属于读改写（后写覆盖），集成方不得依赖这些端点提供并发冲突提示。
- 冲突发生时，客户端保留本地草稿并明确处理远端新状态。
- 列表分页由服务器契约决定，客户端不得先取全集再伪造分页。
- 过期响应必须被抑制，不能覆盖较新的用户请求结果。

## 前端边界

Dashboard bridge 位于 [`pages/dashboard/src/lib/bridge.ts`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/pages/dashboard/src/lib/bridge.ts)。集成时应复用该边界的 envelope、冲突和错误处理语义，而不是建立第二套协议。

::: warning 管理权限
Page API 处于 AstrBot 宿主认证的管理边界内。不要把它直接暴露为普通用户可访问的公网 API。
:::
