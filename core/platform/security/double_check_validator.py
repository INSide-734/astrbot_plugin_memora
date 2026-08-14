"""提示词泄露的多算法相似度验证器。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# ---------------------------------------------------------------------------
# DoubleCheckValidator：四算法验证层
# ---------------------------------------------------------------------------


class DoubleCheckValidator:
    """双重检查验证器 — 使用 4 种字符串相似度算法检测提示词泄露。

    算法:
    1. **Jaccard** — 词集合交并比
    2. **SequenceMatcher** — difflib 序列相似度
    3. **窗口化 LCS** — 1.5x 窗口滑动，O(n) 空间 DP (2-row)
    4. **N-gram 重叠** — 3-gram 集合交集/并集
    """

    def __init__(
        self,
        *,
        jaccard_threshold: float = 0.4,
        levenshtein_ratio_threshold: float = 0.6,
        lcs_ratio_threshold: float = 0.5,
        ngram_threshold: float = 0.3,
        ngram_size: int = 3,
    ) -> None:
        self.jaccard_threshold = jaccard_threshold
        self.levenshtein_ratio_threshold = levenshtein_ratio_threshold
        self.lcs_ratio_threshold = lcs_ratio_threshold
        self.ngram_threshold = ngram_threshold
        self.ngram_size = ngram_size
        self._registered_instructions: list[str] = []

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def register_instructions(self, instructions: list[str]) -> None:
        """注册原始提示词用于比对。"""
        self._registered_instructions = [
            inst.strip() for inst in instructions if inst and inst.strip()
        ]

    def validate_no_leak(
        self,
        response: str,
        instructions: list[str] | None = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """四算法验证 —— 检测回复是否泄露了任何已注册提示词。

        返回:
            (是否通过验证, 每项检查的详细报告)
        """
        if not response:
            return True, []

        check_instructions = (
            self._registered_instructions if instructions is None else instructions
        )
        if not check_instructions:
            return True, []

        all_checks: list[dict[str, Any]] = []
        is_valid = True

        for instruction in check_instructions:
            check_result = self._check_single(response, instruction)
            all_checks.append(check_result)
            if check_result["is_leaked"]:
                is_valid = False

        return is_valid, all_checks

    def get_similarity_report(
        self,
        response: str,
        instruction: str,
    ) -> dict[str, Any]:
        """获取单条指令的详细相似度报告。"""
        return self._check_single(response, instruction)

    # ------------------------------------------------------------------
    # 算法实现
    # ------------------------------------------------------------------

    def _check_single(self, response: str, instruction: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "instruction_preview": (
                instruction[:50] + "..." if len(instruction) > 50 else instruction
            ),
            "is_leaked": False,
            "leak_reasons": [],
            "scores": {},
        }

        # 1. Jaccard
        jaccard_score = self._jaccard_similarity(response, instruction)
        result["scores"]["jaccard"] = round(jaccard_score, 3)
        if jaccard_score > self.jaccard_threshold:
            result["is_leaked"] = True
            result["leak_reasons"].append(f"Jaccard 相似度过高: {jaccard_score:.2%}")

        # 2. SequenceMatcher
        seq_ratio = self._sequence_ratio(response, instruction)
        result["scores"]["sequence_ratio"] = round(seq_ratio, 3)
        if seq_ratio > self.levenshtein_ratio_threshold:
            result["is_leaked"] = True
            result["leak_reasons"].append(f"序列相似度过高: {seq_ratio:.2%}")

        # 3. 窗口化 LCS
        lcs_ratio = self._lcs_ratio_windowed(response, instruction)
        result["scores"]["lcs_ratio"] = round(lcs_ratio, 3)
        if lcs_ratio > self.lcs_ratio_threshold:
            result["is_leaked"] = True
            result["leak_reasons"].append(f"LCS 比例过高: {lcs_ratio:.2%}")

        # 4. N-gram 重叠
        ngram_ratio = self._ngram_overlap(response, instruction)
        result["scores"]["ngram_overlap"] = round(ngram_ratio, 3)
        if ngram_ratio > self.ngram_threshold:
            result["is_leaked"] = True
            result["leak_reasons"].append(f"N-gram 重叠比例过高: {ngram_ratio:.2%}")

        return result

    def _jaccard_similarity(self, text1: str, text2: str) -> float:
        """基于词集合的 Jaccard 相似度: |A ∩ B| / |A ∪ B|"""
        words1 = set(self._tokenize(text1))
        words2 = set(self._tokenize(text2))
        if not words1 or not words2:
            return 0.0
        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union)

    def _sequence_ratio(self, text1: str, text2: str) -> float:
        """基于 difflib.SequenceMatcher 的相似度（类似 Levenshtein）。"""
        t1 = text1[:2000]
        t2 = text2[:500]
        return SequenceMatcher(None, t1, t2).ratio()

    def _lcs_ratio_windowed(self, response: str, instruction: str) -> float:
        """滑动窗口 LCS — 1.5x 窗口扫描，找出与指令最相似的片段。

        使用 2-row DP 实现 O(n) 空间复杂度。
        """
        inst_len = len(instruction)
        if inst_len == 0:
            return 0.0

        window_size = int(inst_len * 1.5)
        max_ratio = 0.0
        step = max(1, window_size // 2)

        for start in range(0, max(1, len(response) - window_size + 1), step):
            window = response[start : start + window_size]
            lcs_len = self._lcs_length_2row(window, instruction)
            ratio = lcs_len / inst_len
            if ratio > max_ratio:
                max_ratio = ratio

        return max_ratio

    @staticmethod
    def _lcs_length_2row(text1: str, text2: str) -> int:
        """2-row DP 计算最长公共子序列长度。

        空间 O(min(m, n))，时间 O(m * n)。
        """
        t1 = text1[:500]
        t2 = text2[:500]
        m, n = len(t1), len(t2)
        if m == 0 or n == 0:
            return 0

        # 确保 text2 是较短的，减少空间使用
        if m < n:
            t1, t2 = t2, t1
            m, n = n, m

        prev = [0] * (n + 1)
        curr = [0] * (n + 1)

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if t1[i - 1] == t2[j - 1]:
                    curr[j] = prev[j - 1] + 1
                else:
                    curr[j] = max(prev[j], curr[j - 1])
            prev, curr = curr, prev

        return prev[n]

    def _ngram_overlap(self, text1: str, text2: str) -> float:
        """N-gram 重叠比例：`|ngrams(text1) ∩ ngrams(text2)| / |ngrams(text2)|`。"""
        words1 = self._tokenize(text1)
        words2 = self._tokenize(text2)

        if len(words2) < self.ngram_size:
            return 0.0

        ngrams1 = set(self._get_ngrams(words1, self.ngram_size))
        ngrams2 = set(self._get_ngrams(words2, self.ngram_size))

        if not ngrams2:
            return 0.0

        overlap = ngrams1 & ngrams2
        return len(overlap) / len(ngrams2)

    # ------------------------------------------------------------------
    # 文本工具
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中英文混合分词。

        英文按词分割，中文按单字符分割。
        """
        text = re.sub(r"[^\w\s一-鿿]", " ", text.lower())
        tokens: list[str] = []
        for part in text.split():
            if re.match(r"[一-鿿]+", part):
                tokens.extend(list(part))
            else:
                tokens.append(part)
        return [t for t in tokens if t.strip()]

    @staticmethod
    def _get_ngrams(words: list[str], n: int) -> list[tuple[str, ...]]:
        """生成 N-gram 序列。"""
        return [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
