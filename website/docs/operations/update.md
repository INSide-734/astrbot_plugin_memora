# 在线更新

Memora 可以从 GitHub Release 检查、下载并应用经过校验的 runtime 包。

## 管理命令

```text
/memora update check
/memora update download
/memora update apply
```

未提供子命令时默认执行 `check`。

## 下载与校验

下载会优先使用配置的镜像，失败后回退 GitHub，并复用 AstrBot 的 HTTP、HTTPS 或 SOCKS5 代理。

runtime 包只有通过 `SHA256SUMS.txt` 校验后，才会进入插件数据目录。校验失败必须终止，不得把未验证文件作为可安装更新。

## 应用更新

宿主支持单插件重载时，管理员可以确认自动安装。系统会原子切换目录并请求插件重载；重载失败时恢复旧目录。

宿主不支持安全重载时，流程降级为只下载，管理员需要按 AstrBot 的插件管理方式完成安装。

## 忽略版本

Dashboard System 页面可以查看发布说明并忽略指定版本。忽略只影响更新提示，不改变当前运行时或已下载文件的校验要求。

::: warning 更新前备份
涉及长期数据的生产实例应先创建并验证备份。更新包不会把派生索引当作 canonical 数据来源。
:::

最新发布见 [GitHub Releases](https://github.com/INSide-734/astrbot_plugin_memora/releases/latest)。
