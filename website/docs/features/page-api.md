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

- 写回请求保留 revision，并通过比较后应用避免覆盖并发修改。
- 冲突发生时，客户端保留本地草稿并明确处理远端新状态。
- 列表分页由服务器契约决定，客户端不得先取全集再伪造分页。
- 过期响应必须被抑制，不能覆盖较新的用户请求结果。

## 前端边界

Dashboard bridge 位于 [`pages/dashboard/src/lib/bridge.ts`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/pages/dashboard/src/lib/bridge.ts)。集成时应复用该边界的 envelope、冲突和错误处理语义，而不是建立第二套协议。

::: warning 管理权限
Page API 处于 AstrBot 宿主认证的管理边界内。不要把它直接暴露为普通用户可访问的公网 API。
:::
