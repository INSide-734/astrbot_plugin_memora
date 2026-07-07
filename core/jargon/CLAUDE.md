[根目录](../../CLAUDE.md) > [core](../) > [core/](../CLAUDE.md) > **jargon**

## 模块职责

`core/jargon/` 是 Memora 的黑话/圈内用语自动发现与理解子系统。它实现了完整的"发现-验证-查询"管线：先通过零 LLM 成本的统计预过滤器从群聊中识别候选词，再通过 LLM 三步推断引擎判断候选词是否为真正的群内黑话并提取含义，最终提供带缓存的查询服务供 Agent 工具和 LLM 上下文注入使用。

## 入口与启动

- **模块入口**: `core/jargon/__init__.py` -- 懒加载导出 `JargonStatisticalFilter`, `JargonMiner`, `JargonQueryService`, `JargonStore`
- **核心管线**:
  ```
  每条消息
    -> JargonStatisticalFilter.update(text, group_id, sender_id)  [零 LLM]
  
  定期触发 (或手动 run_once)
    -> JargonStatisticalFilter.get_candidates(group_id)  [候选词]
    -> JargonMiner.run_once(group_id)  [LLM 三步推断]
      -> 步骤 1: 基于上下文推断含义 (_PROMPT_STEP1_CONTEXT)
      -> 步骤 2: 仅基于词面推断含义 (_PROMPT_STEP2_TERM_ONLY)
      -> 步骤 3: 两者对比判定是否为黑话 (_PROMPT_STEP3_COMPARE)
    -> JargonStore.upsert()  [持久化]
  
  查询时
    -> JargonQueryService.query(keyword, group_id)  [TTLCache 缓存]
    -> JargonQueryService.check_and_explain(text, group_id)  [检测+解释]
  ```

## 对外接口

### JargonStatisticalFilter (零 LLM 统计预过滤器)

| 方法 | 签名 | 职责 |
|------|------|------|
| `update` | `(text, group_id, sender_id)` | 处理一条消息，更新内存统计 (O(1)) |
| `get_candidates` | `(group_id, limit=20, exclude_terms=None) -> list[JargonCandidate]` | 获取 Top-K 候选黑话 |
| `get_stats` | `(group_id) -> JargonStats` | 获取群组统计摘要 |
| `reset_group` | `(group_id)` | 清除指定群组统计数据 |

### JargonMiner (LLM 三步推断引擎)

| 方法 | 签名 | 职责 |
|------|------|------|
| `run_once` | `(group_id, limit=5) -> list[JargonMeaning]` | 执行一轮批量推断 |
| `infer_meaning` | `(candidate) -> JargonMeaning \| None` | 对单个候选词执行三步推断 |

### JargonQueryService (查询与注入服务)

| 方法 | 签名 | 职责 |
|------|------|------|
| `query` | `(keyword, group_id, use_cache=True) -> list[dict]` | 按关键词搜索黑话 |
| `check_and_explain` | `(text, group_id) -> str \| None` | 检查文本中的黑话并返回注入文本 |
| `get_group_jargon` | `(group_id, use_cache=True) -> list[dict]` | 获取群组所有已确认黑话 |
| `invalidate_cache` | `(group_id=None)` | 使缓存失效 |

### JargonStore (持久化层)

| 方法 | 签名 | 职责 |
|------|------|------|
| `upsert` | `(meaning)` | 插入或更新黑话记录 |
| `get_by_term` | `(term, group_id) -> JargonMeaning \| None` | 精确查询 |
| `list_by_group` | `(group_id, confirmed_only=True) -> list[JargonMeaning]` | 列出群组黑话 |
| `search` | `(keyword, group_id) -> list[JargonMeaning]` | LIKE 模糊搜索 |
| `confirm` | `(term, group_id, confirmed=True)` | 手动确认/取消确认 |
| `delete` | `(term, group_id)` | 删除黑话条目 |

## 关键依赖与配置

- **jieba**: 中文分词（统计过滤器分词 + 标准词过滤）
- **LLM 适配器**: `JargonMiner` 需要支持 `text_chat()` 或 `generate_response()` 或 `call_llm_with_retry()` 的 LLM 客户端
- **JargonMiner 配置**:
  - `inference_timeout` (`float | None`, 默认 120.0): 单个候选推断超时（秒），None 表示不启用超时
  - `INFERENCE_THRESHOLDS` (`list[int]`): 渐进触发阈值 `[3, 6, 10, 20, 40, 60, 100]`，count >= 100 时标记 is_complete
- **JargonQueryService**: `TTLCache(maxsize=500, ttl=60)` 查询缓存

## 数据模型

### JargonCandidate (dataclass)
统计过滤器产出的候选词：
| 字段 | 类型 | 说明 |
|------|------|------|
| term | str | 候选词文本 |
| group_id | str | 来源群组 ID |
| score | float | 三信号综合评分 [0, 1] |
| frequency | int | 群内出现次数 |
| unique_users | int | 不同用户数 |
| idf_score | float | 信号 1：跨群 IDF |
| burst_score | float | 信号 2：爆发频率 |
| concentration_score | float | 信号 3：用户集中度 |
| first_seen | float | 首次出现时间戳 |
| context_examples | list[str] | 上下文示例（最多 10 条） |

### JargonMeaning (dataclass)
LLM 推断出的黑话含义：

| 字段 | 类型 | 说明 |
|------|------|------|
| term | str | 黑话词条 |
| group_id | str | 来源群组 |
| meaning | str | 推断含义 |
| confidence | float | 置信度 [0, 1] |
| is_jargon | bool | 是否为真黑话 |
| is_confirmed | bool | 是否人工确认 |
| is_global | bool | 是否跨群通用 |
| is_complete | bool | 推断是否完成 (count >= 100) |
| count | int | 使用次数 |
| last_inference_count | int | 上次推断时的 count |
| context_examples | list[str] | 上下文示例 |

### JargonStats (dataclass)
群组统计摘要：`total_terms`, `candidate_count`, `top_candidates`

## 三信号统计过滤

统计过滤器为每个群组的每个词计算三信号综合评分，**无需 LLM 即可筛选候选黑话**，可将 LLM 调用量降低 70-80%。

| 信号 | 权重 | 公式 | 含义 |
|------|------|------|------|
| 跨群 IDF | 0.4 | `log(num_groups / groups_containing)` | 在群内频繁但在其他群罕见 -> 高分 |
| 爆发频率 | 0.3 | `frequency / age_days` | 近期快速获得关注 -> 高分 |
| 用户集中度 | 0.3 | `1.0 / unique_users` | 少数人使用 -> 高分（暗示专有词汇） |

**过滤规则**:
- 词长 >= 2 字符，频率 >= 3
- 排除 jieba 内置词典频率 > 100 的标准词汇
- 排除包含在 100+ 停用词 frozenset 中的常见词
- 排除 `@mention`、URL、`[图片]/[表情]` 标记、纯数字/标点
- 综合评分 >= 0.35 才视为候选

## LLM 三步推断引擎

### 步骤 1：上下文推断
向 LLM 发送词条 + 上下文，要求 LLM 基于上下文推断含义。若 `no_info: true` 则终止本轮。

### 步骤 2：词面推断
仅发送词条本身（不带上下文），要求 LLM 仅基于词面推断含义。

### 步骤 3：对比判定
将两个推断结果交给 LLM 比较：
- 相似 -> 不是黑话（词面意思已经清楚）
- 不同 -> 可能是黑话（上下文含义不同于词面含义）

### 置信度计算
```
confidence = min(1.0, signal_score * 0.6 + inference_bonus)
inference_bonus = 0.4 if is_jargon else 0.2
```

## 渐进阈值触发

`INFERENCE_THRESHOLDS = [3, 6, 10, 20, 40, 60, 100]`

候选词 count 每达到一个阈值时触发一次推断，用于渐进验证黑话性质：
- `count < 3` -> 不触发
- `count >= 3` -> 第一次推断
- `count >= 6` -> 第二次推断
- ...
- `count >= 100` -> 标记 `is_complete = True`

## 查询缓存 (TTLCache)

- FIFO 淘汰，maxsize=500，TTL=60 秒
- 缓存 key 策略:
  - `query:{group_id}:{keyword}` -- 搜索查询
  - `explain:{group_id}:{hash(text)}` -- 文本解释
  - `group:{group_id}` -- 群组黑话列表

## 存储层 (JargonStore)

继承 `BaseStore`，管理 `jargon_terms` 表：

### jargon_terms 表
| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | 自增主键 |
| term | TEXT | 黑话词条 |
| group_id | TEXT | 群组 ID |
| meaning | TEXT | 含义 |
| confidence | REAL | 置信度 |
| is_jargon | INTEGER | 是否为黑话 |
| is_confirmed | INTEGER | 是否人工确认 |
| is_global | INTEGER | 是否跨群通用 |
| is_complete | INTEGER | 推断是否完成 |
| count | INTEGER | 使用次数 |
| last_inference_count | INTEGER | 上次推断时的 count |
| context_examples | TEXT | JSON 序列化的上下文示例 |
| created_at | REAL | 创建时间 |
| updated_at | REAL | 更新时间 |

唯一约束: `UNIQUE(term, group_id)`
索引: `(group_id, term)`

## 测试与质量

- **测试文件**: `tests/test_jargon_*.py` -- 覆盖统计过滤器、三步推断、持久化、查询服务
- **API 测试**: `tests/test_api_jargon.py` -- REST API 端点测试
- **特殊函数**: `_safe_parse_json()` 处理 LLM 输出中的各种 JSON 格式问题（markdown 代码块、嵌套大括号、前导文本）

## 常见问题 (FAQ)

**Q: 统计过滤器的数据会持久化吗？**
A: 不会。统计过滤器是纯内存结构，重启后数据丢失，通过消息流隐式重建。只有 `JargonStore` 中的黑话含义是持久化的。

**Q: 如果 LLM 不可用怎么办？**
A: `JargonMiner.run_once()` 会在开始时检测 LLM 可用性。若不可用，跳过本轮推断并记录 warning 日志。统计过滤器独立运行，不受 LLM 状态影响。

**Q: 如何确认一个黑话？**
A: 通过 Dashboard 或 API 调用 `JargonStore.confirm(term, group_id)` 设置 `is_confirmed = True`。已确认的黑话会优先出现在查询结果中。

**Q: 为什么有些常见的网络用语会被标记为候选？**
A: 如果 jieba 词典中该词频率 <= 100，它可能被当作候选。可以通过排除词集合 `exclude_terms` 或提高评分阈值来过滤。

## 相关文件清单

| 文件 | 职责 |
|------|------|
| `__init__.py` (67 行) | 懒加载子模块导出 |
| `models.py` (118 行) | 数据模型：JargonCandidate, JargonMeaning, JargonStats |
| `jargon_miner.py` (612 行) | 三步推断引擎 + JSON 解析 + 渐进阈值 |
| `jargon_query.py` (277 行) | 查询服务 + TTLCache + 黑话匹配与解释 |
| `jargon_store.py` (269 行) | SQLite 持久化 (BaseStore) |
| `statistical_filter.py` (617 行) | 三信号统计预过滤器 + jieba 分词 |

## 变更记录 (Changelog)

| 日期 | 变更 | 描述 |
|------|------|------|
| 2026-07-07 | 深度扫描 | 完整读取 6 文件，生成 `core/jargon/CLAUDE.md` |
