"""黑话三步 LLM 推断引擎。

核心算法：通过 LLM 三步推断判断一个候选词是否为群内黑话。

步骤 1：上下文推断
  发送词条与上下文到 LLM，让其基于上下文推断含义。
  如果 LLM 返回 no_info: true，则视为信息不足，放弃本次推断。

步骤 2：词面推断
  仅发送词条本身（不带上下文）到 LLM，让其只基于词面推断含义。

步骤 3：对比判定
  将两次推断结果交给 LLM 比较。
  - 如果结果相似，则不是黑话，词面意思已经足够清楚。
  - 如果结果不同，则可能是黑话，上下文含义不同于词面含义。

渐进阈值触发：每达到 [3, 6, 10, 20, 40, 60, 100] 之一时触发推断，
当 count >= 100 时标记 is_complete = True。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from astrbot.api import logger

from .jargon_store import JargonStore
from .models import JargonCandidate, JargonMeaning
from .statistical_filter import JargonStatisticalFilter

# ---------------------------------------------------------------------------
# 渐进推断阈值
# ---------------------------------------------------------------------------

INFERENCE_THRESHOLDS: list[int] = [3, 6, 10, 20, 40, 60, 100]


# ---------------------------------------------------------------------------
# 三步推断 Prompt 模板
# ---------------------------------------------------------------------------

_PROMPT_STEP1_CONTEXT = """**候选词**
{term}

**候选词出现的上下文**
{context_text}

请根据以上候选词和上下文，推断这个候选词的含义。

- 如果这是一个黑话、俚语或网络用语，请在上下文中推断其含义
- 如果含义明确（常规词汇），也请说明
- 如果上下文信息不足，无法推断含义，请设置 no_info 为 true

以 JSON 格式输出：
{{
  "meaning": "详细含义说明（包含使用场景、来源、具体解释等）",
  "no_info": false
}}

注意：如果信息不足无法推断，请设置 "no_info": true，此时 meaning 可以为空字符串"""

_PROMPT_STEP2_TERM_ONLY = """**候选词**
{term}

请仅根据这个词本身，推断其含义。

- 如果这看起来像一个黑话、俚语或网络用语，请推断其含义
- 如果含义明确（常规词汇），也请说明

以 JSON 格式输出：
{{
  "meaning": "详细含义说明（包含使用场景、来源、具体解释等）"
}}"""

_PROMPT_STEP3_COMPARE = """**推断结果 1（基于上下文）**
{inference1}

**推断结果 2（仅基于词条）**
{inference2}

请比较这两个推断结果，判断它们的含义是否相同或类似。

- 如果两个推断结果的含义相同或类似 → 说明这个词条不是黑话（词面意思已经清楚，无需上下文）
- 如果两个推断结果有明显差异 → 说明这个词条可能是黑话（在上下文中有不同于词面的特殊含义）

以 JSON 格式输出：
{{
  "is_similar": true,
  "reason": "判断理由（用中文简述为什么两个推断相同或不同）"
}}"""


# ---------------------------------------------------------------------------
# JSON 提取辅助函数
# ---------------------------------------------------------------------------


def _safe_parse_json(text: str) -> dict[str, Any] | None:
    """从 LLM 响应中安全提取 JSON 对象。

    处理各种常见的 LLM 输出格式问题：
    - ```json ... ``` 代码块包裹
    - 嵌套 JSON 结构
    - 前导/尾随非 JSON 文本
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # 去除 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[:-3]

    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试提取最外层 {...}
    match = re.search(r"\{[^{}]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 尝试提取嵌套 {...}（匹配最外层大括号对）
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    pass
                start = -1

    return None


def _extract_meaning_str(data: dict[str, Any]) -> str:
    """从 LLM 返回的 JSON 中提取 meaning 字段为字符串。"""
    raw = data.get("meaning", "")
    if raw is None:
        return ""
    if isinstance(raw, dict) or isinstance(raw, list):
        return json.dumps(raw, ensure_ascii=False)
    return str(raw).strip()


# ---------------------------------------------------------------------------
# 黑话挖掘器
# ---------------------------------------------------------------------------


class JargonMiner:
    """由 LLM 驱动的黑话含义推断引擎。

    提供三步推断法：
    1. 基于上下文推断
    2. 仅基于词面推断
    3. 对比两个结果判定是否为黑话

    使用渐进阈值触发机制（INFERENCE_THRESHOLDS），
    异步非阻塞推断（asyncio.create_task）。

    用法示例::

        miner = JargonMiner(llm_client, stats_filter, store)
        results = await miner.run_once(group_id)
    """

    def __init__(
        self,
        llm_client: Any,
        statistical_filter: JargonStatisticalFilter,
        store: JargonStore,
        inference_timeout: float | None = 120.0,
    ) -> None:
        """初始化黑话挖掘器。

        参数:
            llm_client: LLM 调用客户端。需支持 ``text_chat(prompt, system_prompt)``
                或 ``generate_response(prompt, temperature)`` 方法。
            statistical_filter: 统计预过滤器实例。
            store: 黑话持久化存储实例。
            inference_timeout: 单个候选推断任务的超时时间（秒）。传入
                ``None`` 或非正数表示不启用超时。
        """
        self._llm = llm_client
        self._stats = statistical_filter
        self._store = store
        self._llm_available: bool | None = None  # 延迟检测
        self._inference_timeout = (
            float(inference_timeout)
            if inference_timeout is not None and inference_timeout > 0
            else None
        )
        self._inflight_candidates: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def run_once(self, group_id: str, limit: int = 5) -> list[JargonMeaning]:
        """执行一轮黑话推断。

        1. 从统计过滤器获取候选词（get_candidates）
        2. 取前 N 个未完成且达到下一渐进阈值的候选
        3. 对每个候选异步执行三步推断
        4. 更新持久化存储

        参数:
            group_id: 群组 ID。
            limit: 每轮最多推断的候选词数量。

        返回:
            本轮产生或更新的 JargonMeaning 列表。
        """
        if not await self._check_llm_available():
            logger.warning("[黑话挖掘器] LLM 不可用，跳过本轮推断")
            return []

        candidates = self._stats.get_candidates(group_id, limit=limit * 3)
        if not candidates:
            logger.debug(f"[黑话挖掘器] 群 {group_id} 无候选词")
            return []

        # 过滤：只处理未完成且跨过下一渐进阈值的候选
        eligible: list[JargonCandidate] = []
        for cand in candidates:
            if len(eligible) >= limit:
                break
            existing = await self._store.get_by_term(cand.term, cand.group_id)
            last_inference_count = (
                existing.last_inference_count if existing is not None else 0
            )
            if (existing is None or not existing.is_complete) and self._should_infer(
                cand, last_inference_count
            ):
                eligible.append(cand)

        if not eligible:
            logger.debug(f"[黑话挖掘器] 群 {group_id} 无需推断的候选")
            return []

        logger.info(
            f"[黑话挖掘器] 群 {group_id}: {len(eligible)}/{len(candidates)} 候选待推断"
        )

        # `run_once` 负责这些短生命周期任务，并始终会等待或取消它们。
        results: list[JargonMeaning] = []
        tasks = [
            asyncio.create_task(
                self._infer_and_store_with_timeout(cand),
                name=f"jargon-infer:{cand.group_id}:{cand.term}",
            )
            for cand in eligible
        ]

        try:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        for cand, task_result in zip(eligible, task_results, strict=False):
            if isinstance(task_result, Exception):
                logger.error(
                    f"[黑话挖掘器] 推断任务异常：候选词={cand.term}，"
                    f"群组={cand.group_id}，错误={task_result}"
                )
                continue
            if task_result is not None:
                results.append(task_result)

        return results

    async def infer_meaning(self, candidate: JargonCandidate) -> JargonMeaning | None:
        """对单个候选词执行三步推断。

        这是核心推断逻辑，可直接调用或通过 run_once 批量调用。

        参数:
            candidate: 统计过滤器产出的候选词。

        返回:
            JargonMeaning 实例，推断失败时返回 None。
        """
        if not await self._check_llm_available():
            return None

        term = candidate.term
        context_examples = candidate.context_examples

        try:
            # ---- 步骤 1：基于上下文推断 ----
            context_text = "\n---\n".join(context_examples) if context_examples else ""
            prompt1 = _PROMPT_STEP1_CONTEXT.format(term=term, context_text=context_text)
            response1 = await self._call_llm(prompt1)
            if response1 is None:
                logger.warning(f"[黑话挖掘器] 步骤 1 失败：候选词={term}")
                return None

            inference1 = _safe_parse_json(response1)
            if inference1 is None:
                logger.warning(f"[黑话挖掘器] 步骤 1 JSON 解析失败：候选词={term}")
                return None

            # 信息不足 → 放弃本轮（不创建 JargonMeaning）
            if inference1.get("no_info"):
                logger.info(f"[黑话挖掘器] 候选词={term} 信息不足，等待更多数据")
                return None

            meaning1 = _extract_meaning_str(inference1)
            if not meaning1:
                logger.info(f"[黑话挖掘器] 候选词={term} 的含义为空，等待更多数据")
                return None

            # ---- 步骤 2：仅基于词面推断 ----
            prompt2 = _PROMPT_STEP2_TERM_ONLY.format(term=term)
            response2 = await self._call_llm(prompt2)
            if response2 is None:
                logger.warning(f"[黑话挖掘器] 步骤 2 失败：候选词={term}")
                # 降级：仅使用步骤 1 的结果，保守标记为黑话
                return self._build_meaning(
                    candidate=candidate,
                    meaning=meaning1,
                    is_jargon=True,
                    confidence=0.3,
                )

            inference2 = _safe_parse_json(response2)
            if inference2 is None:
                logger.warning(f"[黑话挖掘器] 步骤 2 JSON 解析失败：候选词={term}")
                return self._build_meaning(
                    candidate=candidate,
                    meaning=meaning1,
                    is_jargon=True,
                    confidence=0.3,
                )

            meaning2 = _extract_meaning_str(inference2)

            # ---- 步骤 3：对比判定 ----
            prompt3 = _PROMPT_STEP3_COMPARE.format(
                inference1=json.dumps(inference1, ensure_ascii=False),
                inference2=json.dumps(inference2, ensure_ascii=False),
            )
            response3 = await self._call_llm(prompt3)
            is_jargon = True  # 默认策略：无法对比时保守判定为黑话
            confidence = 0.5

            if response3 is not None:
                comparison = _safe_parse_json(response3)
                if comparison is not None:
                    is_similar = comparison.get("is_similar", False)
                    is_jargon = not is_similar
                    # 置信度基于信号评分和推断一致性
                    confidence = self._calc_confidence(candidate, is_jargon)

            final_meaning = meaning1 if is_jargon else (meaning2 or meaning1)

            return self._build_meaning(
                candidate=candidate,
                meaning=final_meaning,
                is_jargon=is_jargon,
                confidence=confidence,
            )

        except Exception as exc:
            logger.error(f"[黑话挖掘器] 推断异常：候选词={term}，错误={exc}")
            return None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _check_llm_available(self) -> bool:
        """检测 LLM 是否可用（带缓存）。"""
        if self._llm_available is not None:
            return self._llm_available

        if self._llm is None:
            self._llm_available = False
            return False

        try:
            # 尝试获取当前提供器，或直接检测是否可用
            if hasattr(self._llm, "get_current_llm_provider"):
                provider = self._llm.get_current_llm_provider()
                self._llm_available = provider is not None
            elif hasattr(self._llm, "text_chat") or hasattr(
                self._llm, "generate_response"
            ):
                self._llm_available = True
            else:
                self._llm_available = False
        except Exception:
            self._llm_available = False

        return self._llm_available

    async def _call_llm(self, prompt: str, temperature: float = 0.3) -> str | None:
        """调用 LLM，兼容多种适配器接口。

        支持：
        - ``LLMClient.call_llm_with_retry(prompt, system_prompt)``
        - ``llm_adapter.text_chat(prompt, system_prompt)``
        - ``llm_adapter.generate_response(prompt, temperature)``
        """
        try:
            # 尝试使用本插件的 LLMClient 接口
            if hasattr(self._llm, "call_llm_with_retry"):
                return await self._llm.call_llm_with_retry(
                    prompt=prompt,
                    system_prompt="你是一个专业的语言学分析助手，擅长识别和分析网络用语、黑话和俚语。请严格按 JSON 格式输出。",
                )

            # 尝试 SelfLearning FrameworkLLMAdapter 接口
            if hasattr(self._llm, "generate_response"):
                return await self._llm.generate_response(
                    prompt, temperature=temperature
                )

            # 尝试直接调用 AstrBot 提供器接口
            if hasattr(self._llm, "text_chat"):
                response = await self._llm.text_chat(
                    prompt=prompt,
                    system_prompt="你是一个专业的语言学分析助手，擅长识别和分析网络用语、黑话和俚语。请严格按 JSON 格式输出。",
                )
                if hasattr(response, "completion_text"):
                    return response.completion_text
                return str(response)

            logger.warning("[黑话挖掘器] 未识别的 LLM 适配器接口")
            return None

        except Exception as exc:
            logger.error(f"[黑话挖掘器] LLM 调用失败：{exc}")
            return None

    def _should_infer(
        self, candidate: JargonCandidate, last_inference_count: int = 0
    ) -> bool:
        """判断候选词是否需要在此轮触发推断。

        逻辑：
        1. count < 第一个阈值 → 不触发
        2. 找到当前 count 已达到的最高阈值
        3. 仅当该阈值高于上次推断记录时触发

        参数:
            candidate: 候选词（其 frequency 字段用作 count）。
            last_inference_count: 已持久化的上次推断触发频次。

        返回:
            是否需要触发推断。
        """
        count = candidate.frequency
        if count < INFERENCE_THRESHOLDS[0]:
            return False

        # 只在越过新的阈值时触发，避免同一频次循环调用 LLM。
        for threshold in reversed(INFERENCE_THRESHOLDS):
            if count >= threshold:
                return last_inference_count < threshold
        return False

    @staticmethod
    def _calc_confidence(candidate: JargonCandidate, is_jargon: bool) -> float:
        """计算置信度。

        基于统计信号评分和推断结果加权：
        - 信号评分（0-1）占 60%
        - 推断一致性占 40%（is_jargon=True → 更高置信度对应更大语义偏差）

        参数:
            candidate: 候选词（含信号评分）。
            is_jargon: 三步推断是否判定为黑话。

        返回:
            置信度 [0, 1]。
        """
        signal_confidence = candidate.score  # 三信号综合评分
        inference_bonus = 0.4 if is_jargon else 0.2
        return min(1.0, signal_confidence * 0.6 + inference_bonus)

    def _build_meaning(
        self,
        candidate: JargonCandidate,
        meaning: str,
        is_jargon: bool,
        confidence: float,
    ) -> JargonMeaning:
        """从候选词和推断结果构建 JargonMeaning。

        参数:
            candidate: 统计过滤器候选词。
            meaning: 推断出的含义。
            is_jargon: 是否为真黑话。
            confidence: 置信度。

        返回:
            新构建的 JargonMeaning 实例。
        """
        now = time.time()
        return JargonMeaning(
            term=candidate.term,
            group_id=candidate.group_id,
            meaning=meaning,
            confidence=confidence,
            is_jargon=is_jargon,
            is_confirmed=False,
            is_global=False,
            is_complete=candidate.frequency >= INFERENCE_THRESHOLDS[-1],
            count=candidate.frequency,
            last_inference_count=candidate.frequency,
            context_examples=list(candidate.context_examples),
            created_at=candidate.first_seen or now,
            updated_at=now,
        )

    # ------------------------------------------------------------------
    # 内部：推断 + 存储
    # ------------------------------------------------------------------

    async def _infer_and_store(
        self, candidate: JargonCandidate
    ) -> JargonMeaning | None:
        """对单个候选词执行推断并持久化。

        该方法是 ``run_once`` 中异步任务的入口。

        参数:
            candidate: 统计过滤器候选词。

        返回:
            JargonMeaning 实例，推断失败或信息不足时返回 None。
        """
        candidate_key = (candidate.group_id, candidate.term)
        if candidate_key in self._inflight_candidates:
            logger.debug(f"[黑话挖掘器] 候选词={candidate.term} 正在推断，跳过重复任务")
            return None

        self._inflight_candidates.add(candidate_key)
        try:
            # 任务排队期间可能已有其他调用完成推断，需在这里再次确认。
            existing = await self._store.get_by_term(candidate.term, candidate.group_id)
            if existing and existing.is_complete:
                logger.debug(
                    f"[黑话挖掘器] 候选词={candidate.term} 已完成，跳过重新推断"
                )
                return existing
            if existing and not self._should_infer(
                candidate, existing.last_inference_count
            ):
                logger.debug(
                    f"[黑话挖掘器] 候选词={candidate.term} 未达到下一推断阈值，跳过"
                )
                return None

            meaning = await self.infer_meaning(candidate)
            if meaning is None:
                return None

            # 持久化
            await self._store.upsert(meaning)

            if meaning.is_jargon:
                logger.info(
                    f"[黑话挖掘器] 识别到黑话：{meaning.term} -> {meaning.meaning[:80]}"
                    f" (置信度={meaning.confidence:.2f})"
                )
            else:
                logger.info(
                    f"[黑话挖掘器] {meaning.term} 判定为非黑话"
                    f" (置信度={meaning.confidence:.2f})"
                )

            return meaning
        finally:
            self._inflight_candidates.discard(candidate_key)

    async def _infer_and_store_with_timeout(
        self, candidate: JargonCandidate
    ) -> JargonMeaning | None:
        """在配置的时限内推断并持久化单个候选词。

        参数:
            candidate: 需要推断的候选词。

        返回:
            持久化后的推断结果；超时、重复任务或无结果时返回 None。

        异常:
            asyncio.CancelledError: 调用方取消时继续向上传播。
        """
        if self._inference_timeout is None:
            return await self._infer_and_store(candidate)
        try:
            return await asyncio.wait_for(
                self._infer_and_store(candidate),
                timeout=self._inference_timeout,
            )
        except TimeoutError:
            logger.warning(
                f"[黑话挖掘器] 推断任务超时：候选词={candidate.term}，"
                f"群组={candidate.group_id}，超时={self._inference_timeout}s"
            )
            return None


__all__ = [
    "JargonMiner",
    "INFERENCE_THRESHOLDS",
    "_safe_parse_json",
    "_extract_meaning_str",
]
