"""门禁 dry-run 页面接口处理器。

无副作用：不调 LLM、不写库、不读消息窗口，仅报告确定性检查、
命中规则与最终处置；响应仅回显 allowlist 标量。
"""

from __future__ import annotations

from typing import Any

from ....features.quality.application.gate_rule_engine import (
    CandidateView,
    evaluate_disposition,
    evaluate_rules,
)
from ....features.quality.domain.gate_config import (
    BUILTIN_GENERIC_TERMS,
    GateProfile,
)
from ....features.recall.processors.quality_validator import QualityValidator
from .response_utils import error_response, ok_response

_CONTENT_MAX = 2000
_LIST_MAX_ITEMS = 5
_LIST_ITEM_MAX = 200
_ID_MAX = 64
_PROFILE_MAX = 32
_CHAT_TYPE_MAX = 16

_LIST_FIELDS = ("key_facts", "topics", "participants")


def _generic_terms(profile: GateProfile) -> tuple[str, ...]:
    """按词表模式合并内置泛化词；replace 模式完全由配置掌控。"""
    config = profile.word_lists.generic_terms
    if config.mode == "replace":
        return tuple(config.items)
    return BUILTIN_GENERIC_TERMS + tuple(config.items)


def _validate_payload(payload: Any) -> str | None:
    """校验 dry-run 请求体字段类型与上限，返回 None 或中文错误。"""
    if not isinstance(payload, dict):
        return "请求体必须为 JSON 对象"
    content = payload.get("content")
    if content is not None and (
        not isinstance(content, str) or len(content) > _CONTENT_MAX
    ):
        return f"content 必须为不超过 {_CONTENT_MAX} 字符的字符串"
    summary = payload.get("summary")
    if summary is not None and (
        not isinstance(summary, str) or len(summary) > _CONTENT_MAX
    ):
        return f"summary 必须为不超过 {_CONTENT_MAX} 字符的字符串"
    for field in _LIST_FIELDS:
        items = payload.get(field)
        if items is None:
            continue
        if not isinstance(items, list) or len(items) > _LIST_MAX_ITEMS:
            return f"{field} 必须为不超过 {_LIST_MAX_ITEMS} 项的数组"
        if any(
            not isinstance(item, str) or len(item) > _LIST_ITEM_MAX for item in items
        ):
            return f"{field} 每项必须为不超过 {_LIST_ITEM_MAX} 字符的字符串"
    importance = payload.get("importance")
    if importance is not None and (
        isinstance(importance, bool)
        or not isinstance(importance, (int, float))
        or not 0.0 <= importance <= 1.0
    ):
        return "importance 必须为 [0,1] 区间内的数值"
    profile = payload.get("profile")
    if profile is not None and (
        not isinstance(profile, str) or len(profile) > _PROFILE_MAX
    ):
        return f"profile 必须为不超过 {_PROFILE_MAX} 字符的字符串"
    chat_type = payload.get("chat_type")
    if chat_type is not None and (
        not isinstance(chat_type, str) or len(chat_type) > _CHAT_TYPE_MAX
    ):
        return f"chat_type 必须为不超过 {_CHAT_TYPE_MAX} 字符的字符串"
    for field in ("group_id", "persona_id"):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > _ID_MAX):
            return f"{field} 必须为不超过 {_ID_MAX} 字符的字符串"
    return None


class GateApiMixin:
    """门禁 dry-run 接口：预览规则命中与处置，不产生副作用。"""

    plugin: Any

    def _get_web_request(self) -> Any:
        """获取当前页面请求，兼容旧版 Context.request 适配。"""
        context = getattr(self.plugin, "context", None)
        context_request = getattr(context, "request", None)
        if context_request is not None:
            return context_request
        try:
            from astrbot.api.web import request as astrbot_web_request
        except (ImportError, AttributeError):
            return None
        return astrbot_web_request

    def _gate_runtime(self) -> Any:
        """取组合根注入的门禁运行时；未初始化时返回 None。"""
        initializer = getattr(self.plugin, "initializer", None)
        return getattr(initializer, "gate_runtime", None)

    async def dry_run_gate(self) -> dict[str, Any]:
        """解析绑定上下文并返回确定性检查、命中规则与最终处置。"""
        request = self._get_web_request()
        try:
            payload = await request.get_json(silent=True) or {}
        except Exception:
            return error_response("请求体必须为 JSON 对象", code="gate_dry_run_invalid")
        field_error = _validate_payload(payload)
        if field_error is not None:
            return error_response(field_error, code="gate_dry_run_invalid")
        runtime = self._gate_runtime()
        if runtime is None:
            return error_response("门禁运行时不可用", code="gate_runtime_unavailable")
        explicit_profile = payload.get("profile")
        if explicit_profile is not None:
            # 显式给定（含空串）时直接查找，不回落绑定解析。
            profile: GateProfile | None = runtime.snapshot().profile_by_name(
                str(explicit_profile)
            )
        else:
            profile = runtime.resolve_profile(
                str(payload.get("chat_type") or "private"),
                payload.get("group_id"),
                payload.get("persona_id"),
            )
        if profile is None:
            return error_response("profile 不存在", code="gate_profile_not_found")
        view = CandidateView(
            content=str(payload.get("content") or ""),
            summary=str(payload.get("summary") or ""),
            key_facts=tuple(payload.get("key_facts") or ()),
            topics=tuple(payload.get("topics") or ()),
            participants=tuple(payload.get("participants") or ()),
            importance=float(payload.get("importance", 0.5)),
            chat_type=str(payload.get("chat_type") or "private"),
        )
        outcome = evaluate_rules(view, profile)
        quality = QualityValidator.validate_summary_quality(
            {
                "summary": view.summary or view.content,
                "key_facts": list(view.key_facts),
                "importance": view.importance,
            },
            min_summary_chars=profile.quality.min_summary_chars,
            generic_terms=_generic_terms(profile),
        )
        if not profile.checks.quality_low_check:
            quality = "normal"
        reason_codes = ("summary_quality_low",) if quality == "low" else ()
        disposition = evaluate_disposition(reason_codes, outcome, profile)
        return ok_response(
            {
                "profile": profile.name,
                "quality": quality,
                "matched_rules": list(outcome.matched_rule_ids),
                "disposition": disposition,
            }
        )
