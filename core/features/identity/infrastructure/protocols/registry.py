"""内置协议身份解析器的固定 manifest。"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ...domain.models import IdentityProtocolAdapter
from .onebot11 import OneBot11IdentityAdapter
from .qq_official import QQOfficialIdentityAdapter

ProtocolParserFactory = Callable[[], IdentityProtocolAdapter]

_BUILTIN_PROTOCOL_PARSER_FACTORIES: tuple[ProtocolParserFactory, ...] = (
    OneBot11IdentityAdapter,
    QQOfficialIdentityAdapter,
)


def build_default_protocol_parsers(
    additional_parsers: Iterable[IdentityProtocolAdapter] = (),
) -> tuple[IdentityProtocolAdapter, ...]:
    """实例化内置解析器，并按声明顺序追加调用方解析器。

    参数:
        additional_parsers: 不修改内置 manifest 即可追加的协议解析器。

    返回:
        可直接交给 ``ProtocolIdentityResolver`` 的不可变解析器元组。
    """

    builtin_parsers = tuple(
        parser_factory() for parser_factory in _BUILTIN_PROTOCOL_PARSER_FACTORIES
    )
    return (*builtin_parsers, *tuple(additional_parsers))


__all__ = ["build_default_protocol_parsers"]
