"""identity feature 的纯模型、应用服务与目录端口契约测试。"""

from __future__ import annotations

from core.features.identity.application.service import (
    ProtocolIdentityService as FeatureProtocolIdentityService,
)
from core.features.identity.contracts import IdentityDirectoryPort
from core.features.identity.domain.models import (
    IdentityProtocolAdapter as FeatureIdentityProtocolAdapter,
)
from core.features.identity.domain.models import IdentityTrust as FeatureIdentityTrust
from core.features.identity.domain.models import NameFieldState as FeatureNameFieldState
from core.features.identity.domain.models import (
    ResolvedIdentity as FeatureResolvedIdentity,
)
from core.features.identity.infrastructure.store import ProtocolIdentityStore
from core.identity.models import (
    IdentityProtocolAdapter,
    IdentityTrust,
    NameFieldState,
    ResolvedIdentity,
)
from core.identity.service import ProtocolIdentityService


def test_legacy_identity_model_and_service_exports_keep_type_identity() -> None:
    """旧导入路径必须复用 feature 的同一类型与唯一服务实现。"""

    assert IdentityTrust is FeatureIdentityTrust
    assert NameFieldState is FeatureNameFieldState
    assert ResolvedIdentity is FeatureResolvedIdentity
    assert IdentityProtocolAdapter is FeatureIdentityProtocolAdapter
    assert ProtocolIdentityService is FeatureProtocolIdentityService


def test_protocol_identity_store_satisfies_identity_directory_port(tmp_path) -> None:
    """SQLite Store 必须满足应用层目录端口，供组合根安全注入。"""

    store = ProtocolIdentityStore(str(tmp_path / "identity.db"))

    assert isinstance(store, IdentityDirectoryPort)
