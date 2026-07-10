"""
功能融合模块 — 检测伴侣插件存在，跳过重复处理，并提供逆向服务委托。

对等委托逻辑 (出站 — Memora 委托给伴侣插件)：
- 检测到 self_learning 激活 → 跳过本地 expression/jargon/persona 处理
- 检测到 GroupChatPlus 激活 → 跳过本地 reply/response 影响
- 双方都加载时，Memora 专注于长期记忆存储和召回

逆向委托逻辑 (入站 — 伴侣插件委托给 Memora)：
- self_learning 可通过 MEMORA_SERVICE_ALIASES 检测 Memora
- Memora 提供记忆召回 (recall_memory) 和知识检索 (search_knowledge) 服务
- 通过 setter 注入 MemoryEngine / KnowledgeManager 依赖

双重门控: config switch + plugin active check
"""

from __future__ import annotations

import inspect
from typing import Any, Iterable

from astrbot.api import logger


class FeatureDelegation:
    """检测外部伴侣插件并决定哪些本地功能应跳过。"""

    # self_learning 插件别名 (用于 star registry 匹配)
    SELF_LEARNING_ALIASES = (
        "astrbot_plugin_self_learning",
        "self-learning",
        "SelfLearning",
        "self_learning",
        "selflearning",
    )

    # GroupChatPlus 插件别名
    GROUP_CHAT_PLUS_ALIASES = (
        "astrbot_plugin_group_chat_plus",
        "Group Chat Plus",
        "ChatPlus",
        "group_chat_plus",
        "groupchatplus",
    )

    # Memora 服务别名 — 供 self_learning 等外部插件检测 Memora 是否可用
    MEMORA_SERVICE_ALIASES = (
        "astrbot_plugin_memora",
        "Memora",
        "memora",
        "memora_plugin",
    )

    def __init__(
        self,
        context: Any,
        memory_engine: Any = None,
        knowledge_manager: Any = None,
    ) -> None:
        """初始化功能融合检测器。

        参数：
            context: AstrBot Context 对象，用于查询已注册的插件。
            memory_engine: 可选的 MemoryEngine 引用，用于提供记忆召回服务。
            knowledge_manager: 可选的 KnowledgeManager 引用，用于提供知识检索服务。
        """
        self._context = context
        self._memory_engine = memory_engine
        self._knowledge_manager = knowledge_manager
        # 防抖：仅状态变更时输出日志
        self._last_status: dict[str, bool] | None = None

    # ------------------------------------------------------------------
    # 公开查询方法
    # ------------------------------------------------------------------

    def self_learning_plugin(self) -> Any | None:
        """返回活跃的 self_learning Star 实例，否则返回 None。

        返回：
            self_learning 插件的 Star 实例/元数据，或 None。
        """
        return self._find_active_star(self.SELF_LEARNING_ALIASES)

    def chatplus_plugin(self) -> Any | None:
        """返回活跃的 GroupChatPlus Star 实例，否则返回 None。

        返回：
            GroupChatPlus 插件的 Star 实例/元数据，或 None。
        """
        return self._find_active_star(self.GROUP_CHAT_PLUS_ALIASES)

    def should_delegate_jargon(self) -> bool:
        """self_learning 是否接管了 jargon (黑话/方言) 学习和注入。

        返回：
            True 表示 Memora 应跳过本地 jargon 处理。
        """
        return self.self_learning_plugin() is not None

    def should_delegate_expression(self) -> bool:
        """self_learning 是否接管了 expression (表达风格) 学习。

        返回：
            True 表示 Memora 应跳过本地 expression 处理。
        """
        return self.self_learning_plugin() is not None

    def should_delegate_affection(self) -> bool:
        """self_learning 是否接管了 affection (情感/亲密度) 追踪。

        返回：
            True 表示 Memora 应跳过本地 affection 处理。
        """
        return self.self_learning_plugin() is not None

    def should_skip_persona_processing(self) -> bool:
        """检测 self_learning 是否正在处理人格画像/风格更新。

        返回：
            True 表示应跳过本地的 persona/风格处理。
        """
        return self.self_learning_plugin() is not None

    def should_skip_style_extraction(self) -> bool:
        """检测 self_learning 是否已经负责风格模式提取。

        返回：
            True 表示应跳过本地的风格提取。
        """
        return self.self_learning_plugin() is not None

    def should_delegate_reply(self) -> bool:
        """GroupChatPlus 是否接管了回复决策/生成。

        返回：
            True 表示 Memora 应专注于记忆存储而非回复影响。
        """
        return self.chatplus_plugin() is not None

    # ------------------------------------------------------------------
    # 服务提供方法（逆向委托 — 伴侣插件委托给 Memora）
    # ------------------------------------------------------------------

    def set_memory_engine(self, engine: Any) -> None:
        """注入 MemoryEngine 引用，使能记忆召回服务。

        应在插件初始化完成后调用。若未注入，服务方法返回空结果。

        参数：
            engine: MemoryEngine 实例。
        """
        self._memory_engine = engine

    def set_knowledge_manager(self, manager: Any) -> None:
        """注入 KnowledgeManager 引用，使能知识检索服务。

        应在插件初始化完成后调用。若未注入，服务方法返回空结果。

        参数：
            manager: KnowledgeManager 实例。
        """
        self._knowledge_manager = manager

    def can_provide_memory_service(self) -> bool:
        """Memora 是否可以对外提供记忆召回服务。

        双重门控：
        1. MemoryEngine 已注入且可用
        2. self_learning 伴侣插件已激活（有意义的使用场景）

        返回：
            True 表示外部插件可调用 recall_memory()。
        """
        return (
            self._memory_engine is not None
            and self.self_learning_plugin() is not None
        )

    def can_provide_knowledge_service(self) -> bool:
        """Memora 是否可以对外提供知识检索服务。

        双重门控：
        1. KnowledgeManager 已注入且可用
        2. self_learning 伴侣插件已激活（有意义的使用场景）

        返回：
            True 表示外部插件可调用 search_knowledge()。
        """
        return (
            self._knowledge_manager is not None
            and self.self_learning_plugin() is not None
        )

    async def recall_memory(
        self,
        query: str,
        session_id: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """对外提供的记忆召回服务。

        封装 MemoryEngine 的召回接口，供 self_learning 等伴侣插件调用。
        若 MemoryEngine 未注入或未就绪，返回空列表。

        参数：
            query: 搜索查询文本。
            session_id: 可选的会话 ID，用于会话范围过滤。
            top_k: 返回结果数量上限。

        返回：
            记忆原子字典列表，包含 content, score, metadata 等字段。
        """
        if not self._memory_engine:
            return []
        try:
            raw = self._memory_engine.recall(
                query=query,
                session_id=session_id,
                top_k=top_k,
            )
            if inspect.isawaitable(raw):
                raw = await raw
            return raw if isinstance(raw, list) else []
        except Exception:
            logger.warning(
                "[功能融合] recall_memory 调用失败", exc_info=True
            )
            return []

    async def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """对外提供的知识检索服务。

        封装 KnowledgeManager 的搜索接口，供 self_learning 等伴侣插件调用。
        若 KnowledgeManager 未注入或未就绪，返回空列表。

        参数：
            query: 搜索查询文本。
            top_k: 返回结果数量上限。

        返回：
            知识条目字典列表，包含 content, source, score 等字段。
        """
        if not self._knowledge_manager:
            return []
        try:
            raw = self._knowledge_manager.search(
                query=query,
                top_k=top_k,
            )
            if inspect.isawaitable(raw):
                raw = await raw
            return raw if isinstance(raw, list) else []
        except Exception:
            logger.warning(
                "[功能融合] search_knowledge 调用失败", exc_info=True
            )
            return []

    # ------------------------------------------------------------------
    # 结构化状态查询（控制台展示）
    # ------------------------------------------------------------------

    def get_delegation_status(self) -> dict[str, Any]:
        """返回完整的委托状态快照，供控制台展示和日志使用。

        返回：
            结构化字典:
            {
                "self_learning_active": bool,
                "self_learning_label": str | None,
                "chatplus_active": bool,
                "chatplus_label": str | None,
                "delegated_jargon": bool,
                "delegated_expression": bool,
                "delegated_affection": bool,
                "delegated_reply": bool,
                "provided_memory_service": bool,
                "provided_knowledge_service": bool,
            }
        """
        sl_plugin = self.self_learning_plugin()
        cp_plugin = self.chatplus_plugin()
        return {
            "self_learning_active": sl_plugin is not None,
            "self_learning_label": self._star_label(sl_plugin),
            "chatplus_active": cp_plugin is not None,
            "chatplus_label": self._star_label(cp_plugin),
            "delegated_jargon": self.should_delegate_jargon(),
            "delegated_expression": self.should_delegate_expression(),
            "delegated_affection": self.should_delegate_affection(),
            "delegated_reply": self.should_delegate_reply(),
            "provided_memory_service": self.can_provide_memory_service(),
            "provided_knowledge_service": self.can_provide_knowledge_service(),
        }

    def get_provided_services_status(self) -> dict[str, Any]:
        """返回 Memora 对外提供的服务状态快照。

        供 self_learning 等伴侣插件查询 Memora 提供哪些后端能力。

        返回：
            结构化字典:
            {
                "memora_available": bool,
                "memora_aliases": list[str],
                "memory_service": bool,
                "knowledge_service": bool,
                "service_details": {
                    "memory_recall": str | None,
                    "knowledge_search": str | None,
                },
            }
        """
        memory_ready = self._memory_engine is not None
        knowledge_ready = self._knowledge_manager is not None
        return {
            "memora_available": True,
            "memora_aliases": list(self.MEMORA_SERVICE_ALIASES),
            "memory_service": self.can_provide_memory_service(),
            "knowledge_service": self.can_provide_knowledge_service(),
            "service_details": {
                "memory_recall": (
                    "可用 — recall_memory(query, session_id, top_k)"
                    if memory_ready
                    else "不可用 — MemoryEngine 未注入"
                ),
                "knowledge_search": (
                    "可用 — search_knowledge(query, top_k)"
                    if knowledge_ready
                    else "不可用 — KnowledgeManager 未注入"
                ),
            },
        }

    # ------------------------------------------------------------------
    # 防抖状态日志
    # ------------------------------------------------------------------

    def log_status(self) -> None:
        """记录委托状态，仅在状态变化时输出日志（防抖）。"""
        status = self.get_delegation_status()
        current = {
            "sl": status["self_learning_active"],
            "cp": status["chatplus_active"],
        }
        if current == self._last_status:
            return
        self._last_status = current

        if status["self_learning_active"]:
            logger.info(
                f"[功能融合] 检测到 self_learning ({status['self_learning_label']}) 活跃，"
                "跳过本地 expression / jargon / persona 处理。"
            )
        else:
            logger.info("[功能融合] 未检测到 self_learning，保留本地完整处理。")

        if status["chatplus_active"]:
            logger.info(
                f"[功能融合] 检测到 GroupChatPlus ({status['chatplus_label']}) 活跃，"
                "跳过本地 reply / response 影响，专注于长期记忆存储。"
            )
        else:
            logger.info("[功能融合] 未检测到 GroupChatPlus，保留本地回复影响能力。")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _find_active_star(self, aliases: Iterable[str]) -> Any | None:
        """在已注册的 Star 列表中按别名匹配目标插件。

        匹配策略（按优先级）：
        1. 尝试 context.get_registered_star(alias) — 新版 AstrBot API
        2. 遍历 context.get_all_stars() — 旧版兼容路径
        3. 匹配字段: name / display_name / root_dir_name / module_path
        4. 大小写不敏感
        5. 仅考虑 activated=True 且有有效 star_cls 的 Star

        参数：
            aliases: 插件别名元组。

        返回：
            匹配到的活跃 Star 实例；未找到则返回 None。
        """
        raw_aliases = [str(a).strip() for a in aliases if str(a or "").strip()]
        wanted = {alias.lower() for alias in raw_aliases}
        if not wanted or not self._context:
            return None

        # 路径 A: 尝试新 API（get_registered_star）
        getter = getattr(self._context, "get_registered_star", None)
        if callable(getter):
            for alias in raw_aliases:
                try:
                    star = getter(alias)
                except Exception:
                    star = None
                if self._is_active_star(star):
                    logger.debug(
                        f"[功能融合] 通过 get_registered_star('{alias}') 发现插件"
                    )
                    return star

        # 路径 B: 遍历所有已注册的 Star
        all_getter = getattr(self._context, "get_all_stars", None)
        if not callable(all_getter):
            return None

        try:
            stars = all_getter() or []
        except Exception:
            return None

        for star in stars:
            if not self._is_active_star(star):
                continue
            candidate_names = self._extract_candidate_names(star)
            if any(name.lower() in wanted for name in candidate_names):
                logger.debug("[功能融合] 通过 get_all_stars() 匹配发现插件")
                return star
        return None

    @staticmethod
    def _is_active_star(star: Any) -> bool:
        """判断 Star 元数据对应的插件是否已激活且可运行。

        参数：
            star: Star 元数据或实例对象。

        返回：
            True 表示插件已激活且 star_cls 可用。
        """
        if not star:
            return False
        if getattr(star, "activated", True) is False:
            return False
        return getattr(star, "star_cls", None) is not None

    @staticmethod
    def _extract_candidate_names(star_meta: Any) -> list[str]:
        """从 Star 元数据中提取所有可用于匹配的候选名称。

        参数：
            star_meta: Star 元数据对象。

        返回：
            候选名称列表（含 module_path 的各组成部分）。
        """
        names: list[str] = []
        for attr in ("name", "display_name", "root_dir_name"):
            value = getattr(star_meta, attr, None)
            if value and isinstance(value, str):
                names.append(value)

        module_path = getattr(star_meta, "module_path", None)
        if isinstance(module_path, str):
            names.append(module_path)
            # 也匹配 module_path 的各部件（如 "astrbot_plugin_self_learning"）
            parts = [part for part in module_path.split(".") if part]
            names.extend(parts)

        return names

    @staticmethod
    def _star_label(star: Any) -> str | None:
        """从 Star 实例/元数据提取人类可读标签。

        参数：
            star: Star 实例或元数据。

        返回：
            display_name > name > root_dir_name > module_path，或 None。
        """
        if not star:
            return None
        return (
            getattr(star, "display_name", None)
            or getattr(star, "name", None)
            or getattr(star, "root_dir_name", None)
            or getattr(star, "module_path", None)
        )
