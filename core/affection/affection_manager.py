"""好感度管理器：协调交互分类、分数更新、情绪门控与级联。"""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any, Protocol

from astrbot.api import logger

from ..base.entity_editing import EntityValidationError, compute_entity_revision
from ..base.list_sorting import SortQuery
from .affection_store import AffectionStore
from .models import (
    INTERACTION_RULES,
    AffectionLevel,
    BotMood,
    InteractionType,
    MoodType,
    UserAffection,
    classify_by_keywords,
)

# ---- LLM 适配器协议 --------------------------------------------------------------


class LLMAdapter(Protocol):
    """好感度管理器所需的最小 LLM 适配器协议。"""

    async def chat_completion(self, prompt: str, temperature: float = 0.1) -> str: ...


# ---- 各情绪类型的描述文本 --------------------------------------------------------


_MOOD_DESCRIPTIONS: dict[MoodType, list[str]] = {
    MoodType.HAPPY: [
        "今天心情特别好，看什么都觉得很有趣呢~",
        "感觉整个世界都充满了阳光，好开心啊！",
        "今天是个美好的一天，想和大家多聊聊天~",
    ],
    MoodType.EXCITED: [
        "哇！感觉有好多有趣的事情要发生，好兴奋！",
        "今天充满了活力，什么都想尝试一下！",
        "感觉像是喝了好多咖啡，特别有精神~",
    ],
    MoodType.CALM: [
        "今天的心情很平静，适合安静地聊天。",
        "感觉内心很宁静，想听听大家的故事。",
        "今天想要慢节奏地度过，不着急。",
    ],
    MoodType.PLAYFUL: [
        "今天想要开点小玩笑，大家别介意哦~",
        "感觉特别想玩，有什么有趣的游戏吗？",
        "今天的心情很调皮，想逗大家开心！",
    ],
    MoodType.SAD: [
        "今天有点忧郁，需要大家的安慰呢...",
        "心情有些低落，希望能得到一些温暖的话语。",
        "感觉有点孤单，想要更多的陪伴。",
    ],
    MoodType.ANXIOUS: [
        "今天有些紧张不安，需要大家多包容一下。",
        "感觉心里有点忐忑，不太确定该怎么办。",
        "今天的状态不是很稳定，可能反应会有点慢。",
    ],
    MoodType.ANGRY: [
        "今天心情不太好，可能说话会比较直接。",
        "感觉有些烦躁，需要一些时间平静下来。",
        "今天不太想被打扰，希望大家理解。",
    ],
    MoodType.SERIOUS: [
        "今天想要认真讨论一些问题，专注一点。",
        "感觉需要集中精力，暂时不太想开玩笑。",
        "今天的心境比较严肃，想深入思考。",
    ],
    MoodType.NOSTALGIC: [
        "今天想起了很多过往的事情，有点怀念。",
        "感觉很想回忆以前的美好时光。",
        "今天的心情有些感性，容易触景生情。",
    ],
    MoodType.CURIOUS: [
        "今天对什么都很好奇，想了解更多！",
        "感觉有好多问题想问，希望大家不要嫌烦。",
        "今天的求知欲特别强，想学习新的东西。",
    ],
}


def _random_mood_description(mood_type: MoodType) -> str:
    """为指定情绪类型随机选择一条描述文本。"""
    pool = _MOOD_DESCRIPTIONS.get(mood_type, ["今天的心情很特别。"])
    return random.choice(pool)


# ---- 分类提示词模板 ---------------------------------------------------


_CLASSIFICATION_PROMPT = """请分析以下用户消息属于什么类型的交互行为：

用户消息：{message}
机器人回复：{bot_response}
{mood_context}

可能的交互类型：
积极类型：
- chat: 普通聊天
- compliment: 称赞鼓励 (例如：你好美、你真棒、好厉害等)
- praise: 夸赞表扬 (例如：做得好、很优秀等)
- encourage: 鼓励支持
- support: 支持认同
- flirt: 撩拨调情 (例如：好看、漂亮、可爱等)
- comfort: 安慰关怀
- help: 寻求帮助
- thanks: 表达感谢
- apology: 道歉认错
- tease: 善意调侃
- care: 关心问候 (例如：你好吗、怎么样等)
- gift: 赠送礼物

负面类型：
- insult: 明确的侮辱攻击 (例如：蠢货、白痴、垃圾等恶毒词汇)
- harassment: 骚扰行为 (例如：持续骚扰、不当言论等)
- abuse: 恶意谩骂 (例如：脏话、恶毒攻击等)
- threat: 威胁恐吓 (例如：威胁、恐吓等)

请仔细分析消息的情感色彩和意图，特别注意：
1. "你好美"、"很漂亮"、"真可爱"等是赞美，应归类为compliment或flirt
2. 只有明确包含侮辱、攻击性词汇时才是insult
3. 只有真正的骚扰、威胁性表达才是负面类型
4. 当不确定时，优先选择积极类型或chat

请只返回一个类型名称，不要其他内容。"""


# ---- 好感度管理器 ------------------------------------------------------------------


class AffectionManager:
    """编排一次交互中的完整好感度生命周期。"""

    DEFAULT_MOOD = MoodType.CALM
    DEFAULT_INTENSITY = 0.5

    def __init__(
        self,
        store: AffectionStore,
        llm_adapter: LLMAdapter | None = None,
        *,
        max_affection: int = 100,
        min_affection: int = -100,
        max_total_affection: int = 5000,
        affection_decay_rate: float = 0.5,
    ) -> None:
        """初始化好感度管理器。"""
        self._store = store
        self._llm = llm_adapter
        self._max_affection = max_affection
        self._min_affection = min_affection
        self._max_total_affection = max_total_affection
        self._decay_rate = affection_decay_rate

        # 内存中的情绪缓存：group_id -> BotMood
        self._mood_cache: dict[str, BotMood] = {}
        self._mood_lock = asyncio.Lock()

    # ---- 对外 API --------------------------------------------------------------------

    async def process_interaction(
        self,
        user_id: str,
        group_id: str,
        message: str,
        bot_response: str = "",
    ) -> dict[str, Any]:
        """主入口：分类、门控、更新并触发情绪级联。"""
        try:
            # 1. 确保群组当前有情绪状态
            mood = await self._ensure_mood(group_id)

            # 2. 分类交互类型
            itype = await self._classify(message, bot_response, mood)

            # 3. 检查情绪门控
            rule = INTERACTION_RULES.get(itype)
            gate_ok, gate_reason = self._check_mood_gate(itype, mood, rule)
            if not gate_ok:
                return {
                    "success": True,
                    "interaction_type": itype.value,
                    "affection_score": None,
                    "affection_level": None,
                    "affection_delta": 0,
                    "mood_type": mood.mood_type.value if mood else None,
                    "mood_intensity": mood.intensity if mood else None,
                    "mood_description": mood.description if mood else None,
                    "gated": True,
                    "gate_reason": gate_reason,
                }

            # 4. 计算好感度变化量
            delta = self._calculate_delta(itype, mood, rule)
            if delta == 0:
                return self._build_result(itype, mood, 0)

            # 5. 持久化写入
            record = await self._store.upsert_affection(
                group_id,
                user_id,
                delta,
                max_score=self._max_affection,
                min_score=self._min_affection,
            )
            new_score = record["affection_score"]

            # 6. 若总量超限则执行重分配
            if delta > 0:
                await self._maybe_redistribute(group_id, user_id)

            # 7. 触发情绪级联
            if rule is not None:
                await self._apply_mood_cascade(group_id, itype, rule, mood)

            return {
                "success": True,
                "interaction_type": itype.value,
                "affection_score": new_score,
                "affection_level": AffectionLevel.name_for(new_score),
                "affection_delta": delta,
                "mood_type": mood.mood_type.value if mood else None,
                "mood_intensity": mood.intensity if mood else None,
                "mood_description": mood.description if mood else None,
                "gated": False,
            }

        except Exception:
            logger.exception(
                f"[好感度管理] process_interaction 失败: {group_id}/{user_id}"
            )
            return {
                "success": False,
                "interaction_type": "unknown",
                "affection_score": None,
                "affection_level": None,
                "affection_delta": 0,
                "mood_type": None,
                "mood_intensity": None,
                "mood_description": None,
                "error": "内部错误",
            }

    async def get_mood(self, group_id: str) -> BotMood:
        """返回指定群组当前情绪，必要时从存储层加载。"""
        return await self._ensure_mood(group_id)

    async def set_mood(
        self,
        group_id: Any,
        mood_type: Any,
        intensity: Any = 0.5,
        duration_hours: Any = 4.0,
        description: Any = None,
    ) -> BotMood:
        """显式设置某个群组的 Bot 情绪。"""
        normalized_group_id = self._normalize_identity(group_id, "group_id")
        errors: dict[str, str] = {}
        if not isinstance(mood_type, MoodType):
            errors["mood_type"] = "不支持的情绪类型"
        normalized_intensity = self._normalize_finite_float(
            intensity, "intensity", errors
        )
        normalized_duration = self._normalize_finite_float(
            duration_hours, "duration_hours", errors
        )
        if description is not None and not isinstance(description, str):
            errors["description"] = "必须为字符串"
        if errors:
            raise EntityValidationError(errors)

        resolved_description = (
            description.strip()
            if isinstance(description, str) and description.strip()
            else _random_mood_description(mood_type)
        )
        resolved_intensity = max(0.1, min(1.0, normalized_intensity))
        resolved_duration = max(0.25, min(168.0, normalized_duration))
        async with self._mood_lock:
            return await self._persist_mood_locked(
                normalized_group_id,
                mood_type,
                resolved_intensity,
                resolved_description,
                resolved_duration,
            )

    async def reset_mood(self, group_id: Any) -> BotMood:
        """将群组情绪重置为默认平静状态，并保留一条新的历史记录。"""
        return await self.set_mood(
            group_id,
            self.DEFAULT_MOOD,
            self.DEFAULT_INTENSITY,
        )

    async def get_mood_history(
        self,
        group_id: Any,
        limit: Any = 20,
        sort: SortQuery = SortQuery("start_time", "desc"),
    ) -> list[BotMood]:
        """返回按最新优先排序的已持久化情绪历史。"""
        normalized_group_id = self._normalize_identity(group_id, "group_id")
        normalized_limit = self._normalize_pagination(limit, "limit", minimum=1)
        records = await self._store.get_mood_history(
            normalized_group_id,
            limit=normalized_limit,
            sort=sort,
        )
        return [
            mood
            for record in records
            if (mood := self._mood_from_record(record)) is not None
        ]

    @staticmethod
    def revision_for_affection(value: UserAffection) -> str:
        """返回完整持久化好感度字段的规范修订版本。"""
        return compute_entity_revision(
            {
                "user_id": value.user_id,
                "group_id": value.group_id,
                "affection_score": value.affection_score,
                "interaction_count": value.interaction_count,
                "last_interaction": value.last_interaction,
            }
        )

    async def create_user_affection_manual(
        self, group_id: Any, user_id: Any, score: Any
    ) -> UserAffection:
        """创建管理员好感度记录，初始互动字段保持为零。"""
        normalized_group_id = self._normalize_identity(group_id, "group_id")
        normalized_user_id = self._normalize_identity(user_id, "user_id")
        normalized_score = self._normalize_score(score)
        record = await self._store.create_affection_strict(
            normalized_group_id, normalized_user_id, normalized_score
        )
        return self._affection_from_record(record)

    async def update_user_affection_manual(
        self,
        group_id: Any,
        user_id: Any,
        score: Any,
        *,
        expected_revision: Any,
    ) -> UserAffection:
        """按修订版本只更新管理员可写的好感度分数。"""
        normalized_group_id = self._normalize_identity(group_id, "group_id")
        normalized_user_id = self._normalize_identity(user_id, "user_id")
        normalized_score = self._normalize_score(score)
        normalized_revision = self._normalize_revision(expected_revision)
        record = await self._store.update_affection_if_revision(
            normalized_group_id,
            normalized_user_id,
            normalized_score,
            expected_revision=normalized_revision,
        )
        return self._affection_from_record(record)

    async def delete_user_affection_manual(
        self, group_id: Any, user_id: Any, *, expected_revision: Any
    ) -> bool:
        """按修订版本删除管理员好感度记录。"""
        normalized_group_id = self._normalize_identity(group_id, "group_id")
        normalized_user_id = self._normalize_identity(user_id, "user_id")
        normalized_revision = self._normalize_revision(expected_revision)
        return await self._store.delete_affection_if_revision(
            normalized_group_id,
            normalized_user_id,
            expected_revision=normalized_revision,
        )

    async def list_user_affections(
        self,
        group_id: Any,
        limit: Any = 50,
        offset: Any = 0,
        sort: SortQuery = SortQuery("affection_score", "desc"),
    ) -> tuple[list[UserAffection], int]:
        """分页列出群组用户好感度，使用稳定的存储排序。"""
        normalized_group_id = self._normalize_identity(group_id, "group_id")
        normalized_limit = self._normalize_pagination(limit, "limit", minimum=1)
        normalized_offset = self._normalize_pagination(offset, "offset", minimum=0)
        records, total = await self._store.list_affections(
            normalized_group_id,
            normalized_limit,
            normalized_offset,
            sort=sort,
        )
        return [self._affection_from_record(record) for record in records], total

    async def get_group_affection_status(self, group_id: str) -> dict[str, Any]:
        """获取群组级别的好感度与情绪概览。"""
        top = await self._store.get_top_users(group_id, limit=5)
        total = await self._store.get_total_affection(group_id)
        count = await self._store.get_user_count(group_id)
        mood = await self._ensure_mood(group_id)

        return {
            "total_affection": total,
            "max_total_affection": self._max_total_affection,
            "user_count": count,
            "top_users": top,
            "current_mood": {
                "type": mood.mood_type.value,
                "intensity": mood.intensity,
                "description": mood.description,
                "duration_hours": mood.duration_hours,
                "start_time": mood.start_time,
                "is_active": mood.is_active(),
            },
        }

    async def get_user_affection(
        self, group_id: str, user_id: str
    ) -> UserAffection | None:
        """获取单个用户的好感度记录。"""
        record = await self._store.get_affection(group_id, user_id)
        if record is None:
            return None
        return self._affection_from_record(record)

    @staticmethod
    def _affection_from_record(record: dict[str, Any]) -> UserAffection:
        return UserAffection(
            user_id=record["user_id"],
            group_id=record["group_id"],
            affection_score=record["affection_score"],
            interaction_count=record["interaction_count"],
            last_interaction=record["last_interaction"],
        )

    @staticmethod
    def _mood_from_record(record: dict[str, Any]) -> BotMood | None:
        """在唯一的持久化边界验证 mood 行；损坏记录安全跳过。"""
        try:
            mood_type = MoodType(record["mood_type"])
            description = record["description"]
            values = (
                record["intensity"],
                record["start_time"],
                record["duration_hours"],
            )
            if not isinstance(description, str):
                raise TypeError("description")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in values
            ):
                raise ValueError("numeric")
            return BotMood(
                mood_type=mood_type,
                intensity=float(record["intensity"]),
                description=description,
                start_time=float(record["start_time"]),
                duration_hours=float(record["duration_hours"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "[好感度管理] 跳过格式异常的情绪记录: %s", type(exc).__name__
            )
            return None

    @staticmethod
    def _normalize_identity(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise EntityValidationError({field: "必须为字符串"})
        normalized = value.strip()
        if not normalized:
            raise EntityValidationError({field: "不能为空"})
        if len(normalized) > 128:
            raise EntityValidationError({field: "文本过长"})
        return normalized

    def _normalize_score(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise EntityValidationError({"score": "必须为整数"})
        if not self._min_affection <= value <= self._max_affection:
            raise EntityValidationError(
                {
                    "score": "必须在 "
                    + str(self._min_affection)
                    + " 到 "
                    + str(self._max_affection)
                    + " 之间"
                }
            )
        return value

    @staticmethod
    def _normalize_revision(value: Any) -> str:
        if not isinstance(value, str):
            raise EntityValidationError({"expected_revision": "必须为字符串"})
        normalized = value.strip()
        if not normalized:
            raise EntityValidationError({"expected_revision": "不能为空"})
        if len(normalized) > 256:
            raise EntityValidationError({"expected_revision": "文本过长"})
        return normalized

    @staticmethod
    def _normalize_finite_float(
        value: Any, field: str, errors: dict[str, str]
    ) -> float:
        if isinstance(value, bool):
            errors[field] = "必须为数字"
            return 0.0
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            errors[field] = "必须为数字"
            return 0.0
        if not math.isfinite(normalized):
            errors[field] = "必须为有限数字"
        return normalized

    @staticmethod
    def _normalize_pagination(value: Any, field: str, *, minimum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise EntityValidationError({field: "必须为整数"})
        maximum = 100 if field == "limit" else 100000
        if not minimum <= value <= maximum:
            raise EntityValidationError(
                {field: "必须在 " + str(minimum) + " 到 " + str(maximum) + " 之间"}
            )
        return value

    # ---- 内部：情绪生命周期 ------------------------------------------------------

    async def _ensure_mood(self, group_id: str) -> BotMood:
        """获取或创建指定群组的有效情绪。"""
        cached = self._mood_cache.get(group_id)
        if cached and cached.is_active():
            return cached

        async with self._mood_lock:
            cached = self._mood_cache.get(group_id)
            if cached and cached.is_active():
                return cached

            mood = await self._load_active_mood(group_id)
            if mood is not None:
                self._mood_cache[group_id] = mood
                return mood

            return await self._persist_mood_locked(
                group_id,
                self.DEFAULT_MOOD,
                self.DEFAULT_INTENSITY,
                _random_mood_description(self.DEFAULT_MOOD),
                4.0,
            )

    async def _load_active_mood(self, group_id: str) -> BotMood | None:
        """读取首条有效活动情绪；若首条损坏则继续扫描较旧记录。"""
        record = await self._store.get_active_mood(group_id)
        mood = self._mood_from_record(record) if record else None
        if mood and mood.is_active():
            return mood
        if record is None:
            return None
        for candidate in await self._store.get_active_moods(group_id):
            mood = self._mood_from_record(candidate)
            if mood and mood.is_active():
                return mood
        return None

    async def _persist_mood_locked(
        self,
        group_id: str,
        mood_type: MoodType,
        intensity: float,
        description: str,
        duration_hours: float,
    ) -> BotMood:
        """在持有 ``_mood_lock`` 时持久化，并使取消后的缓存与提交一致。"""
        mood = BotMood(
            mood_type=mood_type,
            intensity=intensity,
            description=description,
            start_time=time.time(),
            duration_hours=duration_hours,
        )
        save_task = asyncio.create_task(
            self._store.save_bot_mood(
                group_id,
                mood_type.value,
                mood.intensity,
                mood.description,
                mood.duration_hours,
            )
        )
        cancellation: asyncio.CancelledError | None = None
        try:
            await asyncio.shield(save_task)
        except asyncio.CancelledError as exc:
            cancellation = exc
            try:
                await self._await_mood_save_completion(save_task)
            except BaseException:
                raise cancellation

        self._mood_cache[group_id] = mood
        if cancellation is not None:
            raise cancellation
        return mood

    @staticmethod
    async def _await_mood_save_completion(save_task: asyncio.Task[Any]) -> Any:
        """即使调用方重复取消，也消费内部写任务的最终结果。"""
        while not save_task.done():
            try:
                await asyncio.shield(save_task)
            except asyncio.CancelledError:
                continue
        return save_task.result()

    # ---- 内部：交互分类 ------------------------------------------------------

    async def _classify(
        self, message: str, bot_response: str, mood: BotMood
    ) -> InteractionType:
        """优先使用 LLM 分类，失败时回退到关键字规则。"""
        # 先尝试 LLM 分类
        if self._llm is not None:
            try:
                prompt = _CLASSIFICATION_PROMPT.format(
                    message=message,
                    bot_response=bot_response or "(无)",
                    mood_context=f"机器人当前心情：{mood.description}",
                )
                raw = await self._llm.chat_completion(prompt, temperature=0.1)
                result = raw.strip().lower()
                try:
                    return InteractionType(result)
                except ValueError:
                    logger.warning(
                        f"[好感度管理] LLM 返回无效交互类型: {result}，回退到规则"
                    )
            except Exception:
                logger.warning("[好感度管理] LLM 分类失败，回退到规则")

        # 关键字回退
        kw_result = classify_by_keywords(message)
        if kw_result is not None:
            return kw_result

        # 最终默认值
        return InteractionType.CHAT

    # ---- 内部：情绪门控 ---------------------------------------------------------

    @staticmethod
    def _check_mood_gate(
        itype: InteractionType,
        mood: BotMood | None,
        rule: Any,
    ) -> tuple[bool, str]:
        """返回 ``(是否允许, 原因)``。

        门控失败表示该交互已被识别，但不应影响好感度计分。
        """
        if rule is None or mood is None:
            return True, ""
        requirements = getattr(rule, "mood_requirements", None)
        if not requirements:
            return True, ""
        if mood.mood_type in requirements:
            return True, ""
        return False, f"当前心情({mood.mood_type.value})不适合{itype.value}交互"

    # ---- 内部：变化量计算 ---------------------------------------------------

    def _calculate_delta(
        self,
        itype: InteractionType,
        mood: BotMood | None,
        rule: Any,
    ) -> int:
        """计算最终的好感度变化量。"""
        if rule is None:
            return 0

        base = rule.base_change
        if rule.mood_sensitive and mood is not None:
            modifier = mood.get_mood_modifier()
        else:
            modifier = 1.0

        return round(base * modifier)

    # ---- 内部：重分配 ------------------------------------------------------

    async def _maybe_redistribute(self, group_id: str, exclude_user: str) -> None:
        """当总好感度超过软上限时，削减高分用户的好感度。"""
        total = await self._store.get_total_affection(group_id)
        if total <= self._max_total_affection:
            return

        overhead = total - self._max_total_affection
        all_users = await self._store.get_all_affections(group_id)
        other_users = [
            u
            for u in all_users
            if u["user_id"] != exclude_user and u["affection_score"] > 0
        ]
        if not other_users:
            return

        other_users.sort(key=lambda u: u["affection_score"], reverse=True)

        # 按比例逐轮削减，直到超额部分被消除
        for _ in range(3):  # 最多执行 3 轮
            remaining_total = sum(u["affection_score"] for u in other_users)
            if overhead <= 0 or remaining_total <= 0:
                break
            ratio = min(1.0, overhead / remaining_total * self._decay_rate)
            for u in other_users:
                if overhead <= 0:
                    break
                cut = max(1, round(u["affection_score"] * ratio))
                cut = min(cut, u["affection_score"])
                if cut < 1:
                    continue
                new_score = u["affection_score"] - cut
                expected_revision = compute_entity_revision(
                    {
                        "user_id": u["user_id"],
                        "group_id": u["group_id"],
                        "affection_score": u["affection_score"],
                        "interaction_count": u["interaction_count"],
                        "last_interaction": u["last_interaction"],
                    }
                )
                if await self._store.redistribute_affection_if_revision(
                    group_id,
                    u["user_id"],
                    new_score,
                    expected_revision=expected_revision,
                ):
                    u["affection_score"] = new_score
                    overhead -= cut

    # ---- 内部：情绪级联 --------------------------------------------------------

    async def _apply_mood_cascade(
        self,
        group_id: str,
        itype: InteractionType,
        rule: Any,
        current_mood: BotMood,
    ) -> None:
        """根据交互结果更新机器人的情绪。"""
        mood_effect = getattr(rule, "mood_effect", 0.0)

        if getattr(rule, "negative_mood_trigger", False):
            await self._cascade_negative(group_id, itype, mood_effect)
        elif getattr(rule, "positive_mood_boost", False):
            await self._cascade_positive(group_id, itype, mood_effect)
        else:
            await self._cascade_adjust(group_id, current_mood, mood_effect)

    async def _cascade_negative(
        self, group_id: str, itype: InteractionType, effect: float
    ) -> None:
        """负面交互会触发强烈且即时的情绪覆盖，持续 2 小时。"""
        mapping: dict[InteractionType, tuple[MoodType, list[str]]] = {
            InteractionType.THREAT: (
                MoodType.ANXIOUS,
                [
                    "感到被威胁，心情变得紧张不安...",
                    "受到恐吓，现在有些害怕和担心。",
                    "被威胁让我感到很不安全。",
                ],
            ),
            InteractionType.ABUSE: (
                MoodType.ANGRY,
                [
                    "被恶意谩骂，现在心情很愤怒！",
                    "受到恶毒攻击，感到非常生气。",
                    "恶语相向让我感到愤怒和受伤。",
                ],
            ),
            InteractionType.INSULT: (
                MoodType.SAD,
                [
                    "被侮辱攻击，心情变得很低落...",
                    "受到攻击，感到伤心和失望。",
                    "被人侮辱让我感到很难过。",
                ],
            ),
            InteractionType.HARASSMENT: (
                MoodType.ANXIOUS,
                [
                    "被骚扰困扰，现在感到很不安。",
                    "持续的骚扰让我感到紧张。",
                    "这种行为让我感到不舒服。",
                ],
            ),
        }
        mood_type, descriptions = mapping.get(
            itype, (MoodType.SAD, ["心情有些低落..."])
        )
        intensity = min(0.9, abs(effect))
        description = random.choice(descriptions)
        await self.set_mood(
            group_id,
            mood_type,
            intensity,
            duration_hours=2,
            description=description,
        )
        logger.info(
            f"[好感度管理] 群 {group_id} 触发负面情绪: {mood_type.value} ({intensity:.2f})"
        )

    async def _cascade_positive(
        self, group_id: str, itype: InteractionType, effect: float
    ) -> None:
        """强烈的正向交互会触发持续 4 小时的情绪提升。"""
        if itype == InteractionType.GIFT:
            mood_type = MoodType.EXCITED
            pool = [
                "收到礼物，太兴奋了！",
                "有人送礼物给我，好开心好激动！",
                "这个礼物让我感到非常兴奋！",
            ]
        elif itype in (InteractionType.PRAISE, InteractionType.ENCOURAGE):
            mood_type = MoodType.HAPPY
            pool = [
                "被夸赞鼓励，心情变得很开心！",
                "收到赞美，感到特别高兴。",
                "这些鼓励的话让我心情大好！",
            ]
        else:
            mood_type = MoodType.HAPPY
            pool = [
                "感受到善意，心情变好了。",
                "这种关怀让我感到温暖。",
                "谢谢你的友好，我心情好多了。",
            ]

        intensity = min(0.8, effect)
        description = random.choice(pool)
        await self.set_mood(
            group_id,
            mood_type,
            intensity,
            duration_hours=4,
            description=description,
        )
        logger.info(
            f"[好感度管理] 群 {group_id} 触发积极情绪: {mood_type.value} ({intensity:.2f})"
        )

    async def _cascade_adjust(
        self, group_id: str, mood: BotMood, effect: float
    ) -> None:
        """对当前情绪强度进行轻微调整。"""
        if abs(effect) < 0.05:
            return
        new_intensity = max(0.1, min(0.9, mood.intensity + effect))
        if abs(new_intensity - mood.intensity) < 0.1:
            return
        await self.set_mood(
            group_id,
            mood.mood_type,
            new_intensity,
            duration_hours=1,
        )

    # ---- 内部：辅助方法 -------------------------------------------------------------

    def _build_result(
        self, itype: InteractionType, mood: BotMood, delta: int
    ) -> dict[str, Any]:
        return {
            "success": True,
            "interaction_type": itype.value,
            "affection_score": None,
            "affection_level": None,
            "affection_delta": delta,
            "mood_type": mood.mood_type.value if mood else None,
            "mood_intensity": mood.intensity if mood else None,
            "mood_description": mood.description if mood else None,
            "gated": False,
        }

    async def close(self) -> None:
        """清理资源。"""
        async with self._mood_lock:
            await self._store.close()


__all__ = ["AffectionManager", "LLMAdapter"]
