<div align="center">

<img src="logo.png" alt="Memora Logo" width="200" />

# Memora

### 为 AstrBot 提供从对话理解、长期存储到安全召回与可视化管理的完整记忆系统

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-green.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-1.1.0-orange.svg)](metadata.yaml)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5%204.24.2-purple.svg)](https://github.com/Soulter/AstrBot)

</div>

Memora 是 AstrBot 的长期记忆插件，为对话提供记忆提取、混合检索、安全注入、稳定身份、关系演化与可视化管理能力。

完整中文文档：<https://inside-734.github.io/astrbot_plugin_memora/>

## 主要能力

- 以 SQLite canonical memory 作为唯一权威数据，全文、向量、图和 Projection 均可重建。
- 组合 BM25、FAISS、图检索、关系扩展与重排序，并按会话、身份和隐私边界过滤。
- 通过请求级策略路由安全注入相关记忆，不把动态记忆写入 System Prompt。
- 支持稳定身份、来源可追溯的自动/人工用户画像、知识库、笔记、好感度、社交关系和群聊黑话。
- 自动知识只从达到质量门槛的 canonical memory 生成带来源证据的 derived proposal，并通过
  Agent 工具、Page API 或 Dashboard 显式读取，不进入默认被动召回。
- 提供 Dashboard、管理命令、Agent 工具与 Page API。
- 提供诊断、评测、备份恢复、索引重建和校验后的在线更新。

## 环境要求

- Python `>=3.12,<3.13`
- AstrBot `>=4.24.2`
- 可用的 Embedding Provider
- 可用的 LLM Provider

Node.js 仅在开发 Dashboard 或文档站时需要，普通插件安装不需要 Node.js。

## 安装

### AstrBot 插件市场

在 AstrBot 管理面板中打开“插件市场”，搜索 `Memora` 并安装。

### Release 安装包

1. 打开 [最新 Release](https://github.com/INSide-734/astrbot_plugin_memora/releases/latest)。
2. 下载 `astrbot_plugin_memora-<version>-runtime.zip`。
3. 在 AstrBot 插件管理页面上传 ZIP；安装前无需解压。

安装后配置 Embedding Provider 与 LLM Provider，重启 AstrBot，然后以管理员身份检查状态：

```text
/memora status
```

打开管理面板：

```text
/memora webui
```

Provider 尚未就绪时，Memora 会在后台等待并重试，不会阻塞 AstrBot 的聊天主链路。

## 文档导航

- [项目介绍](https://inside-734.github.io/astrbot_plugin_memora/guide/introduction)
- [快速开始](https://inside-734.github.io/astrbot_plugin_memora/guide/getting-started)
- [配置入门](https://inside-734.github.io/astrbot_plugin_memora/guide/configuration)
- [核心概念](https://inside-734.github.io/astrbot_plugin_memora/concepts/architecture)
- [功能指南](https://inside-734.github.io/astrbot_plugin_memora/features/dashboard)
- [运维指南](https://inside-734.github.io/astrbot_plugin_memora/operations/backup-recovery)
- [管理命令](https://inside-734.github.io/astrbot_plugin_memora/reference/commands)
- [完整配置参考](https://inside-734.github.io/astrbot_plugin_memora/reference/configuration)
- [开发环境](https://inside-734.github.io/astrbot_plugin_memora/development/setup)
- [质量门禁](https://inside-734.github.io/astrbot_plugin_memora/development/quality-gates)

配置字段和默认值以仓库中的 [`_conf_schema.json`](_conf_schema.json) 为准，架构不变量见 [`DESIGN.md`](DESIGN.md)，发布记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可证

本项目基于 [GNU Affero General Public License v3.0](LICENSE) 开源。
