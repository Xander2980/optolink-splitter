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


class MeasurementTracker:
    def __init__(self):
        self.values: dict[str, Measurement] = {}
        self.dirty: set[str] = set()
        self.last_report: dict[str, float] = {}
        self.active: set[str] = set()

    def configure(self, names) -> None:
        # Retain old names as invalid until the next flush, so removed sensors
        # do not remain online in the broker.
        self.active = set(names)
        # Bound retired entries if the poll list is repeatedly replaced while offline.
        retired = sorted(set(self.values) - self.active)
        for name in retired[max(128, len(self.active)):]:
            self.values.pop(name, None)
            self.dirty.discard(name)
            self.last_report.pop(name, None)
        for name in set(self.values) | self.active:
            self.values.setdefault(name, Measurement()).invalidate()
            self.dirty.add(name)
        self.last_report.clear()

    def observe(self, name: str, value, now: float, wall_time: float) -> None:
        entry = self.values.setdefault(name, Measurement())
        if not valid_measurement(value):
            self.invalidate(name)
            return
        was_valid = entry.valid
        entry.success(now, wall_time)
        if not was_valid or now - self.last_report.get(name, -1e12) >= 30:
            self.dirty.add(name)

    def invalidate(self, name: str) -> None:
        entry = self.values.setdefault(name, Measurement())
        if entry.invalidate() or name not in self.last_report:
            self.dirty.add(name)

    def invalidate_all(self) -> None:
        for name in self.values:
            self.invalidate(name)

    def expire(self, now: float, deadlines: dict[str, Optional[float]]) -> None:
        for name, entry in self.values.items():
            if entry.expire(now, deadlines.get(name)):
                self.dirty.add(name)

    def republish(self) -> None:
        self.dirty.update(self.values)

    def mark_sent(self, name: str, now: float) -> None:
        self.dirty.discard(name)
        if name in self.active:
            self.last_report[name] = now
        else:
            self.values.pop(name, None)
            self.last_report.pop(name, None)
