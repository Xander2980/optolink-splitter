"""Optional local diagnostics. No extra runtime dependencies."""
import math
import os
from dataclasses import dataclass, field


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def bounds(value):
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(
        v is None or finite(v) for v in value
    ) and (None in value or value[0] <= value[1])


@dataclass
class AdvancedConfig:
    enabled: bool = True
    web_enabled: bool = True
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    web_password_env: str = "OPTOLINK_WEB_PASSWORD"
    telemetry_interval: float = 10
    history_points: int = 720
    poll_overrides: dict = field(default_factory=dict)
    poll_bus_target: float = 0.65
    plausibility: dict = field(default_factory=dict)
    unchanged_warning_seconds: dict = field(default_factory=dict)
    heating_names: dict = field(default_factory=dict)
    burner_states: dict = field(default_factory=dict)
    auto_catalog_profile: bool = True
    heating_max_gap: float = 30
    short_burn_seconds: float = 300
    read_only: bool = False
    verified_writes: bool = False
    write_min_interval: float = 2
    detect_device: bool = True
    firmware_datapoint: object = None
    mqtt_discovery: bool = False
    device_profiles: dict = field(default_factory=dict)
    require_profile_for_writes: bool = False

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("advanced must be a dictionary")
        try:
            obj = cls(**raw)
        except TypeError as exc:
            raise ValueError("Unknown advanced configuration key") from exc
        for name in ("enabled", "web_enabled", "read_only", "verified_writes",
                     "detect_device", "require_profile_for_writes", "mqtt_discovery", "auto_catalog_profile"):
            if type(getattr(obj, name)) is not bool:
                raise ValueError(name + " must be boolean")
        if type(obj.web_port) is not int or not 1 <= obj.web_port <= 65535:
            raise ValueError("Invalid web_port")
        if not isinstance(obj.web_host, str) or not obj.web_host:
            raise ValueError("Invalid web_host")
        if not isinstance(obj.web_password_env, str) or not obj.web_password_env:
            raise ValueError("Invalid web_password_env")
        for name in ("telemetry_interval", "heating_max_gap", "short_burn_seconds"):
            if not finite(getattr(obj, name)) or getattr(obj, name) < 1:
                raise ValueError("Invalid " + name)
        if not finite(obj.write_min_interval) or obj.write_min_interval < 0:
            raise ValueError("Invalid write_min_interval")
        if not finite(obj.poll_bus_target) or not 0.1 <= obj.poll_bus_target <= 0.9:
            raise ValueError("poll_bus_target must be 0.1..0.9")
        if type(obj.history_points) is not int or not 60 <= obj.history_points <= 3600:
            raise ValueError("history_points must be 60..3600")
        for name in ("poll_overrides", "plausibility", "unchanged_warning_seconds",
                     "heating_names", "device_profiles", "burner_states"):
            mapping = getattr(obj, name)
            if not isinstance(mapping, dict) or any(not isinstance(k, str) for k in mapping):
                raise ValueError("Invalid " + name)
        for name, rule in obj.poll_overrides.items():
            if not isinstance(rule, dict) or set(rule) - {"seconds", "priority"}:
                raise ValueError("Invalid polling rule: " + name)
            seconds, priority = rule.get("seconds"), rule.get("priority", 0)
            if not finite(seconds) or seconds < 0.5 or type(priority) is not int or not 0 <= priority <= 10:
                raise ValueError("Invalid polling interval or priority: " + name)
        if any(not bounds(v) for v in obj.plausibility.values()):
            raise ValueError("Invalid plausibility limits")
        if any(not finite(v) or v <= 0 for v in obj.unchanged_warning_seconds.values()):
            raise ValueError("Invalid unchanged_warning_seconds")
        if any(not isinstance(v, str) or not v for v in obj.heating_names.values()):
            raise ValueError("heating_names must map roles to datapoint names")
        if any(not k.isdecimal() or type(v) is not int or v not in (0, 1) for k, v in obj.burner_states.items()):
            raise ValueError("burner_states must map numeric strings to 0 or 1")
        fw = obj.firmware_datapoint
        if fw is not None and (not isinstance(fw, (tuple, list)) or len(fw) != 2 or type(fw[0]) is not int or not 0 <= fw[0] <= 65535 or type(fw[1]) is not int or not 1 <= fw[1] <= 250):
            raise ValueError("firmware_datapoint must be (address, length) or None")
        for ident, profile in obj.device_profiles.items():
            if len(ident) != 8 or any(c not in "0123456789abcdef" for c in ident):
                raise ValueError("Device profile key must be lowercase 4-byte hex")
            if not isinstance(profile, dict) or set(profile) - {"name", "writable", "expected_datapoints", "firmware"}:
                raise ValueError("Invalid device profile")
            if not isinstance(profile.get("name", ""), str):
                raise ValueError("Invalid device profile name")
            if not isinstance(profile.get("firmware", ""), str):
                raise ValueError("Invalid device profile firmware label")
            writable = profile.get("writable", {})
            if not isinstance(writable, dict):
                raise ValueError("Invalid device writable mapping")
            for address, rule in writable.items():
                if not isinstance(address, str):
                    raise ValueError("Profile address must be a hex string")
                try:
                    addr = int(address, 16)
                except ValueError as exc:
                    raise ValueError("Invalid profile address") from exc
                if (address != '%04x' % addr or not 0 <= addr <= 65535 or not isinstance(rule, dict)
                        or type(rule.get("length")) is not int or not 1 <= rule["length"] <= 250
                        or not bounds(rule.get("raw_limits"))
                        or type(rule.get("signed", False)) is not bool):
                    raise ValueError("Invalid profile write rule")
            expected = profile.get("expected_datapoints", [])
            if not isinstance(expected, (list, tuple)) or any(not isinstance(n, str) for n in expected):
                raise ValueError("Invalid expected_datapoints")
        return obj

    def web_password(self):
        return os.environ.get(self.web_password_env, "")
