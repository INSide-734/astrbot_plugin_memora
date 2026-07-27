# ${package_name} ${version}

> ${release_kind} · 构建提交：`${commit_sha}`

## 安装包

| 文件 | 用途 | 大小 | SHA-256 |
| --- | --- | ---: | --- |
| `${runtime_filename}` | ${runtime_purpose} | ${runtime_size} | `${runtime_sha256}` |
| `${source_filename}` | ${source_purpose} | ${source_size} | `${source_sha256}` |

### 推荐安装

下载 `${runtime_filename}` 并将其安装到 AstrBot。系统概览支持自动安装时，可直接在更新卡片中确认安装；插件会校验 SHA-256、切换 runtime 并请求单插件重载，失败会自动恢复旧版本。若宿主不支持自动安装，请按 AstrBot 插件管理流程手动替换目录。该包包含 Dashboard 生产资源和插件运行时所需文件。

### 源码归档

`${source_filename}` 对应本次发布提交，适合代码审阅、问题定位和二次开发。

## 完整性校验

下载两个 ZIP 和 `SHA256SUMS.txt` 后，在同一目录执行：

```bash
sha256sum -c SHA256SUMS.txt
```

## 变更内容

${changelog}

## 构建来源

- 标签：`${release_tag}`
- 提交：`${commit_sha}`
- 质量门禁：`scripts/check_all.py`
- 打包入口：`scripts/package_plugin.py --mode both --from-git`
