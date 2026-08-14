from __future__ import annotations

import time
from datetime import datetime, timezone


def seasonal_similarity(
    event_timestamp: float,
    current_timestamp: float,
) -> float:
    event_dt = datetime.fromtimestamp(event_timestamp, tz=timezone.utc)
    current_dt = datetime.fromtimestamp(current_timestamp, tz=timezone.utc)

    event_doy = event_dt.timetuple().tm_yday
    current_doy = current_dt.timetuple().tm_yday

    days_diff = abs(event_doy - current_doy)
    days_diff = min(days_diff, 365 - days_diff)

    similarity = max(0.0, 1.0 - days_diff / 180.0)
    return similarity


def seasonal_boost(
    event_timestamp: float,
    current_timestamp: float | None = None,
) -> float:
    if current_timestamp is None:
        current_timestamp = time.time()
    sim = seasonal_similarity(event_timestamp, current_timestamp)
    return 1.0 + 0.15 * sim
