"""AnomalyDetector 测试 — 7天滚动窗口 + 3-sigma 异常检测。"""

from __future__ import annotations

import math
import time

from core.features.memory.application.anomaly_detector import AnomalyDetector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _midnight_ts(days_ago: int = 0) -> int:
    """Return Unix timestamp for midnight N days ago."""
    now = int(time.time())
    today_midnight = now - (now % 86400)
    return today_midnight - days_ago * 86400


# ---------------------------------------------------------------------------
# record_daily_count tests
# ---------------------------------------------------------------------------


class TestRecordDailyCount:
    """Tests for recording daily memory creation counts."""

    def test_no_alert_with_fewer_than_3_days(self) -> None:
        """Anomaly detection should return None when window has < 3 data points."""
        detector = AnomalyDetector()
        day0 = _midnight_ts(0)

        result = detector.record_daily_count(day0, 10)
        assert result is None

    def test_no_alert_when_stable(self) -> None:
        """No alert when counts are within normal range."""
        detector = AnomalyDetector()
        base = _midnight_ts(0)
        results: list = []
        for i in range(7):
            day_ts = base - (6 - i) * 86400
            alert = detector.record_daily_count(day_ts, 100)
            if alert:
                results.append(alert)

        assert len(results) == 0

    def test_alert_on_spike(self) -> None:
        """Alert triggered when a count spikes beyond 3 sigma."""
        detector = AnomalyDetector(window_days=14)
        base = _midnight_ts(0)

        # Fill 13 days with tightly varying data (~100) so stdev is small
        values = [99, 101, 100, 99, 101, 100, 99, 101, 100, 99, 101, 100]
        for i, v in enumerate(values):
            day_ts = base - (len(values) - i) * 86400
            detector.record_daily_count(day_ts, v)

        # Spike on the 13th data point (day 0)
        alert = detector.record_daily_count(base, 500)
        assert alert is not None
        assert alert["direction"] == "spike"
        assert alert["z_score"] > detector._sigma_threshold
        assert "count" in alert
        assert "mean_7d" in alert

    def test_alert_on_drop(self) -> None:
        """Alert triggered when count drops significantly below mean."""
        detector = AnomalyDetector(window_days=14)
        base = _midnight_ts(0)
        values = [99, 101, 100, 99, 101, 100, 99, 101, 100, 99, 101, 100]
        for i, v in enumerate(values):
            day_ts = base - (len(values) - i) * 86400
            detector.record_daily_count(day_ts, v)

        alert = detector.record_daily_count(base, 5)
        assert alert is not None
        assert alert["direction"] == "drop"

    def test_low_variance_skipped(self) -> None:
        """When all values are nearly identical, a small change should not trigger."""
        detector = AnomalyDetector()
        base = _midnight_ts(0)

        for i in range(6):
            day_ts = base - (6 - i) * 86400
            detector.record_daily_count(day_ts, 0)

        # Going from all-zeroes to 1 with stdev < 0.5 should not alert
        alert = detector.record_daily_count(base, 1)
        assert alert is None

    def test_debounce_prevents_rapid_realerts(self) -> None:
        """Debounce prevents duplicate alerts within 5 minutes."""
        detector = AnomalyDetector(window_days=14)
        base = _midnight_ts(0)
        values = [99, 101, 100, 99, 101, 100, 99, 101, 100, 99, 101, 100]
        for i, v in enumerate(values):
            day_ts = base - (len(values) - i) * 86400
            detector.record_daily_count(day_ts, v)

        alert1 = detector.record_daily_count(base, 500)
        assert alert1 is not None

        # Same day, immediate second recording — should be debounced
        alert2 = detector.record_daily_count(base, 600)
        assert alert2 is None

    def test_negative_count_clamped_to_zero(self) -> None:
        """Negative counts are clamped to 0 before recording."""
        detector = AnomalyDetector()
        base = _midnight_ts(0)

        for i in range(5):
            day_ts = base - (5 - i) * 86400
            detector.record_daily_count(day_ts, 100)

        # -50 should be clamped to 0 internally
        alert = detector.record_daily_count(base, -50)
        # count=0 after clamping; stdev will be high → spike/drop depends
        # It should produce an alert (the value is an outlier vs mean ~100)
        assert alert is not None  # 0 is far from mean=100

    def test_window_slides_correctly(self) -> None:
        """Old entries fall out of the window after window_days."""
        detector = AnomalyDetector(window_days=3)
        base = _midnight_ts(0)

        # Add 3 old entries that should fall out
        for i in range(4, 7):
            day_ts = base - i * 86400
            detector.record_daily_count(day_ts, 50)

        # Add 2 recent entries at 100, then check — old 50s should be gone
        for i in range(2):
            day_ts = base - i * 86400
            detector.record_daily_count(day_ts, 100)

        stats = detector.stats
        assert stats["window_size"] >= 2
        assert stats["mean"] > 80  # 100*2 + some old ones dropped

    def test_record_batch_returns_all_alerts(self) -> None:
        """record_batch should collect alerts from multiple days."""
        detector = AnomalyDetector(window_days=14)
        base = _midnight_ts(0)
        values = [99, 101, 100, 99, 101, 100, 99, 101, 100, 99, 101, 100]
        for i, v in enumerate(values):
            day_ts = base - (14 - i) * 86400
            detector.record_daily_count(day_ts, v)

        # Now batch with one spike
        batch = [
            (base, 100),
            (base - 86400, 500),  # spike
        ]
        alerts = detector.record_batch(batch)
        assert len(alerts) >= 1
        assert any(a["direction"] == "spike" for a in alerts)


# ---------------------------------------------------------------------------
# stats property tests
# ---------------------------------------------------------------------------


class TestStats:
    """Tests for the stats property."""

    def test_stats_empty_detector(self) -> None:
        """Empty detector returns zeroed stats."""
        detector = AnomalyDetector()
        s = detector.stats
        assert s["window_size"] == 0
        assert s["mean"] == 0.0
        assert s["stdev"] == 0.0
        assert s["alerts"] == 0

    def test_stats_after_recording(self) -> None:
        """Stats reflect recorded data correctly."""
        detector = AnomalyDetector()
        base = _midnight_ts(0)

        for i in range(7):
            day_ts = base - (6 - i) * 86400
            detector.record_daily_count(day_ts, 100)

        s = detector.stats
        assert s["window_size"] == 7
        assert math.isclose(s["mean"], 100.0, abs_tol=1)
        assert s["stdev"] == 0.0  # all identical
        assert "latest_count" in s
        assert "sigma_threshold" in s

    def test_stats_includes_alert_count(self) -> None:
        """Alert count is tracked in stats."""
        detector = AnomalyDetector(window_days=14)
        base = _midnight_ts(0)
        values = [99, 101, 100, 99, 101, 100, 99, 101, 100, 99, 101, 100]
        for i, v in enumerate(values):
            day_ts = base - (len(values) - i) * 86400
            detector.record_daily_count(day_ts, v)

        detector.record_daily_count(base, 500)  # spike
        s = detector.stats
        assert s["alerts"] == 1


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


class TestPersistence:
    """Tests for save_state / load_state round-trip."""

    def test_save_and_load_round_trip(self, tmp_path) -> None:
        """Window state is preserved through save/load cycle."""

        data_dir = str(tmp_path / "anomaly_data")
        detector = AnomalyDetector(data_dir=data_dir)
        base = _midnight_ts(0)

        for i in range(7):
            day_ts = base - (6 - i) * 86400
            detector.record_daily_count(day_ts, 100)
        detector.record_daily_count(base, 500)  # spike → alert_count = 1

        detector.save_state()

        # Create fresh detector and load
        detector2 = AnomalyDetector(data_dir=data_dir)
        detector2.load_state()

        s1 = detector.stats
        s2 = detector2.stats
        assert s2["window_size"] == s1["window_size"]
        assert math.isclose(s2["mean"], s1["mean"], abs_tol=1)
        assert s2["alerts"] == s1["alerts"]

    def test_load_without_file_no_error(self, tmp_path) -> None:
        """Loading from nonexistent file should not raise."""
        data_dir = str(tmp_path / "nonexistent")
        detector = AnomalyDetector(data_dir=data_dir)
        detector.load_state()  # Should not raise
        assert detector.stats["window_size"] == 0

    def test_save_without_data_dir_no_error(self) -> None:
        """Save with empty data_dir is a no-op, not an error."""
        detector = AnomalyDetector(data_dir="")
        detector.record_daily_count(_midnight_ts(0), 100)
        detector.record_daily_count(_midnight_ts(1), 100)
        detector.record_daily_count(_midnight_ts(2), 100)
        detector.save_state()  # Should not raise
