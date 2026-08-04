[`根级 AGENTS.md`](../AGENTS.md) > **runtime_tests**

# 真实运行时测试约束

本目录继承根级 `AGENTS.md` 的全部规则，用于从 AstrBot 公开边界验证
Memora 的真实运行时行为。这里的测试与 `tests/` 中基于 Mock 的单元和集成测试
严格隔离。

## 硬性边界

- 每个测试必须启动一个全新的 AstrBot 实例，不得复用其他测试的进程、数据目录
  或运行时状态。
- 运行期输入只能通过平台注册的消息入口或 HTTP 接口提交；不得直接调用插件对象、
  Provider、Manager、Store 或其他进程内实现。
- AstrBot 运行期间禁止读取进程内对象图和数据库。实例停止后，才允许以只读方式
  读取日志、数据库或其他落盘证据进行取证。
- 日志和失败产物必须脱敏，不得保留登录凭据、测试令牌、模型密钥、请求头、
  用户消息原文或其他敏感载荷。
- 测试驱动、探针和专用端点只属于测试基础设施，不得进入插件发布包或生产加载路径。
- 普通 Pull Request 不得使用真实模型密钥；应使用无秘密、确定性的本地测试
  Provider。只有受控的专用验证流程才能注入真实模型凭据。

## 当前档位

- `pr`：逐场景启动真实 AstrBot，覆盖 bootstrap、端口冲突恢复，以及经受保护
  HTTP 入口注入群消息、内置 `openai_chat_completion` adapter 调用回环 stub、
  Platform 回复、Memora hooks 和 Page API 记忆落库。stub 不保存请求正文。
- `live`：仅显式选择时读取四个 `MEMORA_LIVE_*` 环境变量；API Base 必须使用
  HTTPS、标准端口、非 IP 主机并命中显式白名单。Provider key 只写入一次性场景，
  不传给 AstrBot 子进程环境，结束后删除配置和原始日志。

测试消息只能包含随机或固定 canary，不得放入真实用户文本。测试事件会以固定占位符
代替请求和回复日志；harness 仍须把消息、Provider 回复、完整 key 及其前 12 位加入
失败日志脱敏表。
