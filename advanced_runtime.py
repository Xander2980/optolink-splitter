"""Integration layer: one serial worker; HTTP consumes immutable snapshots."""
import json
import time
import serial
from advanced_config import AdvancedConfig
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
        self.store = SnapshotStore()
        self.web = None
        self.cycle_started = time.monotonic()
        self.last_cycles = 0
        self.discovered = set()

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

    def configure(self, poll):
        self.scheduler.configure(poll.items, poll.cycle_groups, time.monotonic())
        self.diag.configure_points(poll.datapoint_metadata)
        self.diag.set_device(self.diag.device.get("id"), poll.datapoint_metadata)
        unknown = set(self.config.poll_overrides) - set(poll.datapoint_metadata)
        if unknown:
            logger.warning("Unknown polling override names: %s", sorted(unknown))

    def wrap_serial(self, port):
        return SerialMeter(port, self.diag.counters)

    def identify(self, port, poll, pause):
        self.diag.set_device(None, poll.datapoint_metadata)
        if not self.config.detect_device:
            return
        # 0x00f8 / 4 bytes is the existing VS1 initialization identification read.
        # Treat the complete returned bytes as an opaque fingerprint, not a guessed model.
        started = time.monotonic()
        code, _, data = vs12_adapter.read_datapoint_ext(0x00f8, 4, port)
        self.diag.transaction(code, time.monotonic()-started, "identify", 0xf8, data)
        if code == 0xAA:
            raise serial.SerialException("USB removed during identification")
        if code == 1 and len(data) == 4:
            self.diag.set_device(bytes(data).hex(), poll.datapoint_metadata)
        pause(code)
        fw = self.config.firmware_datapoint
        if fw:
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
        self.diag.transaction(result[0], time.monotonic()-start, source, address, result[1])
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
            self.publish(state.mqtt, snapshot)

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

    def discovery(self, mqtt, group, key, topic):
        import hashlib
        identity = hashlib.sha256(settings.mqtt_topic.encode()).hexdigest()[:12]
        unique = "optolink_" + identity + "_" + group + "_" + key
        # Publish discovery repeatedly after reconnect; redundant publishes are filtered by MQTT.
        config = {"name": key.replace("_", " "), "unique_id": unique, "state_topic": topic,
                  "availability_topic": settings.mqtt_topic + "/LWT",
                  "device": {"identifiers": ["optolink_advanced_" + identity], "name": "Optolink Diagnostics"}}
        mqtt.publish_smart("homeassistant/sensor/" + unique + "/config", json.dumps(config), qos=1, retain=True)
