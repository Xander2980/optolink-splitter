"""Bounded system and heating diagnostics; all mutation occurs on the bus worker."""
import math
import time
try:
    import resource
except ImportError:
    resource = None
from pathlib import Path
from collections import Counter, deque
from datetime import datetime
from advanced_config import finite


def number(value):
    if isinstance(value, bool):
        return int(value)
    return value if finite(value) else None


def read_system():
    result = {}
    try:
        # Linux ru_maxrss is KiB; /proc gives current resident size.
        if resource is not None:
            result["ram_peak_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                result["ram_mib"] = round(int(line.split()[1]) / 1024, 2)
                break
    except (OSError, ValueError, IndexError):
        pass
    try:
        result["cpu_temperature_c"] = round(float(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        pass
    return result


class Heating:
    """Observation-based metrics; never infer burner activity across missing samples."""
    def __init__(self, max_gap, short_seconds):
        self.max_gap, self.short_seconds = max_gap, short_seconds
        self.previous = None
        self.transition = None
        self.starts = deque(maxlen=4096)
        self.runs = deque(maxlen=512)
        self.pauses = deque(maxlen=512)
        self.segments = deque(maxlen=7200)
        self.day = None
        self.starts_today = 0
        self.mod_sum = self.mod_seconds = 0.0
        self.kessel = None
        self.slope = None

    def invalidate(self):
        self.previous = self.transition = self.kessel = self.slope = None

    def observe(self, active, modulation, boiler, now, wall, *, burner_sample=True, boiler_sample=True):
        day = datetime.fromtimestamp(wall).date()
        if day != self.day:
            self.day, self.starts_today = day, 0
        if not burner_sample:
            pass
        elif active not in (0, 1):
            self.previous = self.transition = None
        else:
            if self.previous is not None:
                before, old_active, old_mod = self.previous
                dt = now - before
                if 0 < dt <= self.max_gap:
                    self.segments.append((before, now, old_active, old_mod))
                    if active != old_active:
                        if active:
                            self.starts.append(now)
                            self.starts_today += 1
                            if self.transition is not None:
                                self.pauses.append(now - self.transition)
                        elif self.transition is not None:
                            self.runs.append(now - self.transition)
                        self.transition = now
                else:
                    self.transition = None
            self.previous = now, active, modulation
        if not boiler_sample:
            return
        if boiler is not None:
            if self.kessel:
                before, old = self.kessel
                if 0 < now - before <= self.max_gap:
                    self.slope = (boiler - old) * 60 / (now - before)
                else:
                    self.slope = None
            self.kessel = now, boiler
        else:
            self.kessel = self.slope = None

    def metrics(self, now, wall):
        while self.starts and self.starts[0] < now - 3600:
            self.starts.popleft()
        while self.segments and self.segments[0][1] <= now - 3600:
            self.segments.popleft()
        total = on = mod_sum = mod_time = 0.0
        for start, end, active, mod in self.segments:
            duration = max(0, end - max(start, now - 3600))
            total += duration
            if active:
                on += duration
                if mod is not None:
                    mod_sum += mod * duration
                    mod_time += duration
        runs = list(self.runs)[-20:]
        pauses = list(self.pauses)[-20:]
        return {"starts_last_hour": len(self.starts),
                "starts_today": self.starts_today if self.day == datetime.fromtimestamp(wall).date() else 0,
                "mean_run_seconds": round(sum(runs)/len(runs), 1) if runs else None,
                "mean_pause_seconds": round(sum(pauses)/len(pauses), 1) if pauses else None,
                "burner_duty_pct": round(100*on/total, 1) if total else None,
                "observation_seconds_last_hour": round(total, 1),
                "mean_modulation_pct": round(mod_sum/mod_time, 1) if mod_time else None,
                "boiler_rise_k_min": round(self.slope, 2) if self.slope is not None else None,
                "short_cycle_warning": sum(r < self.short_seconds for r in runs[-3:]) == 3 if len(runs) >= 3 else None}


class Diagnostics:
    def __init__(self, config):
        self.config = config
        self.started = time.monotonic()
        self.cpu_sample = self.started, time.process_time()
        self.counters = Counter()
        self.errors = deque(maxlen=100)
        self.audit = deque(maxlen=100)
        self.latencies = deque(maxlen=256)
        self.points = {}
        self.history = deque(maxlen=config.history_points)
        self.heating = Heating(config.heating_max_gap, config.short_burn_seconds)
        self.device = {"id": None, "profile": None, "firmware": None}
        self.profile = None
        self.last_write = None
        self.last_tick = -math.inf
        self.last_snapshot = {}

    def transaction(self, code, elapsed, source, address=None, data=b""):
        self.counters["transactions"] += 1
        self.counters["success"] += int(code == 1)
        self.counters["code_%02x" % code] += 1
        if code == 0xFE:
            self.counters['crc_errors'] += 1
        elif code == 0xFF:
            self.counters['timeouts'] += 1
        self.latencies.append(max(0, elapsed) * 1000)
        if code != 1:
            kind = "device" if code == 3 else "decode" if code == 0xFB else "transport"
            self.errors.append({"time": time.time(), "source": source, "kind": kind,
                                "code": "%02X" % code, "address": address,
                                "data_hex": bytes(data[:128]).hex()})
    
    def observe(self, name, value, now, wall):
        rule = self.config.plausibility.get(name)
        n = number(value)
        if rule and (n is None or (rule[0] is not None and n < rule[0]) or
                     (rule[1] is not None and n > rule[1])):
            self.counters["implausible_values"] += 1
            return False
        old = self.points.get(name)
        changed = now if old is None or value != old["value"] else old["changed"]
        self.points[name] = {"value": value, "at": now, "wall": wall, "changed": changed}
        roles = self.config.heating_names
        def recent(role):
            point = self.points.get(roles.get(role))
            return number(point["value"]) if point and now - point["at"] <= self.config.heating_max_gap else None
        if name == roles.get("burner") or name == roles.get("boiler"):
            mod = recent("modulation")
            self.heating.observe(recent("burner"), mod if mod is not None and 0 <= mod <= 100 else None,
                                 recent("boiler"), now, wall,
                                 burner_sample=name == roles.get("burner"),
                                 boiler_sample=name == roles.get("boiler"))
        return True

    def configure_points(self, names):
        allowed = set(names)
        self.points = {k: v for k, v in self.points.items() if k in allowed}

    def set_device(self, ident, names):
        self.profile = self.config.device_profiles.get(ident)
        self.device = {"id": ident, "firmware_raw": None, "profile": self.profile.get("name") if self.profile else None,
                       "firmware": self.profile.get("firmware") if self.profile else None,
                       "missing_configured_points": sorted(set(self.profile.get("expected_datapoints", [])) - set(names)) if self.profile else []}

    def tick(self, now, wall, quality, ready, mqtt, queues, scheduler, restarts):
        interval = self.config.telemetry_interval
        if now - self.last_tick < interval and self.last_snapshot.get("optolink_ready") == ready:
            return None
        self.last_tick = now
        point_rows = {}
        for name in quality:
            point = self.points.get(name, {"value": None, "wall": None, "changed": now})
            status = quality.get(name)
            valid = bool(ready and status and status.valid and name in self.points)
            unchanged = self.config.unchanged_warning_seconds.get(name)
            point_rows[name] = {"value": point["value"] if valid else None,
                                "last_success": point["wall"], "valid": valid,
                                "unchanged_warning": bool(valid and unchanged and now-point["changed"] > unchanged)}
        def role(key):
            name = self.config.heating_names.get(key)
            point = self.points.get(name)
            if not point or not point_rows[name]["valid"] or now - point["at"] > self.config.heating_max_gap:
                return None
            return number(point["value"])
        active, mod, boiler = role("burner"), role("modulation"), role("boiler")
        if mod is not None and not 0 <= mod <= 100:
            mod = None
        if active is None:
            self.heating.previous = self.heating.transition = None
        if boiler is None:
            self.heating.kessel = self.heating.slope = None
        heat = self.heating.metrics(now, wall) if self.config.heating_names else {}
        flow, ret, target = role("flow"), role("return"), role("boiler_target")
        if self.config.heating_names:
            heat["spread_k"] = round(flow-ret, 2) if flow is not None and ret is not None else None
            heat["boiler_overshoot_k"] = round(boiler-target, 2) if boiler is not None and target is not None else None
            heat["burner_active"] = active
            heat["modulation_pct"] = mod
        prev_t, prev_cpu = self.cpu_sample
        cpu = time.process_time()
        system = read_system()
        system["cpu_pct"] = round(100*(cpu-prev_cpu)/max(0.001, now-prev_t), 2)
        system["uptime_seconds"] = round(now-self.started)
        self.cpu_sample = now, cpu
        total = self.counters["transactions"]
        stats = dict(self.counters)
        stats["success_rate_pct"] = round(100*self.counters["success"]/total, 2) if total else None
        stats["response_mean_ms"] = round(sum(self.latencies)/len(self.latencies), 1) if self.latencies else None
        stats["response_max_ms"] = round(max(self.latencies), 1) if self.latencies else None
        stats["slow_response_warning"] = bool(self.latencies and max(self.latencies) > 1000)
        stats["restarts"] = restarts
        stats["poll_estimated_utilization_pct"] = round(100*scheduler.utilization, 1)
        self.history.append({"time": wall, "boiler": boiler, "flow": flow, "return": ret,
                             "modulation": mod, "burner": active})
        snapshot = {"time": wall, "optolink_ready": ready, "mqtt_connected": mqtt,
                    "system": system, "communication": stats, "queues": queues,
                    "device": dict(self.device), "heating": heat, "datapoints": point_rows,
                    "polling": scheduler.snapshot(), "errors": list(self.errors),
                    "writes": list(self.audit), "history": list(self.history)}
        self.last_snapshot = snapshot
        return snapshot


class SerialMeter:
    """Transparent serial proxy; counts I/O calls and bytes, not decoded frames."""
    def __init__(self, serial_port, counters):
        self.port, self.counters = serial_port, counters

    def __getattr__(self, name):
        return getattr(self.port, name)

    def write(self, data):
        count = self.port.write(data)
        self.counters["serial_write_calls"] += 1
        self.counters["serial_bytes_tx"] += count if isinstance(count, int) else len(data)
        return count

    def read(self, *args, **kwargs):
        data = self.port.read(*args, **kwargs)
        self.counters["serial_bytes_rx"] += len(data)
        return data

    def read_all(self):
        data = self.port.read_all()
        self.counters["serial_bytes_rx"] += len(data)
        return data
