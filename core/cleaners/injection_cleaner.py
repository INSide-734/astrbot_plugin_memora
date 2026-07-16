"""注入清理器 — 从 LLM 上下文中删除历史记忆注入片段和伪造工具调用"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..base.constants import (
    FAKE_TOOL_CALL_ID_PREFIX,
    FAKE_TOOL_CALL_NAME,
    MEMORY_INJECTION_FOOTER,
    MEMORY_INJECTION_HEADER,
)

if TYPE_CHECKING:
    from astrbot.api.provider import ProviderRequest

_VERIFIED_INJECTION_HEADER = "<memora-untrusted-memory>"
_VERIFIED_INJECTION_FOOTER = "</memora-untrusted-memory>"
_DEEPSEEK_REPLAY_HEADER = "[DeepSeekV4-FakeToolCall-Replay]"
_DEEPSEEK_REPLAY_FOOTER = "[/DeepSeekV4-FakeToolCall-Replay]"
_FAKE_TOOL_CALL_ID_PATTERN = re.compile(
    rf"{re.escape(FAKE_TOOL_CALL_ID_PREFIX)}(?:[0-9a-f]{{12}}|[0-9a-f]{{32}})\Z"
)
_LEGACY_FAKE_TOOL_CALL_ID_PATTERN = re.compile(
    rf"{re.escape(FAKE_TOOL_CALL_ID_PREFIX)}[0-9a-f]{{12}}\Z"
)
_INJECTION_CLEANUP_PATTERN = re.compile(
    "(?:"
    + re.escape(_DEEPSEEK_REPLAY_HEADER)
    + r".*?"
    + re.escape(_DEEPSEEK_REPLAY_FOOTER)
    + "|"
    + re.escape(_VERIFIED_INJECTION_HEADER)
    + r".*?"
    + re.escape(_VERIFIED_INJECTION_FOOTER)
    + "|"
    + re.escape(MEMORY_INJECTION_HEADER)
    + r".*?"
    + re.escape(MEMORY_INJECTION_FOOTER)
    + ")",
    flags=re.DOTALL,
)


def _contains_injection(value: str) -> bool:
    return _INJECTION_CLEANUP_PATTERN.search(value) is not None


def _is_memora_fake_call_id(value: object) -> bool:
    return isinstance(value, str) and _FAKE_TOOL_CALL_ID_PATTERN.fullmatch(value) is not None


def _legacy_fake_tool_payload(value: str, expected_query: str) -> bool:
    decoder = json.JSONDecoder()
    payload = None
    for match in re.finditer(r"\{", value):
        try:
            candidate, _ = decoder.raw_decode(value[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "results" in candidate:
            payload = candidate
            break
    if payload is None or payload.get("query") != expected_query:
        return False
    filters = payload.get("applied_filters")
    results = payload.get("results")
    count = payload.get("count")
    if (
        not isinstance(filters, dict)
        or set(filters) != {"session_filtered", "persona_filtered"}
        or not all(isinstance(flag, bool) for flag in filters.values())
        or not isinstance(results, list)
        or not results
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != len(results)
    ):
        return False
    result_keys = {
        "id", "content", "score", "importance", "session_id", "persona_id",
        "create_time", "last_access_time",
    }
    return all(
        isinstance(result, dict)
        and set(result) == result_keys
        and isinstance(result.get("content"), str)
        for result in results
    )


class InjectionCleaner:
    """清理 LLM 请求上下文中的历史记忆注入和伪造工具调用"""

    @staticmethod
    def remove_injected_memories_from_context(
        req: ProviderRequest,
        session_id: str,
    ) -> int:
        """从非系统提示词上下文中删除之前注入的记忆片段。"""
        removed_count = 0
        pattern = _INJECTION_CLEANUP_PATTERN

        try:
            if (
                hasattr(req, "extra_user_content_parts")
                and req.extra_user_content_parts
            ):
                kept_parts = []
                for part in req.extra_user_content_parts:
                    text = getattr(part, "text", "")
                    if isinstance(text, str) and _contains_injection(text):
                        removed_count += 1
                        logger.debug(
                            f"[{session_id}] 从extra_user_content_parts中清理记忆片段"
                        )
                        continue
                    kept_parts.append(part)
                req.extra_user_content_parts = kept_parts

            if (
                hasattr(req, "prompt")
                and req.prompt
                and isinstance(req.prompt, str)
            ):
                original_prompt = req.prompt
                if _contains_injection(original_prompt):
                    cleaned_prompt = pattern.sub("", original_prompt)
                    cleaned_prompt = re.sub(
                        r"\n{3,}", "\n\n", cleaned_prompt
                    ).strip()
                    req.prompt = cleaned_prompt
                    if cleaned_prompt != original_prompt:
                        removed_count += 1
                        logger.debug(
                            f"[{session_id}] 从req.prompt中清理记忆片段 "
                            f"(原长度={len(original_prompt)}, 新长度={len(cleaned_prompt)})"
                        )

            if hasattr(req, "contexts") and req.contexts:
                filtered_contexts = []

                for msg in req.contexts:
                    if isinstance(msg, str):
                        content = msg
                    elif isinstance(msg, dict):
                        if msg.get("role") == "tool":
                            filtered_contexts.append(msg)
                            continue
                        content = msg.get("content", "")
                        if not isinstance(content, (str, list)):
                            filtered_contexts.append(msg)
                            continue
                    else:
                        filtered_contexts.append(msg)
                        continue

                    if isinstance(content, str):
                        if _contains_injection(content):
                            cleaned_content = pattern.sub("", content).strip()
                            cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content)
                            if not cleaned_content:
                                removed_count += 1
                                continue
                            if cleaned_content != content:
                                removed_count += 1
                                if isinstance(msg, str):
                                    filtered_contexts.append(cleaned_content)
                                else:
                                    msg_copy = msg.copy()
                                    msg_copy["content"] = cleaned_content
                                    filtered_contexts.append(msg_copy)
                                continue

                    elif isinstance(content, list):
                        cleaned_parts = []
                        has_changes = False
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text = part.get("text", "")
                                if isinstance(text, str) and _contains_injection(text):
                                    cleaned_text = pattern.sub("", text).strip()
                                    cleaned_text = re.sub(
                                        r"\n{3,}", "\n\n", cleaned_text
                                    )
                                    if not cleaned_text:
                                        has_changes = True
                                        continue
                                    if cleaned_text != text:
                                        has_changes = True
                                        removed_count += 1
                                        part_copy = part.copy()
                                        part_copy["text"] = cleaned_text
                                        cleaned_parts.append(part_copy)
                                        continue
                            cleaned_parts.append(part)
                        if not cleaned_parts:
                            removed_count += 1
                            continue
                        if has_changes:
                            msg_copy = msg.copy()
                            msg_copy["content"] = cleaned_parts
                            filtered_contexts.append(msg_copy)
                            continue

                    filtered_contexts.append(msg)

                req.contexts = filtered_contexts

            if removed_count > 0:
                logger.info(
                    f"[{session_id}] 成功清理旧记忆片段，共删除 {removed_count} 处注入内容"
                )

        except Exception as e:
            logger.error(f"[{session_id}] 删除注入记忆时发生错误: {e}", exc_info=True)

        return removed_count

    @staticmethod
    async def cleanup_injected_memories_from_db(
        connection,
        write_lock: asyncio.Lock,
        session_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, int | str]:
        """批量清理数据库中消息内容里的记忆注入片段。

        Args:
            connection: aiosqlite 数据库连接
            write_lock: asyncio.Lock 写锁
            session_id: 指定会话ID，为None则清理所有会话
            dry_run: 是否为预演模式（只统计不修改）

        Returns:
            dict: 清理统计信息
        """
        if connection is None:
            return {"error": 1, "message": "数据库连接未初始化"}  # type: ignore[return-value]

        stats: dict[str, int | str] = {
            "scanned": 0,
            "matched": 0,
            "cleaned": 0,
            "deleted": 0,
            "errors": 0,
        }

        try:
            async with write_lock:
                query = """
                    SELECT id, session_id, content
                    FROM messages
                    WHERE (
                        content LIKE ?
                        OR content LIKE ?
                        OR content LIKE ?
                    )
                """
                params: list[str | int] = [
                    f"%{MEMORY_INJECTION_HEADER}%",
                    f"%{_VERIFIED_INJECTION_HEADER}%",
                    f"%{_DEEPSEEK_REPLAY_HEADER}%",
                ]

                if session_id:
                    query += " AND session_id = ?"
                    params.append(session_id)

                async with connection.execute(query, params) as cursor:
                    rows = await cursor.fetchall()

                rows_list = list(rows)
                stats["scanned"] = len(rows_list)

                for row in rows_list:
                    msg_id = row["id"]
                    msg_session = row["session_id"]
                    original_content = row["content"]

                    if not isinstance(original_content, str) or not _contains_injection(
                        original_content
                    ):
                        continue

                    stats["matched"] += 1  # type: ignore[operator]

                    cleaned_content = _INJECTION_CLEANUP_PATTERN.sub(
                        "", original_content
                    )
                    cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content).strip()

                    if not cleaned_content:
                        if not dry_run:
                            await connection.execute(
                                "DELETE FROM messages WHERE id = ?", (msg_id,)
                            )
                        stats["deleted"] += 1  # type: ignore[operator]
                        logger.debug(
                            f"[cleanup_injected_memories] {'[DRY-RUN] ' if dry_run else ''}"
                            f"删除纯记忆消息: id={msg_id}, session={msg_session}"
                        )
                        continue

                    if cleaned_content != original_content:
                        if not dry_run:
                            await connection.execute(
                                "UPDATE messages SET content = ? WHERE id = ?",
                                (cleaned_content, msg_id),
                            )
                        stats["cleaned"] += 1  # type: ignore[operator]
                        logger.debug(
                            f"[cleanup_injected_memories] {'[DRY-RUN] ' if dry_run else ''}"
                            f"清理消息: id={msg_id}, "
                            f"原长度={len(original_content)}, "
                            f"新长度={len(cleaned_content)}"
                        )

                if not dry_run:
                    await connection.commit()

            logger.info(
                f"[cleanup_injected_memories] {'[DRY-RUN] ' if dry_run else ''}"
                f"清理完成: 扫描={stats['scanned']}, 匹配={stats['matched']}, "
                f"清理={stats['cleaned']}, 删除={stats['deleted']}"
            )

        except Exception as e:
            stats["errors"] = 1
            logger.error(f"批量清理记忆注入失败: {e}", exc_info=True)

        return stats  # type: ignore[return-value]

    @staticmethod
    def remove_fake_tool_call_from_context(
        req: ProviderRequest,
        session_id: str,
    ) -> int:
        """Remove only adjacent, executor-shaped verified fake-tool pairs."""
        if not hasattr(req, "contexts") or not req.contexts:
            return 0

        removed = 0
        try:
            contexts = req.contexts
            kept: list[object] = []
            index = 0
            while index < len(contexts):
                assistant = contexts[index]
                tool = contexts[index + 1] if index + 1 < len(contexts) else None
                if InjectionCleaner._is_verified_fake_tool_pair(assistant, tool):
                    removed += 2
                    index += 2
                    continue
                kept.append(assistant)
                index += 1
            if removed:
                req.contexts = kept
                logger.info(f"[{session_id}] 清理了 {removed} 条伪造工具调用消息")
        except Exception as e:
            logger.error(
                f"[{session_id}] 清理伪造工具调用时发生错误: {e}",
                exc_info=True,
            )
        return removed

    @staticmethod
    def _is_verified_fake_tool_pair(assistant: object, tool: object) -> bool:
        if not isinstance(assistant, dict) or not isinstance(tool, dict):
            return False
        if assistant.get("role") != "assistant" or tool.get("role") != "tool":
            return False
        calls = assistant.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            return False
        call = calls[0]
        if not isinstance(call, dict):
            return False
        call_id = call.get("id")
        function = call.get("function")
        if (
            not _is_memora_fake_call_id(call_id)
            or not isinstance(function, dict)
            or function.get("name") != FAKE_TOOL_CALL_NAME
        ):
            return False
        content = tool.get("content")
        if not isinstance(content, str):
            return False
        if _LEGACY_FAKE_TOOL_CALL_ID_PATTERN.fullmatch(call_id):
            try:
                arguments = json.loads(function.get("arguments", ""))
            except (TypeError, json.JSONDecodeError):
                return False
            expected_query = (
                arguments.get("query") if isinstance(arguments, dict) else None
            )
            if not isinstance(expected_query, str):
                return False
            valid_content = _legacy_fake_tool_payload(content, expected_query)
        else:
            envelope_start = content.find(_VERIFIED_INJECTION_HEADER)
            envelope_end = content.find(
                _VERIFIED_INJECTION_FOOTER,
                envelope_start + len(_VERIFIED_INJECTION_HEADER),
            )
            valid_content = envelope_start >= 0 and envelope_end >= 0
        return (
            tool.get("tool_call_id") == call_id
            and tool.get("name") == FAKE_TOOL_CALL_NAME
            and valid_content
        )
