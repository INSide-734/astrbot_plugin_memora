# 为 Memora 贡献

感谢参与 Memora。提交内容即表示你同意遵守[社区行为准则](CODE_OF_CONDUCT.md)。

## 先选择正确入口

- 安装、配置和使用咨询请前往 [GitHub Discussions](https://github.com/INSide-734/astrbot_plugin_memora/discussions)。
- 可复现缺陷、功能建议和文档问题请使用对应 Issue Form。
- 安全漏洞请按[安全政策](SECURITY.md)私密报告，不要创建公开 Issue。

开始较大改动前，请先通过 Issue 说明问题、目标和边界，避免重复实现或与现有设计冲突。

## 开发环境

Python 版本、锁定环境、Git hook、Dashboard 依赖和完整门禁以[开发环境与门禁说明](../docs/DEV_SETUP.md)为准。不要只修改本地 `.venv`，也不要绕过 pre-commit。

## 实施原则

- Bug 修复和新行为遵循 RED → GREEN → REFACTOR。
- 只修改完成目标所需的文件，不清理无关代码。
- 生产代码、测试、脚本和文档中的解释性文本使用中文。
- 配置叶变更需要同步 Schema、模型、运行时、Dashboard 和契约测试。
- 不记录密钥、请求头、身份信息、记忆正文或运行时数据库。

## 分支、提交与 PR

从最新 `main` 创建描述性分支。提交信息沿用仓库现有 Conventional Commits 风格，例如 `fix: ...`、`feat: ...`、`docs: ...`。

PR 应保持单一目标，并说明：

- 关联 Issue 或没有 Issue 的原因；
- 实际改动、验证命令和结果；
- 配置、API、存储、隐私、协议身份与兼容性风险；
- Dashboard 变更的桌面和移动端脱敏截图。

维护者会结合 CODEOWNERS、路径标签、现有 CI 和代码质量门禁进行审阅。评审意见解决前不要隐藏失败检查或扩大变更范围。
