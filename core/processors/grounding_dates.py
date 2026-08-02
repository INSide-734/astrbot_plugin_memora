"""为来源忠实性校验提供确定性的日期规范化证据。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.conversation_models import Message

_ISO_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})[-/.]"
    r"(?P<month>0?[1-9]|1[0-2])[-/.]"
    r"(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)"
)
_CHINESE_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})年"
    r"(?P<month>0?[1-9]|1[0-2])月"
    r"(?P<day>0?[1-9]|[12]\d|3[01])日?"
)
_MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|sept(?:ember)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DAY_MONTH_YEAR_RE = re.compile(
    rf"(?<!\d)(?P<day>0?[1-9]|[12]\d|3[01])"
    rf"(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_PATTERN})\.?,?\s+"
    rf"(?P<year>(?:19|20)\d{{2}})(?!\d)",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR_RE = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\.?\s+"
    rf"(?P<day>0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?,?\s+"
    rf"(?P<year>(?:19|20)\d{{2}})(?!\d)",
    re.IGNORECASE,
)
_ANCHOR_LABEL_RE = re.compile(
    r"observation\s+date|current\s+date|conversation\s+date|today\s+is|"
    r"观察日期|当前日期|对话日期",
    re.IGNORECASE,
)
_CONTEXTUAL_YEAR_RE = re.compile(
    r"(?:\bin\s+|\bsince\s+|\bduring\s+|\bfrom\s+)"
    r"(?P<english_year>(?:19|20)\d{2})\b|"
    r"(?P<chinese_year>(?:19|20)\d{2})年",
    re.IGNORECASE,
)
_YEARS_AGO_RE = re.compile(
    r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"\s+years?\s+ago\b",
    re.IGNORECASE,
)
_CHINESE_YEARS_AGO_RE = re.compile(r"(?P<count>[一二三四五六七八九十\d]+)年前")
_LAST_WEEKDAY_RE = re.compile(
    r"\blast\s+(?P<weekday>monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday)\b",
    re.IGNORECASE,
)

_MONTH_NUMBERS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_CHINESE_WEEKDAYS = {
    "上周一": 0,
    "上周二": 1,
    "上周三": 2,
    "上周四": 3,
    "上周五": 4,
    "上周六": 5,
    "上周日": 6,
    "上周天": 6,
}


def supported_claim_date_numbers(
    claim_text: str,
    source_text: str,
    referenced_messages: list[Message],
) -> set[str]:
    """返回候选中可由引用来源确定支持的日期数字组成部分。

    Args:
        claim_text: 候选摘要与事实合并后的声明。
        source_text: 已通过 offset 校验的引用正文。
        referenced_messages: 引用对应的原始消息，用于相对日期的时间戳后备。

    Returns:
        可从候选数字严格匹配中豁免的年、月、日字符串集合。只有完整
        日期相等或带日期语境的年份被来源支持时才会返回对应数字。
    """

    source_dates = _extract_absolute_dates(source_text)
    anchors = _extract_labeled_anchor_dates(source_text)
    if not anchors:
        anchors = set(source_dates)
    if not anchors:
        anchors = _message_anchor_dates(referenced_messages)

    supported_dates = set(source_dates)
    supported_years: set[int] = set()
    for anchor in anchors:
        derived_dates, derived_years = _derive_relative_dates(source_text, anchor)
        supported_dates.update(derived_dates)
        supported_years.update(derived_years)

    allowed_numbers: set[str] = set()
    for claim_date in _extract_absolute_dates(claim_text).intersection(supported_dates):
        allowed_numbers.update(
            {str(claim_date.year), str(claim_date.month), str(claim_date.day)}
        )
    for year in _extract_contextual_years(claim_text).intersection(supported_years):
        allowed_numbers.add(str(year))
    return allowed_numbers


def _extract_absolute_dates(text: str) -> set[date]:
    """从中英文常用表示中提取经过日历校验的绝对日期。"""

    dates: set[date] = set()
    for pattern in (_ISO_DATE_RE, _CHINESE_DATE_RE):
        for match in pattern.finditer(text):
            _add_valid_date(
                dates,
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
    for pattern in (_DAY_MONTH_YEAR_RE, _MONTH_DAY_YEAR_RE):
        for match in pattern.finditer(text):
            month = _MONTH_NUMBERS[match.group("month").casefold()[:3]]
            _add_valid_date(
                dates,
                int(match.group("year")),
                month,
                int(match.group("day")),
            )
    return dates


def _add_valid_date(target: set[date], year: int, month: int, day: int) -> None:
    """把合法日历日期加入集合，忽略 2 月 30 日等无效组合。"""

    try:
        target.add(date(year, month, day))
    except ValueError:
        return


def _extract_labeled_anchor_dates(text: str) -> set[date]:
    """优先提取观察日期等显式标签后的日期锚点。"""

    anchors: set[date] = set()
    for match in _ANCHOR_LABEL_RE.finditer(text):
        anchors.update(_extract_absolute_dates(text[match.end() : match.end() + 64]))
    return anchors


def _message_anchor_dates(messages: list[Message]) -> set[date]:
    """把有效消息时间戳转换为相对日期推导的本地日期锚点。"""

    anchors: set[date] = set()
    for message in messages:
        timestamp = getattr(message, "timestamp", None)
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            continue
        try:
            anchors.add(datetime.fromtimestamp(timestamp).date())
        except (OSError, OverflowError, ValueError):
            continue
    return anchors


def _derive_relative_dates(text: str, anchor: date) -> tuple[set[date], set[int]]:
    """按单个可靠锚点解析有限且无歧义的相对日期表达。"""

    normalized = text.casefold()
    dates: set[date] = set()
    years: set[int] = set()

    if "day before yesterday" in normalized or "前天" in text:
        dates.add(anchor - timedelta(days=2))
    elif "yesterday" in normalized or "昨天" in text:
        dates.add(anchor - timedelta(days=1))
    if "tomorrow" in normalized or "明天" in text:
        dates.add(anchor + timedelta(days=1))
    if "last year" in normalized or "去年" in text:
        years.add(anchor.year - 1)

    for match in _YEARS_AGO_RE.finditer(text):
        raw_count = match.group("count").casefold()
        count = int(raw_count) if raw_count.isdigit() else _WORD_NUMBERS[raw_count]
        years.add(anchor.year - count)
    for match in _CHINESE_YEARS_AGO_RE.finditer(text):
        raw_count = match.group("count")
        count = (
            int(raw_count) if raw_count.isdigit() else _CHINESE_NUMBERS.get(raw_count)
        )
        if count is not None:
            years.add(anchor.year - count)

    for match in _LAST_WEEKDAY_RE.finditer(text):
        dates.add(
            _previous_weekday(anchor, _WEEKDAYS[match.group("weekday").casefold()])
        )
    for marker, weekday in _CHINESE_WEEKDAYS.items():
        if marker in text:
            dates.add(_previous_weekday(anchor, weekday))
    return dates, years


def _previous_weekday(anchor: date, weekday: int) -> date:
    """返回锚点之前最近一次指定星期，锚点同日时回退七天。"""

    days_back = (anchor.weekday() - weekday) % 7 or 7
    return anchor - timedelta(days=days_back)


def _extract_contextual_years(text: str) -> set[int]:
    """只提取带明确年份语境的候选四位年份。"""

    years: set[int] = set()
    for match in _CONTEXTUAL_YEAR_RE.finditer(text):
        value = match.group("english_year") or match.group("chinese_year")
        if value:
            years.add(int(value))
    return years


__all__ = ["supported_claim_date_numbers"]
