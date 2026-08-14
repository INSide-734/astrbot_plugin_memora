import time as _time

_MEMORY_TYPE_KEYS = ("memory_type", "atom_type")
_EPISODIC = "EPISODIC"
_FACTUAL = "FACTUAL"
_PREFERENCE = "PREFERENCE"
_RELATIONAL = "RELATIONAL"

_TIME_THRESHOLDS: list[tuple[float, str]] = [
    (86400, "刚才"),
    (604800, "前几天"),
    (2592000, "几周前"),
    (7776000, "一两个月前"),
    (15552000, "几个月前"),
    (31536000, "半年前"),
]

_PREFERENCE_MARKERS = ("喜欢", "讨厌", "爱", "怕", "不喜欢", "不爱", "喜欢喝", "喜欢吃")


class HumanLikeMemoryFormatter:
    def __init__(self, max_fragments: int = 5, max_fragment_length: int = 80):
        self.max_fragments = max_fragments
        self.max_fragment_length = max_fragment_length

    def format(self, memories: list[dict]) -> list[str]:
        if not memories:
            return ["没有特别的记忆浮现"]

        grouped: dict[str, list[dict]] = {
            _EPISODIC: [],
            _FACTUAL: [],
            _PREFERENCE: [],
            _RELATIONAL: [],
            "OTHER": [],
        }
        for m in memories:
            mtype = self._resolve_type(m)
            bucket = grouped.get(mtype, grouped["OTHER"])
            bucket.append(m)

        fragments: list[str] = []
        for mtype in (_EPISODIC, _FACTUAL, _PREFERENCE, _RELATIONAL, "OTHER"):
            for memory in grouped.get(mtype, []):
                text = self._format_one(memory, mtype)
                if text:
                    fragments.append(text)

        if not fragments:
            return ["没有特别的记忆浮现"]

        fragments = self._deduplicate(fragments)
        return fragments[: self.max_fragments]

    @staticmethod
    def _resolve_type(memory: dict) -> str:
        for key in _MEMORY_TYPE_KEYS:
            raw = memory.get(key)
            if isinstance(raw, str) and raw.strip():
                upper = raw.strip().upper()
                if upper in (_EPISODIC, _FACTUAL, _PREFERENCE, _RELATIONAL):
                    return upper
        return "OTHER"

    def _format_one(self, memory: dict, mtype: str) -> str:
        if mtype == _EPISODIC:
            return self._format_episodic(memory)
        if mtype == _FACTUAL:
            return self._format_factual(memory)
        if mtype == _PREFERENCE:
            return self._format_preference(memory)
        if mtype == _RELATIONAL:
            return self._format_relational(memory)
        return self._format_factual(memory)

    def _format_episodic(self, memory: dict) -> str:
        time_hint = self._extract_time_hint(memory)
        content = self._extract_content(memory)
        if not content:
            return ""
        if time_hint:
            return f"记得{time_hint} {content}"
        return f"想起 {content}"

    def _format_factual(self, memory: dict) -> str:
        content = self._extract_content(memory)
        if not content:
            return ""
        return f"ta{content}"

    def _format_preference(self, memory: dict) -> str:
        content = self._extract_content(memory)
        if not content:
            return ""
        has_marker = any(marker in content for marker in _PREFERENCE_MARKERS)
        if has_marker:
            return f"ta{content}"
        return f"ta喜欢{content}"

    def _format_relational(self, memory: dict) -> str:
        content = self._extract_content(memory)
        if not content:
            return ""
        return content

    def _extract_content(self, memory: dict) -> str:
        key_facts = memory.get("key_facts")
        if isinstance(key_facts, str) and key_facts.strip():
            return key_facts.strip()[: self.max_fragment_length]

        metadata = memory.get("metadata")
        if isinstance(metadata, dict):
            inner = metadata.get("key_facts")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()[: self.max_fragment_length]

        content = memory.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[: self.max_fragment_length]

        return ""

    def _extract_time_hint(self, memory: dict) -> str:
        ts = self._find_timestamp(memory)
        if ts is None:
            return ""

        delta = _time.time() - ts
        for threshold, hint in _TIME_THRESHOLDS:
            if delta < threshold:
                return hint

        years = int(delta / 86400) // 365
        if years == 1:
            return "去年"
        if years == 2:
            return "前年"
        return f"{years}年前"

    @staticmethod
    def _find_timestamp(memory: dict) -> float | None:
        for field in ("create_time", "timestamp"):
            val = memory.get(field)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue

        metadata = memory.get("metadata")
        if isinstance(metadata, dict):
            for field in ("create_time", "timestamp"):
                val = metadata.get(field)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue
        return None

    def _deduplicate(self, fragments: list[str]) -> list[str]:
        if len(fragments) <= 1:
            return fragments
        result: list[str] = [fragments[0]]
        for frag in fragments[1:]:
            if not self._is_overlapping(frag, result):
                result.append(frag)
        return result

    @staticmethod
    def _is_overlapping(candidate: str, existing: list[str]) -> bool:
        if len(candidate) <= 3:
            return False
        for ref in existing:
            shorter = candidate if len(candidate) <= len(ref) else ref
            longer = ref if shorter is candidate else candidate
            if len(shorter) < 4:
                continue
            best = 0
            for i in range(len(longer) - 3):
                end = min(i + len(shorter), len(longer))
                window = longer[i:end]
                matched = 0
                for j in range(len(window)):
                    if j < len(shorter) and window[j] == shorter[j]:
                        matched += 1
                    else:
                        break
                if matched > best:
                    best = matched
            if len(shorter) > 0 and best / len(shorter) > 0.5:
                return True
        return False
