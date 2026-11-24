"""Integration layer: one serial worker; HTTP consumes immutable snapshots."""
import json
import time
import serial
from advanced_config import AdvancedConfig
from advanced_profiles import Catalog
from advanced_diagnostics import Diagnostics, SerialMeter
from advanced_scheduler import Scheduler
from advanced_web import SnapshotStore, start_web, stop_web
from advanced_write import guarded_write
from logger_util import logger
import requests_util
import vs12_adapter
from c_settings_adapter import settings
import viconn_util


class AdvancedRuntime:
    def __init__(self, raw):
        self.config = AdvancedConfig.parse(raw)
        self.diag = Diagnostics(self.config)
        self.scheduler = Scheduler(self.config.poll_overrides, self.config.poll_bus_target)
        self.catalog = Catalog()
        self.store = SnapshotStore(self.catalog)
        self.catalog_profile = None
        self.web = None
        self.cycle_started = time.monotonic()
        self.last_cycles = 0
        self.discovered = set()
        self.generation = None

    def start(self):
        try:
            self.web = start_web(self.config, self.store)
            if self.web:
                logger.info("Read-only dashboard listening at %s:%s", self.config.web_host, self.config.web_port)
        except (OSError, ValueError):
            logger.exception("Dashboard unavailable; Optolink service continues")

    def stop(self):
        stop_web(self.web)
        self.web = None

    def apply_catalog_profile(self, ident, poll):
        profile = self.catalog.match(ident) if self.config.auto_catalog_profile else None
        roles = self.catalog.resolve(profile, poll)
        previous = self.diag.heating_names
        self.diag.heating_names = {**roles, **self.config.heating_names}
        states = profile.get("burner_states", {}) if profile and roles.get("burner") and "burner" not in self.config.heating_names else {}
        self.diag.burner_states = {**states, **self.config.burner_states}
        if previous != self.diag.heating_names:
            self.diag.heating.invalidate()
        self.catalog_profile = profile
        self.diag.device["catalog_profile"] = profile["name"] if profile else None
        self.diag.device["catalog_missing_roles"] = sorted(set(profile.get("roles", {})) - set(roles)) if profile else []

    def configure(self, poll):
        self.scheduler.configure(poll.items, poll.cycle_groups, time.monotonic())
        self.diag.configure_points(poll.datapoint_metadata)
        ident = self.diag.device.get("id")
        firmware = self.diag.device.get("firmware_raw")
        self.diag.set_device(ident, poll.datapoint_metadata)
        self.diag.device["firmware_raw"] = firmware
        self.apply_catalog_profile(ident, poll)
        unknown = set(self.config.poll_overrides) - set(poll.datapoint_metadata)
        if unknown:
            logger.warning("Unknown polling override names: %s", sorted(unknown))

    def wrap_serial(self, port):
        return SerialMeter(port, self.diag.counters)

    def identify(self, port, poll, pause):
        self.diag.set_device(None, poll.datapoint_metadata)
        self.apply_catalog_profile(None, poll)
        if not self.config.detect_device:
            return
        if viconn_util.has_pending_requests():
            logger.info('Device identification deferred to avoid delaying Vitoconnect')
            return
        # 0x00f8 / 4 bytes is the existing VS1 initialization identification read.
        # Keep the full fingerprint for write authorization; catalogs match HW/SW separately.
        started = time.monotonic()
        code, _, data = vs12_adapter.read_datapoint_ext(0x00f8, 4, port)
        self.diag.transaction(code, time.monotonic()-started, "identify", 0xf8, data)
        if code == 0xAA:
            raise serial.SerialException("USB removed during identification")
        if code == 1 and len(data) == 4:
            ident = bytes(data).hex()
            self.diag.set_device(ident, poll.datapoint_metadata)
            self.apply_catalog_profile(ident, poll)
            if self.config.auto_catalog_profile and self.catalog_profile is None:
                logger.warning(
                    "No unambiguous catalog profile for device %s (hardware 0x%02X, software 0x%02X); "
                    "automatic role mapping unavailable", ident, data[2], data[3])
        pause(code)
        fw = self.config.firmware_datapoint
        if fw is None and self.catalog_profile:
            fw = self.catalog_profile.get("firmware_datapoint")
        if fw and not viconn_util.has_pending_requests():
            started = time.monotonic()
            code, _, data = vs12_adapter.read_datapoint_ext(fw[0], fw[1], port)
            self.diag.transaction(code, time.monotonic()-started, "firmware", fw[0], data)
            if code == 0xAA:
                raise serial.SerialException("USB removed during firmware read")
            if code == 1 and len(data) == fw[1]:
                self.diag.device["firmware_raw"] = bytes(data).hex()
            pause(code)

    def request(self, command, port, source, pause=None):
        start = time.monotonic()
        address = None
        try:
            parts = command.split(";") if isinstance(command, str) else command
            if len(parts) > 1:
                try:
                    address = int(str(parts[1]), 0)
                except (ValueError, TypeError):
                    pass
            execute = lambda: requests_util.response_to_request(command, port)
            if isinstance(command, str) and requests_util.is_write_command(command):
                result = guarded_write(command, source, self.diag,
                    lambda addr, length: vs12_adapter.read_datapoint_ext(addr, length, port),
                    execute, lambda: pause(1) if pause else None)
                event = self.diag.audit[-1] if self.diag.audit else {}
                logger.info("Write audit %s", json.dumps(event, separators=(",", ":")))
            else:
                result = execute()
        except (serial.SerialException, OSError):
            self.diag.transaction(0xAA, time.monotonic()-start, source, address)
            raise
        except (ValueError, TypeError, IndexError, OverflowError, KeyError):
            self.diag.transaction(0xFB, time.monotonic()-start, source, address)
            raise
        if result[0] == 0xAA:
            self.diag.counters["usb_io_failures"] += 1
        self.diag.transaction(result[0], time.monotonic()-start, source, address, result[1] or b"")
        return result

    def tick(self, state, connected, now, force=False):
        # Snapshots are built only by the main serial worker.
        queues = {"mqtt": state.mqtt.cmnd_queue.qsize() if state.mqtt else 0,
                  "tcp": state.tcp.requests.qsize() if state.tcp else 0,
                  "readbacks": len(state.mqtt.lst_force_refresh) if state.mqtt else 0,
                  "vitoconnect_pending": viconn_util.has_pending_requests()}
        cycles = self.diag.counters["poll_cycles"]
        if cycles != self.last_cycles:
            self.diag.counters["last_poll_cycle_seconds"] = round(now-self.cycle_started, 2)
            self.cycle_started, self.last_cycles = now, cycles
        if force:
            self.diag.last_tick = float("-inf")
        snapshot = self.diag.tick(now, time.time(), state.measurements.values,
                                  state.optolink_ready, connected, queues,
                                  self.scheduler, state.num_restarts)
        if snapshot is None:
            return
        try:
            self.store.update(snapshot)
        except (TypeError, ValueError):
            logger.exception("Dashboard snapshot encoding failed")
        if connected and state.mqtt:
            try:
                generation = state.mqtt.connection_generation
                if generation != self.generation:
                    self.discovered.clear()
                    self.generation = generation
                self.publish(state.mqtt, snapshot)
            except (ValueError, TypeError, OSError):
                logger.exception('Advanced telemetry publication failed')

    def publish(self, mqtt, snap):
        base = settings.mqtt_topic + "/advanced"
        for group in ("system", "communication", "heating", "queues", "device"):
            values = snap[group]
            mqtt.publish_smart(base + "/" + group, json.dumps(values, default=str, allow_nan=False), retain=True)
            for key, value in values.items():
                if isinstance(value, (int, float, bool)) or value is None:
                    topic = base + "/" + group + "/" + key
                    payload = "unknown" if value is None else int(value) if isinstance(value, bool) else value
                    mqtt.publish_smart(topic, payload, retain=True)
                    if self.config.mqtt_discovery and group != "device":
                        self.discovery(mqtt, group, key, topic)
        mqtt.publish_smart(base + "/errors", json.dumps(snap["errors"]), retain=True)
        mqtt.publish_smart(base + "/writes", json.dumps(snap["writes"]), retain=False)

    def discovery(self, mqtt, group, key, topic):
        import hashlib
        identity = hashlib.sha256(settings.mqtt_topic.encode()).hexdigest()[:12]
        unique = "optolink_" + identity + "_" + group + "_" + key
        if unique in self.discovered:
            return
        config = {"name": key.replace("_", " "), "unique_id": unique, "state_topic": topic,
                  "availability_topic": settings.mqtt_topic + "/LWT",
                  "device": {"identifiers": ["optolink_advanced_" + identity], "name": "Optolink Diagnostics"}}
        info = mqtt.publish_smart("homeassistant/sensor/" + unique + "/config", json.dumps(config), qos=1, retain=True, force=True)
        if info is not None and info.rc == 0:
            self.discovered.add(unique)
