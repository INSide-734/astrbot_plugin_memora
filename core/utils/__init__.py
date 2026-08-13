"""
utils 子模块
"""

from ..features.cognition.expression.diversity_manager import (
    EXPRESSION_VARIATIONS,
    LANGUAGE_STYLES,
    RESPONSE_PATTERNS,
    TEMPERATURE_RANGES,
    HomogeneityReport,
    ResponseDiversityManager,
    VariationComposition,
)
from ..features.cognition.expression.style_analyzer import (
    StyleAnalyzer,
    StyleEvolution,
    StyleProfile,
)
from ..features.injection.application.memory_formatter import (
    format_memories_for_fake_tool_call,
    format_memories_for_fake_tool_call_deepseek_v4,
    format_memories_for_injection,
)
from ..features.recall.processors.stopwords_manager import (
    StopwordsManager,
    get_stopwords_manager,
)
from ..features.recall.processors.text_processor import TextProcessor
from ..platform.context_helpers import (
    get_now_datetime,
    get_now_datetime_from_context,
    get_persona_id,
)
from ..platform.task_scheduler import TaskScheduler, get_task_scheduler
from ..shared.cache_manager import CacheManager, get_cache_manager
from ..shared.data_helpers import (
    OperationContext,
    retry_on_failure,
    safe_parse_metadata,
    safe_serialize_metadata,
    validate_timestamp,
)
from ..shared.json_utils import (
    _convert_single_quotes,
    clean_control_characters,
    clean_markdown_blocks,
    detect_llm_provider,
    extract_json_content,
    extract_json_from_response,
    fix_common_json_errors,
    remove_thinking_content,
    safe_parse_llm_json,
)

__all__ = [
    "StopwordsManager",
    "get_stopwords_manager",
    "TextProcessor",
    "safe_parse_metadata",
    "safe_serialize_metadata",
    "validate_timestamp",
    "retry_on_failure",
    "OperationContext",
    "get_persona_id",
    "extract_json_from_response",
    "get_now_datetime",
    "get_now_datetime_from_context",
    "format_memories_for_injection",
    "format_memories_for_fake_tool_call",
    "format_memories_for_fake_tool_call_deepseek_v4",
    # JSON 工具
    "safe_parse_llm_json",
    "remove_thinking_content",
    "clean_markdown_blocks",
    "clean_control_characters",
    "extract_json_content",
    "fix_common_json_errors",
    "_convert_single_quotes",
    "detect_llm_provider",
    # 缓存管理
    "CacheManager",
    "get_cache_manager",
    # 任务调度
    "TaskScheduler",
    "get_task_scheduler",
    # 多样性管理
    "ResponseDiversityManager",
    "HomogeneityReport",
    "VariationComposition",
    "LANGUAGE_STYLES",
    "RESPONSE_PATTERNS",
    "EXPRESSION_VARIATIONS",
    "TEMPERATURE_RANGES",
    # 风格分析
    "StyleAnalyzer",
    "StyleProfile",
    "StyleEvolution",
]
