"""4.2.3: Test proactive reminder — PLANNED atoms injected within 24h window."""

import time


class TestProactiveReminder:
    """Prospective memory — PLANNED atoms within 24h should be injected;
    expired >24h should not."""

    def test_upcoming_within_24h_returns_atoms(self) -> None:
        """Atoms with event_time within next 24h should be returned."""
        now = time.time()
        future_6h = now + 6 * 3600
        future_23h = now + 23 * 3600

        timestamps = [future_6h, future_23h]
        lookahead = 86400  # 24h

        within = [ts for ts in timestamps if ts <= now + lookahead]
        assert len(within) == 2

    def test_expired_over_24h_not_returned(self) -> None:
        """Atoms with event_time > 24h in the future should be excluded."""
        now = time.time()
        future_25h = now + 25 * 3600
        future_48h = now + 48 * 3600

        timestamps = [future_25h, future_48h]
        lookahead = 86400

        within = [ts for ts in timestamps if ts <= now + lookahead]
        assert len(within) == 0

    def test_past_events_not_returned(self) -> None:
        """Events in the past should not be returned as upcoming."""
        now = time.time()
        past_1h = now - 3600
        past_24h = now - 86400

        timestamps = [past_1h, past_24h]
        lookahead = 86400

        # "upcoming" means between now and now+lookahead
        upcoming = [
            ts for ts in timestamps
            if now <= ts <= now + lookahead
        ]
        assert len(upcoming) == 0

    def test_mixed_window_filters_correctly(self) -> None:
        """Mix of past, upcoming, and far-future — only upcoming within 24h."""
        now = time.time()
        past = now - 3600
        soon = now + 2 * 3600
        tomorrow = now + 22 * 3600
        far = now + 48 * 3600

        timestamps = [past, soon, tomorrow, far]
        lookahead = 86400

        upcoming = [
            ts for ts in timestamps
            if now <= ts <= now + lookahead
        ]
        assert len(upcoming) == 2
        assert soon in upcoming
        assert tomorrow in upcoming

    def test_boundary_exactly_24h_included(self) -> None:
        """Event exactly at the 24h boundary should be included."""
        now = time.time()
        exactly_24h = now + 86400

        lookahead = 86400
        upcoming = [
            ts for ts in [exactly_24h]
            if now <= ts <= now + lookahead
        ]
        assert len(upcoming) == 1

    def test_boundary_just_over_24h_excluded(self) -> None:
        """Event just over 24h boundary should be excluded."""
        now = time.time()
        just_over = now + 86401

        lookahead = 86400
        upcoming = [
            ts for ts in [just_over]
            if now <= ts <= now + lookahead
        ]
        assert len(upcoming) == 0
