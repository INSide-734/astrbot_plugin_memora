"""集中式配置默认值参考 — 所有配置键的默认值来源说明。

配置默认值的**唯一运行时来源**是 `config_validator.py` 中的 Pydantic 模型：
- `MemoraConfig` (及子模型) 的 `Field(default=...)` 定义了所有默认值
- `get_default_config()` → `MemoraConfig().model_dump()` 生成完整默认配置字典
- 各模块通过 `config_manager.get("key", default)` 访问，`default` 参数应
  与 Pydantic Field default 保持一致

配置键参考：
- 核心开关、检索权重、图检索、记忆原子 → `MemoraConfig` 各子模型
- 话题分割 (topic_segmentation.*) → `TopicSegmentationConfig` (v1.0.0+)
- 存量回填 (legacy_backfill.*) → `LegacyBackfillConfig` (v1.0.0+)
- AstrBot UI Schema → `_conf_schema.json`

更新配置键时请同步修改：本文件(文档)、config_validator.py(Pydantic默认值)、_conf_schema.json(Schema UI)
"""
