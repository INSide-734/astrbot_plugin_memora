"""测试 reconsolidation — ReconsolidationManager memory reconsolidation flow."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from core.managers.reconsolidation import ReconsolidationManager

# ---------------------------------------------------------------------------
# maybe_reconsolidate tests
# ---------------------------------------------------------------------------


class TestMaybeReconsolidate:
    """测试 maybe_reconsolidate 异步方法。"""

    def _make_mgr(self, **kwargs) -> ReconsolidationManager:
        return ReconsolidationManager(**kwargs)

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self) -> None:
        """当 disabled, returns None immediately."""
        mgr = ReconsolidationManager(enabled=False)
        result = await mgr.maybe_reconsolidate(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_get_memory_callback(self) -> None:
        """当 get_memory callback is None, returns None."""
        mgr = ReconsolidationManager(enabled=True)
        result = await mgr.maybe_reconsolidate(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_update_memory_callback(self) -> None:
        """当 update_memory callback is None, returns None."""
        get_cb = AsyncMock()
        mgr = ReconsolidationManager(
            enabled=True,
            get_memory_cb=get_cb,
            update_memory_cb=None,
        )
        result = await mgr.maybe_reconsolidate(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_memory_not_found(self) -> None:
        """当 get_memory returns None, returns None."""
        get_cb = AsyncMock(return_value=None)
        update_cb = AsyncMock()
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self) -> None:
        """当 memory text is empty, returns None."""
        get_cb = AsyncMock(return_value={"text": "", "metadata": {}})
        update_cb = AsyncMock()
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_reconsolidation_window_not_elapsed(self) -> None:
        """当 last reconsolidation was < 24h ago, returns None."""
        # Set last_reconsolidated_at to now
        recent_time = time.time()
        get_cb = AsyncMock(
            return_value={
                "text": "some memory",
                "metadata": {"last_reconsolidated_at": recent_time},
            }
        )
        update_cb = AsyncMock()
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_llm_no_context_returns_unchanged(self) -> None:
        """当 no LLM caller or empty context, returns unchanged."""
        get_cb = AsyncMock(
            return_value={
                "text": "some memory text here",
                "metadata": {},
            }
        )
        update_cb = AsyncMock()
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1, context="")
        assert result is not None
        assert result["revised"] is False
        assert result["reason"] == "unchanged"

    @pytest.mark.asyncio
    async def test_llm_revision_no_change_returns_unchanged(self) -> None:
        """当 LLM returns the same text, returns unchanged."""
        get_cb = AsyncMock(
            return_value={
                "text": "original memory text",
                "metadata": {},
            }
        )
        update_cb = AsyncMock()
        # LLM returns same text
        llm_cb = AsyncMock(return_value="original memory text")
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1, context="some context")
        assert result is not None
        assert result["revised"] is False

    @pytest.mark.asyncio
    async def test_llm_revision_applied(self) -> None:
        """当 LLM returns different text, revision is applied."""
        get_cb = AsyncMock(
            return_value={
                "text": "original memory text hello world",
                "metadata": {},
            }
        )
        update_cb = AsyncMock()
        llm_cb = AsyncMock(return_value="revised memory text hello world")
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1, context="new context info")
        assert result is not None
        assert result["revised"] is True
        assert result["memory_id"] == 1
        assert result["count"] == 1
        update_cb.assert_called_once()
        call_args = update_cb.call_args[0]
        assert call_args[0] == 1  # memory_id
        assert "original_content" in call_args[1]["metadata"]
        assert call_args[1]["content"] == "revised memory text hello world"

    @pytest.mark.asyncio
    async def test_reconsolidation_count_increments(self) -> None:
        """Reconsolidation count is incremented on each revision."""
        get_cb = AsyncMock(
            return_value={
                "text": "original text that is quite long for reconsolidation",
                "metadata": {"reconsolidation_count": 3},
            }
        )
        update_cb = AsyncMock()
        llm_cb = AsyncMock(
            return_value="revised text that is quite long for reconsolidation"
        )
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1, context="ctx")
        assert result["count"] == 4

    @pytest.mark.asyncio
    async def test_string_metadata_parsed(self) -> None:
        """JSON-string metadata is parsed before processing."""
        metadata = json.dumps({"reconsolidation_count": 1})
        get_cb = AsyncMock(
            return_value={
                "text": "original text for reconsolidation test here",
                "metadata": metadata,
            }
        )
        update_cb = AsyncMock()
        llm_cb = AsyncMock(return_value="revised text for reconsolidation test here")
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1, context="ctx")
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self) -> None:
        """当 LLM call raises, returns None (graceful degradation)."""
        get_cb = AsyncMock(
            return_value={
                "text": "original text for reconsolidation test here",
                "metadata": {},
            }
        )
        update_cb = AsyncMock()
        llm_cb = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1, context="ctx")
        # Should return unchanged since LLM failed and original kept
        assert result is not None
        assert result["revised"] is False

    @pytest.mark.asyncio
    async def test_short_llm_result_ignored(self) -> None:
        """LLM result shorter than 10 chars is ignored (unchanged)."""
        get_cb = AsyncMock(
            return_value={
                "text": "original text",
                "metadata": {},
            }
        )
        update_cb = AsyncMock()
        llm_cb = AsyncMock(return_value="short")  # < 10 chars
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1, context="ctx")
        assert result is not None
        assert result["revised"] is False

    @pytest.mark.asyncio
    async def test_outer_exception_returns_none(self) -> None:
        """Any outer exception returns None."""
        get_cb = AsyncMock(side_effect=Exception("Unexpected"))
        update_cb = AsyncMock()
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_original_content_preserved_on_first_revision(self) -> None:
        """The original text is saved as original_content on first reconsolidation."""
        get_cb = AsyncMock(
            return_value={
                "text": "very first original memory content here",
                "metadata": {},
            }
        )
        update_cb = AsyncMock()
        llm_cb = AsyncMock(return_value="revised memory content here after update")
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        await mgr.maybe_reconsolidate(1, context="ctx")
        update_cb.assert_called_once()
        meta = update_cb.call_args[0][1]["metadata"]
        assert meta["original_content"] == "very first original memory content here"

    @pytest.mark.asyncio
    async def test_original_content_not_overwritten_on_second_revision(self) -> None:
        """original_content is preserved even on subsequent reconsolidations."""
        get_cb = AsyncMock(
            return_value={
                "text": "already revised memory content here",
                "metadata": {
                    "original_content": "initial original content",
                },
            }
        )
        update_cb = AsyncMock()
        llm_cb = AsyncMock(return_value="further revised memory content here")
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        await mgr.maybe_reconsolidate(1, context="ctx")
        meta = update_cb.call_args[0][1]["metadata"]
        assert meta["original_content"] == "initial original content"

    @pytest.mark.asyncio
    async def test_use_content_field_fallback(self) -> None:
        """Memory using 'content' instead of 'text' field works."""
        get_cb = AsyncMock(
            return_value={
                "content": "content field memory here that works fine",
                "metadata": {},
            }
        )
        update_cb = AsyncMock()
        llm_cb = AsyncMock(return_value="revised content field memory here")
        mgr = ReconsolidationManager(
            get_memory_cb=get_cb,
            update_memory_cb=update_cb,
            llm_caller=llm_cb,
            enabled=True,
        )
        result = await mgr.maybe_reconsolidate(1, context="ctx")
        assert result["revised"] is True
