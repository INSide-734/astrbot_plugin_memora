"""验证领域 Agent 工具通过公开 handler 保持原有调用语义。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.cognition.affection.models import BotMood, MoodType
from core.features.cognition.expression.models import ExpressionPattern
from core.features.cognition.expression.pattern_learner import ExpressionPatternLearner
from core.features.cognition.jargon.jargon_query import JargonQueryService
from core.features.cognition.social.models import SocialRelation
from core.platform.transport.tools.affection_tools import (
    AffectionCheckTool,
    BotMoodTool,
)
from core.platform.transport.tools.expression_tools import ExpressionRecallTool
from core.platform.transport.tools.jargon_tools import JargonExplainTool, JargonListTool
from core.platform.transport.tools.social_tools import (
    RelationGraphTool,
    RelationLookupTool,
)
from tests.tool_contract_support import call_text_handler


def _event() -> MagicMock:
    """构造可提供当前发送者和群组会话标识的消息事件。"""

    event = MagicMock()
    event.unified_msg_origin = "group:42"
    event.get_sender_id.return_value = "user-1"
    return event


@pytest.mark.asyncio
async def test_affection_check_handler_uses_event_identity() -> None:
    """好感度查询应从公开事件解析用户和群组标识。"""

    manager = MagicMock()
    manager.get_user_affection = AsyncMock(
        return_value=SimpleNamespace(
            user_id="user-1",
            group_id="group:42",
            affection_score=42,
            interaction_count=7,
            last_interaction=123.0,
        )
    )
    manager.get_mood = AsyncMock(
        return_value=BotMood(MoodType.HAPPY, description="心情很好")
    )
    tool = AffectionCheckTool(affection_manager=manager)

    payload = json.loads(await call_text_handler(tool, _event()))

    assert payload["found"] is True
    assert payload["affection_score"] == 42
    assert payload["bot_mood"] == "happy"
    manager.get_user_affection.assert_awaited_once_with("group:42", "user-1")
    manager.get_mood.assert_awaited_once_with("group:42")


@pytest.mark.asyncio
async def test_bot_mood_handler_uses_event_group() -> None:
    """情绪查询应从公开事件解析群组并保留情绪字段。"""

    manager = MagicMock()
    manager.get_mood = AsyncMock(
        return_value=BotMood(
            MoodType.CALM,
            intensity=0.6,
            description="平静",
            start_time=100.0,
            duration_hours=2.0,
        )
    )
    tool = BotMoodTool(affection_manager=manager)

    payload = json.loads(await call_text_handler(tool, _event()))

    assert payload["found"] is True
    assert payload["group_id"] == "group:42"
    assert payload["mood_type"] == "calm"
    assert payload["intensity"] == 0.6
    manager.get_mood.assert_awaited_once_with("group:42")


@pytest.mark.asyncio
async def test_jargon_explain_handler_uses_event_group() -> None:
    """黑话解释应把事件群组和规整后的词条交给查询服务。"""

    service = JargonQueryService(MagicMock())
    tool = JargonExplainTool(jargon_query_service=service)

    query = AsyncMock(return_value=[{"term": "awsl", "meaning": "啊我死了"}])
    with patch.object(service, "query", query):
        payload = json.loads(await call_text_handler(tool, _event(), term="  awsl  "))

    assert payload["found"] is True
    assert payload["count"] == 1
    query.assert_awaited_once_with("awsl", "group:42")


@pytest.mark.asyncio
async def test_jargon_list_handler_uses_event_group() -> None:
    """黑话列表应从公开事件解析群组并返回服务结果。"""

    service = JargonQueryService(MagicMock())
    tool = JargonListTool(jargon_query_service=service)

    get_group_jargon = AsyncMock(return_value=[{"term": "awsl"}])
    with patch.object(service, "get_group_jargon", get_group_jargon):
        payload = json.loads(await call_text_handler(tool, _event()))

    assert payload["found"] is True
    assert payload["count"] == 1
    get_group_jargon.assert_awaited_once_with("group:42")


@pytest.mark.asyncio
async def test_expression_recall_handler_preserves_filtering() -> None:
    """表达召回应保留事件群组、查询上限和情境过滤语义。"""

    learner = ExpressionPatternLearner(MagicMock())
    get_patterns = AsyncMock(
        return_value=[
            ExpressionPattern(
                situation="用户问候",
                expression="早上好",
                group_id="group:42",
                persona_id="default",
                pattern_id=9,
            ),
            ExpressionPattern(
                situation="用户道别",
                expression="回头见",
                group_id="group:42",
                persona_id="default",
                pattern_id=10,
            ),
        ]
    )
    tool = ExpressionRecallTool(expression_learner=learner)

    with patch.object(learner, "get_patterns_for_injection", get_patterns):
        payload = json.loads(
            await call_text_handler(tool, _event(), situation="问候", limit=3)
        )

    assert payload["found"] is True
    assert payload["count"] == 1
    assert payload["patterns"][0]["pattern_id"] == 9
    get_patterns.assert_awaited_once_with(
        "group:42",
        persona_id="default",
        user_id=None,
        limit=3,
    )


def _relation(*, relation_type: str, strength: float) -> SocialRelation:
    """构造领域工具格式化所需的最小社交关系。"""

    return SocialRelation(
        from_user="user-1",
        to_user="user-2",
        relation_type=relation_type,
        strength=strength,
        frequency=4,
        last_interaction=321.0,
        group_id="group:42",
        tags=["同事"],
    )


@pytest.mark.asyncio
async def test_relation_lookup_handler_uses_event_identity() -> None:
    """用户关系查询应从公开事件解析用户和群组标识。"""

    manager = MagicMock()
    manager.get_user_relations_in_group = AsyncMock(
        return_value=[_relation(relation_type="colleague", strength=0.7)]
    )
    tool = RelationLookupTool(relation_manager=manager)

    payload = json.loads(await call_text_handler(tool, _event()))

    assert payload["found"] is True
    assert payload["relations"][0]["relation_name_cn"] == "同事"
    manager.get_user_relations_in_group.assert_awaited_once_with(
        "user-1",
        "group:42",
    )


@pytest.mark.asyncio
async def test_relation_graph_handler_preserves_sort_and_summary() -> None:
    """群组关系图谱应保留强度排序和类型汇总语义。"""

    manager = MagicMock()
    manager.get_relations_by_group = AsyncMock(
        return_value=[
            _relation(relation_type="classmate", strength=0.4),
            _relation(relation_type="best_friend", strength=0.9),
        ]
    )
    tool = RelationGraphTool(relation_manager=manager)

    payload = json.loads(await call_text_handler(tool, _event()))

    assert payload["found"] is True
    assert [item["strength"] for item in payload["relations"]] == [0.9, 0.4]
    assert payload["type_summary"]["best_friend"] == {
        "count": 1,
        "name_cn": "挚友",
    }
    manager.get_relations_by_group.assert_awaited_once_with("group:42")
