"""反思响应的安全清洗与认知组件投喂辅助。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ....platform.context_helpers import get_persona_id
from ....shared.contracts.prompt_protection import (
    PROMPT_PROTECTION_REQUIRED_ATTR,
    PROMPT_PROTECTION_REQUIRED_EXTRA_KEY,
    PROMPT_PROTECTION_SCOPE_ATTR,
    PROMPT_PROTECTION_SCOPE_EXTRA_KEY,
)
from ...identity.domain.models import IdentityTrust, ResolvedIdentity

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent


class ReflectionContextMixin:
    """为 ReflectionHandler 提供响应清洗和认知投喂。"""

    def _sanitize_response_text(
        self,
        response_text: str,
        session_id: str,
        *,
        scope_id: str | None = None,
        protection_required: bool = False,
        event: AstrMessageEvent | None = None,
    ) -> str:
        """清理用户可见回复，并消费与该请求关联的保护作用域。"""
        try:
            if protection_required:
                if self._prompt_protection is None:
                    return ""
                has_scope = getattr(self._prompt_protection, "has_scope", None)
                if callable(has_scope) and not has_scope(scope_id):
                    self._discard_prompt_protection_scope(scope_id)
                    return ""
            if not protection_required and not self._config_manager.get(
                "security.sanitize_llm_response", True
            ):
                self._discard_prompt_protection_scope(scope_id)
                return response_text
            if self._prompt_protection is None:
                return response_text
            sanitized, report = self._prompt_protection.sanitize_response(
                response_text,
                enable_validation=self._config_manager.get(
                    "security.double_check_enabled",
                    True,
                ),
                scope_id=scope_id,
                consume_scope=True,
            )
            leaks = report.get("leaks_removed") or []
            validation_passed = report.get("validation_passed", True)
            if leaks or not validation_passed:
                logger.warning(
                    f"[{session_id}] LLM 回复触发安全清洗："
                    f"移除项数量={len(leaks)}, 校验通过={validation_passed}"
                )
            return sanitized if validation_passed else ""
        except asyncio.CancelledError:
            self._discard_prompt_protection_scope(scope_id)
            raise
        except Exception:
            self._discard_prompt_protection_scope(scope_id)
            logger.warning(
                f"[{session_id}] LLM 回复安全清洗失败，已阻止输出",
                exc_info=True,
            )
            return ""
        finally:
            if event is not None:
                self._clear_prompt_protection_context(event, scope_id)

    def _discard_prompt_protection_scope(self, scope_id: str | None) -> None:
        if self._prompt_protection is None or not scope_id:
            return
        discard = getattr(self._prompt_protection, "discard_scope", None)
        if callable(discard):
            try:
                discard(scope_id)
            except Exception:
                logger.warning("[反思处理] 请求安全关联清理失败", exc_info=True)

    def _clear_prompt_protection_context(
        self,
        event: AstrMessageEvent,
        scope_id: str | None,
    ) -> None:
        self._discard_prompt_protection_scope(scope_id)
        try:
            setter = getattr(event, "set_extra", None)
        except asyncio.CancelledError:
            raise
        except Exception:
            setter = None
            logger.warning(
                "[反思处理] 请求安全关联官方通道清理失败",
                exc_info=True,
            )
        if callable(setter):
            for key, value in (
                (PROMPT_PROTECTION_SCOPE_EXTRA_KEY, None),
                (PROMPT_PROTECTION_REQUIRED_EXTRA_KEY, False),
            ):
                try:
                    setter(key, value)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "[反思处理] 请求安全关联官方通道清理失败",
                        exc_info=True,
                    )
        for attr, reset_value in (
            (PROMPT_PROTECTION_SCOPE_ATTR, None),
            (PROMPT_PROTECTION_REQUIRED_ATTR, False),
        ):
            try:
                delattr(event, attr)
            except asyncio.CancelledError:
                raise
            except Exception:
                try:
                    setattr(event, attr, reset_value)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "[反思处理] 请求安全关联私有通道清理失败",
                        exc_info=True,
                    )

    @staticmethod
    def _get_prompt_protection_context(
        event: AstrMessageEvent,
    ) -> tuple[str | None, bool, bool]:
        official_scope: Any = None
        official_required: Any = False
        official_failed = False
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                official_scope = getter(PROMPT_PROTECTION_SCOPE_EXTRA_KEY)
                official_required = getter(PROMPT_PROTECTION_REQUIRED_EXTRA_KEY)
            except asyncio.CancelledError:
                raise
            except Exception:
                official_failed = True
                logger.warning(
                    "[反思处理] 请求安全关联官方通道读取失败",
                    exc_info=True,
                )
        try:
            private_scope = getattr(event, PROMPT_PROTECTION_SCOPE_ATTR, None)
            private_required = getattr(event, PROMPT_PROTECTION_REQUIRED_ATTR, False)
        except Exception:
            private_scope = None
            private_required = False
        scope_id = (
            official_scope
            if isinstance(official_scope, str) and official_scope
            else private_scope
            if isinstance(private_scope, str) and private_scope
            else None
        )
        required = official_required is True or private_required is True
        lookup_failed = (
            official_failed and scope_id is None and private_required is not True
        )
        return scope_id, required, lookup_failed

    def _writes_blocked(self) -> bool:
        if self._write_guard_cb is None:
            return False
        try:
            return bool(self._write_guard_cb())
        except Exception:
            logger.error("[反思处理] 写入维护状态检查失败", exc_info=True)
            return True

    async def _feed_cognitive_components(
        self,
        event: AstrMessageEvent,
        response_text: str,
        identity: ResolvedIdentity | None = None,
    ) -> None:
        """尽力将助手回复投喂给可选认知模块。"""
        session_id = event.unified_msg_origin or "default"
        sender_id = self._user_id_for_identity(event, identity)
        persona_id = await get_persona_id(self._context, event)
        try:
            if self._expression_learner is not None:
                self._expression_learner.buffer_message(
                    group_id=session_id,
                    sender_id=getattr(self._expression_learner, "bot_id", "bot"),
                    content=response_text,
                )
                await self._expression_learner.maybe_learn(
                    session_id,
                    persona_id=persona_id or "default",
                    user_id=None,
                )
        except Exception:
            logger.debug("[认知模块] 助手回复投喂到表达模式学习器失败", exc_info=True)

        try:
            if self._affection_manager is not None and sender_id is not None:
                user_text = await self._latest_user_text(session_id)
                await self._affection_manager.process_interaction(
                    user_id=sender_id,
                    group_id=session_id,
                    message=user_text,
                    bot_response=response_text,
                )
        except Exception:
            logger.debug("[认知模块] 好感度更新失败", exc_info=True)

        try:
            if (
                self._jargon_miner is not None
                and event.get_message_type() == MessageType.GROUP_MESSAGE
            ):
                await self._jargon_miner.run_once(session_id, limit=2)
        except Exception:
            logger.debug("[认知模块] 基于助手回复触发黑话挖掘失败", exc_info=True)

    @staticmethod
    def _user_id_for_identity(
        event: AstrMessageEvent,
        identity: ResolvedIdentity | None,
    ) -> str | None:
        """选择好感度用户标识；未注册协议保持旧事件发送者语义。"""

        if identity is not None:
            if identity.trust_status is IdentityTrust.TRUSTED:
                return identity.canonical_user_id
            if identity.trust_status is not IdentityTrust.UNSUPPORTED:
                return None
        getter = getattr(event, "get_sender_id", None)
        if not callable(getter):
            return None
        try:
            sender_id = getter()
        except Exception:
            return None
        if sender_id is None:
            return None
        normalized = str(sender_id).strip()
        return normalized or None

    async def _latest_user_text(self, session_id: str) -> str:
        try:
            recent = await self._conversation_manager.get_context(
                session_id,
                max_messages=4,
            )
            for msg in reversed(recent or []):
                if msg.get("role") == "user" and msg.get("content"):
                    return str(msg["content"])
        except Exception:
            logger.debug("[认知模块] 查询最近一条用户消息失败", exc_info=True)
        return ""

    _prompt_protection: Any
    _config_manager: Any
    _write_guard_cb: Any
    _context: Any
    _expression_learner: Any
    _affection_manager: Any
    _jargon_miner: Any
    _conversation_manager: Any
