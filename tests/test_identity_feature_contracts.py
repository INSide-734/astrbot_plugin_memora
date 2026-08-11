"""identity feature 的纯模型、应用服务与目录端口契约测试。"""

from __future__ import annotations

from core.features.identity import (
    IdentityProtocolAdapter,
    IdentityTrust,
    NameFieldState,
    OneBot11IdentityAdapter,
    ProtocolIdentityResolver,
    ProtocolIdentityService,
    QQOfficialIdentityAdapter,
    ResolvedIdentity,
    build_default_protocol_parsers,
)
from core.features.identity.application.service import (
    ProtocolIdentityService as OwnedProtocolIdentityService,
)
from core.features.identity.contracts import (
    IDENTITY_SCHEMA_VERSION as FeatureIdentitySchemaVersion,
)
from core.features.identity.contracts import (
    IdentityDirectoryPort,
)
from core.features.identity.domain.models import (
    IdentityProtocolAdapter as OwnedIdentityProtocolAdapter,
)
from core.features.identity.domain.models import IdentityTrust as OwnedIdentityTrust
from core.features.identity.domain.models import NameFieldState as OwnedNameFieldState
from core.features.identity.domain.models import (
    ResolvedIdentity as OwnedResolvedIdentity,
)
from core.features.identity.infrastructure.protocols import (
    OneBot11IdentityAdapter as OwnedOneBot11IdentityAdapter,
)
from core.features.identity.infrastructure.protocols import (
    ProtocolIdentityResolver as OwnedProtocolIdentityResolver,
)
from core.features.identity.infrastructure.protocols import (
    QQOfficialIdentityAdapter as OwnedQQOfficialIdentityAdapter,
)
from core.features.identity.infrastructure.store import ProtocolIdentityStore


def test_identity_package_exports_owned_implementations() -> None:
    """feature 包级入口必须导出各分层唯一实现。"""

    assert IdentityTrust is OwnedIdentityTrust
    assert NameFieldState is OwnedNameFieldState
    assert ResolvedIdentity is OwnedResolvedIdentity
    assert IdentityProtocolAdapter is OwnedIdentityProtocolAdapter
    assert ProtocolIdentityService is OwnedProtocolIdentityService
    assert OneBot11IdentityAdapter is OwnedOneBot11IdentityAdapter
    assert QQOfficialIdentityAdapter is OwnedQQOfficialIdentityAdapter
    assert ProtocolIdentityResolver is OwnedProtocolIdentityResolver
    assert FeatureIdentitySchemaVersion == "stable-identity-v1"


def test_protocol_identity_store_satisfies_identity_directory_port(tmp_path) -> None:
    """SQLite Store 必须满足应用层目录端口，供组合根安全注入。"""

    store = ProtocolIdentityStore(str(tmp_path / "identity.db"))

    assert isinstance(store, IdentityDirectoryPort)


def test_builtin_protocol_manifest_contains_supported_parsers() -> None:
    """内置 manifest 必须按固定顺序提供 OneBot 11 与 QQ 官方解析器。"""

    parsers = build_default_protocol_parsers()

    assert tuple(type(parser) for parser in parsers) == (
        OneBot11IdentityAdapter,
        QQOfficialIdentityAdapter,
    )
    assert OneBot11IdentityAdapter.__module__.startswith(
        "core.features.identity.infrastructure.protocols."
    )
    assert QQOfficialIdentityAdapter.__module__.startswith(
        "core.features.identity.infrastructure.protocols."
    )
