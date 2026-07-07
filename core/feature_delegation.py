"""
功能融合模块 — 检测伴侣插件存在，跳过重复处理。

对等委托逻辑：
- 检测到 self_learning 激活 → 跳过本地 expression/jargon/persona 处理
- 检测到 GroupChatPlus 激活 → 跳过本地 reply/response 影响
- 双方都加载时，Memora 专注于长期记忆存储和召回

双重门控: config switch + plugin active check
"""

from __future__ import annotations

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

    def __init__(self, context: Any) -> None:
        """初始化功能融合检测器。

        参数：
            context: AstrBot Context 对象，用于查询已注册的插件。
        """
        self._context = context
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
