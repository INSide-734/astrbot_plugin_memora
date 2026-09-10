"""测试 version_check — AstrBot version detection utilities."""


class TestParseVersion:
    def test_parse_simple(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("4.24.2") == (4, 24, 2)

    def test_parse_with_v_prefix(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("v4.24.2") == (4, 24, 2)

    def test_parse_single_digit(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("1") == (1,)

    def test_parse_two_part(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("4.24") == (4, 24)

    def test_parse_empty_string(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("") == ()

    def test_parse_non_version(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("not-a-version") == ()

    def test_parse_extra_text(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("4.24.2-beta1") == (4, 24, 2)

    def test_parse_whitespace(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("  4.24.2  ") == (4, 24, 2)


class TestVersionLt:
    def test_current_lt_minimum(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("4.24.0", "4.24.2") is True

    def test_current_eq_minimum(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("4.24.2", "4.24.2") is False

    def test_current_gt_minimum(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("5.0.0", "4.24.2") is False

    def test_different_lengths(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("4.24", "4.24.2") is True
        assert _version_lt("4.24.2.1", "4.24.2") is False

    def test_invalid_versions(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("invalid", "4.24.2") is False
        assert _version_lt("4.24.2", "invalid") is False

    def test_both_invalid(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("bad", "also-bad") is False

    def test_major_version_diff(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("3.99.99", "4.0.0") is True
        assert _version_lt("5.0.0", "4.99.99") is False
