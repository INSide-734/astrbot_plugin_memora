# 架构导览

本页帮助高级用户和贡献者理解 Memora 的主要链路。完整的内部不变量以仓库根级 [`DESIGN.md`](https://github.com/INSide-734/astrbot_plugin_memora/blob/main/DESIGN.md) 为准。

## 组件关系

```mermaid
flowchart TD
    AstrBot["AstrBot 事件与 Provider"] --> Plugin["MemoraPlugin"]
    Plugin --> Init["PluginInitializer"]
    Plugin --> Events["EventHandler"]
    Plugin --> API["PluginPageApi"]
    Init --> Engine["MemoryEngine"]
    Events --> Recall["RecallHandler"]
    Events --> Reflect["ReflectionHandler"]
    Recall --> Retrieval["BM25 + FAISS + Graph"]
    Recall --> Injection["Router + Executor"]
    Reflect --> Processor["MemoryProcessor"]
    Processor --> Engine
    Engine --> SQLite["SQLite 权威持久化"]
    Engine --> Evolution["Memory Evolution"]
    Evolution --> Derived["Relation / Projection"]
    Derived --> Retrieval
    API --> Dashboard["Dashboard"]
```

## 写入链

1. `EventHandler` 接收 AstrBot 消息事件。
2. 会话管理和内容提取建立可处理上下文。
3. `MemoryProcessor` 抽取并分类值得长期保留的信息。
4. `MemoryEngine` 在统一提交边界写入 SQLite。
5. 全文、向量、图和演化任务从 canonical 数据派生。

普通可恢复失败会降级对应能力，不应破坏聊天主链路；异步取消必须继续传播。

## 召回链

新的请求经过查询处理、作用域过滤、多路检索、关系扩展、Projection 附着、重排序和隐私过滤，然后交给注入策略路由与执行器。请求变更必须先完整构建，再原子应用。

## 三个不变量

1. SQLite canonical memory 及其整数 ID 始终是唯一权威身份。
2. FTS5、FAISS、图、Relation 和 Projection 是可校验、可失效、可重建的派生层。
3. 动态记忆只在当前请求内临时提供，不进入 System Prompt。

## 管理边界

`PluginPageApi` 与 Dashboard bridge 是前后端边界。写回请求保留 revision、字段校验和显式错误 envelope；Dashboard 不伪造客户端分页，也不把内部异常原样暴露给浏览器。
