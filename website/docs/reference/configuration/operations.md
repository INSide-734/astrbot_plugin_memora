---
aside: false
pageClass: config-reference-page
---

# 运维、可靠性与安全配置

本页逐项说明迁移、重建、备份、写入可靠性、成本、更新和安全配置。它们主要影响启动恢复、后台维护和管理操作，不应作为召回质量的常规调参入口。

备份、迁移、重建和更新都可能触发磁盘或网络操作。修改前先确认可用空间、数据备份和维护窗口；安全字段应保持默认开启，只有完成风险评估后才考虑放宽。

## 数据库迁移

配置域：`"migration_settings"`。控制插件启动时的数据库版本升级行为

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"migration_settings.auto_migrate"` | `"bool"` | `true` | - | 自动迁移<br><small>插件启动时自动检测并升级旧版本数据库。建议保持开启。</small> |
| `"migration_settings.create_backup"` | `"bool"` | `true` | - | 迁移前自动备份<br><small>执行数据库迁移前自动创建备份文件，防止迁移失败导致数据丢失。建议保持开启。</small> |

## 索引重建

配置域：`"index_rebuild_settings"`。控制 /memora rebuild-index 的分批大小、Embedding 请求速率和失败容忍度。低配云服务器建议保持较小并发。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"index_rebuild_settings.batch_size"` | `"int"` | `50` | 最小值：`1`<br>最大值：`500` | 读取批量<br><small>每批从 documents 表读取的记忆条数。越大内存峰值越高、失败时影响的条数越多，建议 25-100。</small> |
| `"index_rebuild_settings.embedding_batch_size"` | `"int"` | `8` | 最小值：`1`<br>最大值：`256` | Embedding 批量<br><small>单次 Embedding API 请求包含的文本数量。服务商 TPM 限流严格时继续调小。</small> |
| `"index_rebuild_settings.tasks_limit"` | `"int"` | `1` | 最小值：`1`<br>最大值：`8` | Embedding 并发<br><small>批量 Embedding 内部并发上限。为避免触发 API 限流，默认 1。</small> |
| `"index_rebuild_settings.max_retries"` | `"int"` | `5` | 最小值：`1`<br>最大值：`8` | 批次重试次数<br><small>单个 Embedding 批次失败后的最大重试次数。</small> |
| `"index_rebuild_settings.retry_base_delay"` | `"float"` | `30` | 最小值：`0`<br>最大值：`60` | 重试等待秒数<br><small>批次失败后的指数退避基础等待时间。遇到 429/TPM 限流时会至少等待 30 秒。</small> |
| `"index_rebuild_settings.batch_delay"` | `"float"` | `5` | 最小值：`0`<br>最大值：`10` | 读取批次间隔秒数<br><small>每个 documents 读取批次之间的额外等待时间，用于主动降低整体重建速率。</small> |
| `"index_rebuild_settings.request_delay"` | `"float"` | `5` | 最小值：`0`<br>最大值：`60` | Embedding 请求间隔秒数<br><small>每个 Embedding API 子请求之间的等待时间。频繁触发 429/TPM 时优先增大此项。</small> |
| `"index_rebuild_settings.max_failure_ratio"` | `"float"` | `0.02` | 最小值：`0`<br>最大值：`1` | 允许失败比例<br><small>全量向量重建时，失败比例不超过该值才允许切换新索引。0.02 表示最多允许 2% 失败。</small> |

## 定期备份

配置域：`"backup_settings"`。每日自动备份记忆数据库，防止数据意外丢失

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"backup_settings.enabled"` | `"bool"` | `true` | - | 启用每日自动备份<br><small>每日衰减任务执行后自动备份数据库。建议开启。</small> |
| `"backup_settings.keep_days"` | `"int"` | `7` | - | 备份保留天数<br><small>超过该天数的旧备份文件将被自动删除。默认保留 7 天。</small> |

## 写入可靠性

配置域：`"write_reliability"`。控制记忆写入操作的自动修复与重试行为，提升数据一致性。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"write_reliability.repair_enabled"` | `"bool"` | `true` | - | 启用写入自动修复<br><small>写入记忆后自动校验索引完整性，发现不一致时自动修复。建议开启。</small> |
| `"write_reliability.max_retries"` | `"int"` | `3` | - | 写入最大重试次数<br><small>单次写入操作失败后的最大重试次数。超过后记录错误并放弃本次写入。建议 3-5。</small> |

## 成本控制

配置域：`"cost_control"`。统一管理高成本 LLM 功能的启用与降级策略。balanced 模式默认禁止额外 LLM 调用。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"cost_control.mode"` | `"string"` | `"balanced"` | 可选：`"balanced"` / `"low_cost"` / `"quality"` | 成本模式<br><small>balanced: 默认禁止额外LLM调用; low_cost: 最小化所有成本; quality: 允许高成本路径(LLM reranker/strategyD等)</small> |
| `"cost_control.max_extra_llm_calls_per_turn"` | `"int"` | `0` | 最小值：`0`<br>最大值：`10` | 每轮额外 LLM 调用上限<br><small>被动召回 + 反思总共允许的额外 LLM 调用次数。balanced/low_cost 下默认 0。</small> |
| `"cost_control.allow_llm_reranker_in_passive_recall"` | `"bool"` | `false` | - | 允许被动召回触发 LLM reranker<br><small>WARNING: 开启会显著增加 token 消耗和延迟。仅 quality 模式建议开启。</small> |
| `"cost_control.allow_llm_topic_strategy_d"` | `"bool"` | `false` | - | 允许 strategy D（两阶段 LLM 话题分割）<br><small>WARNING: strategy D 需要多次 LLM 调用。仅 quality 模式或手动批处理建议开启。</small> |
| `"cost_control.max_reflection_parallel_llm_calls"` | `"int"` | `2` | 最小值：`1`<br>最大值：`8` | 单次反思流程允许并行执行的 LLM 请求数。提高可缩短批量处理时间，但会增加瞬时并发、限流风险和费用；Provider 配额较低时应保持较小值。 |
| `"cost_control.llm_reranker_min_candidates"` | `"int"` | `12` | 最小值：`1`<br>最大值：`50` | LLM reranker 最小候选数<br><small>候选数低于此值时不触发 LLM reranker（使用 MMR 替代）。</small> |
| `"cost_control.llm_reranker_prompt_chars"` | `"int"` | `3000` | 最小值：`500`<br>最大值：`10000` | LLM reranker prompt 最大字符数 |

## Dashboard 运行时构建

配置域：`"dashboard"`。控制是否允许通过 Web API 在运行时执行 dashboard 依赖安装和构建。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"dashboard.allow_runtime_build"` | `"bool"` | `false` | - | 允许运行时安装/构建 Dashboard<br><small>开启后非 runtime 包的引导页可以通过 Page API 执行“安装依赖”和“构建页面”。首次构建前临时开启，构建成功并刷新后恢复关闭。runtime 包无需开启；关闭该项不会禁用已经构建好的 Dashboard。</small> |
| `"dashboard.build_timeout_seconds"` | `"int"` | `120` | 最小值：`5`<br>最大值：`1800` | 运行时构建超时（秒）<br><small>运行时 dashboard install/build 的最大执行时间。超时后会终止子进程并返回错误。</small> |
| `"dashboard.max_output_chars"` | `"int"` | `20000` | 最小值：`1000`<br>最大值：`200000` | 构建输出最大字符数<br><small>运行时 install/build 返回给前端的 stdout/stderr 最大长度，超出部分会被截断。</small> |

## 插件 runtime 更新

配置域：`"update_settings"`。管理员可通过 /memora update 检查、下载或安装已发布的 runtime 包。下载会优先使用镜像地址，并在 SHA-256 校验通过后保存到插件数据目录；宿主支持时 Dashboard 可自动切换目录、单插件重载并在失败时恢复旧版本。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"update_settings.enabled"` | `"bool"` | `true` | - | 启用插件更新<br><small>关闭后 /memora update 不会访问网络。</small> |
| `"update_settings.mirror_url"` | `"string"` | `""` | - | GitHub 下载镜像前缀<br><small>留空使用官方地址；例如 https://ghproxy.net/ 或 https://mirror.example/，也可使用包含 {url} 占位符的地址。仅允许 HTTP(S)，不支持本地文件路径。</small> |
| `"update_settings.timeout_seconds"` | `"int"` | `30` | 最小值：`5`<br>最大值：`120` | 更新请求超时（秒）<br><small>单次 Release 元数据或安装包请求的超时时间。</small> |

## 安全防护

配置域：`"security"`。控制记忆注入提示词保护、LLM 回复清洗和结构化输出护栏。

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"security.prompt_protection_enabled"` | `"bool"` | `true` | - | 启用提示词保护<br><small>对注入的记忆上下文添加内部保护包装，降低提示词泄露和指令注入风险。</small> |
| `"security.sanitize_llm_response"` | `"bool"` | `true` | - | 清洗 LLM 回复<br><small>助手回复落库前移除泄露的内部提示词片段，避免污染长期记忆。</small> |
| `"security.guardrails_enabled"` | `"bool"` | `true` | - | 启用输出护栏<br><small>对 LLM 记忆抽取输出执行结构化校验和安全过滤。</small> |
| `"security.double_check_enabled"` | `"bool"` | `true` | - | 启用二次校验<br><small>提示词保护和回复清洗后执行额外校验，提升安全边界可靠性。</small> |
| `"security.wrapper_template_index"` | `"int"` | `0` | 最小值：`0`<br>最大值：`10` | 保护模板索引<br><small>选择提示词保护包装模板。默认 0。</small> |
| `"security.strict_mode"` | `"bool"` | `false` | - | 严格安全模式<br><small>开启后安全组件失败会跳过记忆注入或回复落库；关闭时记录告警并按兼容模式降级。</small> |

## 记忆导入导出

配置域：`"export"`。JSONL/Markdown 格式记忆导出和去重合并导入

| 配置项 | 类型 | 默认值 | 选项与范围 | 说明 |
|---|---|---|---|---|
| `"export.enabled"` | `"bool"` | `true` | - | 是否开放记忆导入与导出能力。关闭后相关管理入口不可用，可用于限制数据迁移面；它不会删除已经导出的文件或现有记忆。 |

返回[配置参考总览](/reference/configuration)。
