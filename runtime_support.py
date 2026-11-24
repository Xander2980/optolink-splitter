"""Dependency-free helpers for bounded retry and measurement freshness."""
import math
from dataclasses import dataclass
from typing import Optional


def reconnect_delay(initial: float, maximum: float, failures: int) -> float:
    return min(maximum, initial * 2 ** min(20, max(0, failures - 1)))


def freshness_deadline(interval: float, count: int, cycle: int, override: float) -> Optional[float]:
    if cycle <= 0 or interval < 0:
        return None
    if override > 0:
        return override
    return max(120.0, 3 * max(interval, count * 0.25) * cycle)


def valid_measurement(value) -> bool:
    if value is None or value == "(conversion failed)":
        return False
    return not isinstance(value, float) or math.isfinite(value)


@dataclass
class Measurement:
    updated: Optional[float] = None
    observed_at: Optional[float] = None
    valid: bool = False

    def success(self, now: float, wall_time: float) -> None:
        self.updated = now
        self.observed_at = wall_time
        self.valid = True

    def invalidate(self) -> bool:
        changed = self.valid
        self.valid = False
        return changed

    def expire(self, now: float, max_age: Optional[float]) -> bool:
        if self.valid and max_age is not None and self.updated is not None and now - self.updated >= max_age:
            return self.invalidate()
        return False
