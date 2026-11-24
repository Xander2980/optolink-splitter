"""Deterministic regression tests; no real heater, broker or GPIO required."""
import base64
import json
import math
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from advanced_config import AdvancedConfig
from advanced_diagnostics import Diagnostics, Heating, SerialMeter
from advanced_scheduler import Scheduler
from advanced_web import SnapshotStore, LimitedServer, make_handler, stop_web, start_web
from advanced_write import guarded_write
from advanced_runtime import AdvancedRuntime


class ConfigTests(unittest.TestCase):
    def test_invalid_ranges_and_profile(self):
        for raw in ({"poll_bus_target": 2}, {"poll_overrides": {"temp": {"seconds": 0}}},
                    {"plausibility": {"t": [10, 1]}}, {"device_profiles": {"oops": {}}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                AdvancedConfig.parse(raw)

    def test_valid_configuration(self):
        c = AdvancedConfig.parse({"poll_overrides": {"t": {"seconds": 5, "priority": 8}},
            "device_profiles": {"01020304": {"writable": {"1234": {"length": 1, "raw_limits": [0, 50]}}}}})
        self.assertEqual(c.poll_overrides["t"]["priority"], 8)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.s = Scheduler({"a": {"seconds": 2, "priority": 8},
                            "b": {"seconds": 5}, "disabled": {"seconds": 1}}, 0.65)
        self.s.configure([("g", "a"), ("g", "b"), ("off", "disabled")], {"g": 1, "off": -1}, 0)

    def test_priority_and_legacy_disabled(self):
        self.assertEqual(self.s.choose(0), ("a", 0))
        self.assertFalse(self.s.owns("disabled"))
        self.s.complete("a", 1, 1, 1)
        self.assertEqual(self.s.choose(2), ("b", 1))

    def test_backoff_and_recovery(self):
        for n in range(3):
            self.s.complete("a", 3, n, 1)
        self.assertTrue(self.s.jobs["a"].unsupported)
        self.assertGreaterEqual(self.s.jobs["a"].due, 900)
        self.s.reset(10)
        self.assertEqual(self.s.jobs["a"].failures, 0)

    def test_overload_slows_intervals(self):
        self.s.cost = 10
        self.s.complete("a", 1, 0, 10)
        self.assertGreater(self.s.jobs["a"].due, 2)

    def test_scheduler_stays_bounded_under_load(self):
        for i in range(10000):
            chosen = self.s.choose(i)
            if chosen:
                self.s.complete(chosen[0], 1, i, 0.1)
        self.assertEqual(len(self.s.jobs), 2)


class DiagnosticsTests(unittest.TestCase):
    def test_heating_observed_transitions_and_gap(self):
        h = Heating(30, 300)
        for t, active in [(0,0),(10,1),(20,1),(30,0),(40,0),(50,1),(60,0)]:
            h.observe(active, 50, 40+t/60, t, 1700000000+t)
        m = h.metrics(60,1700000060)
        self.assertEqual(m["starts_last_hour"], 2)
        self.assertEqual(m["mean_run_seconds"], 15)
        self.assertEqual(m["mean_pause_seconds"], 20)
        self.assertAlmostEqual(m["boiler_rise_k_min"],1)
        h.observe(1,50,42,200,1700000200)
        self.assertEqual(h.metrics(200,1700000200)["starts_last_hour"],2)

    def test_bounded_error_history_and_plausibility(self):
        d = Diagnostics(AdvancedConfig.parse({"plausibility": {"temperature": [0,100]}}))
        self.assertFalse(d.observe("temperature", 101, 1, 1))
        self.assertFalse(d.observe("temperature", float("nan"), 1, 1))
        self.assertTrue(d.observe("temperature", 42, 2, 2))
        for _ in range(1000):
            d.transaction(0xFE, .1, "poll", 0x1234, b"bad")
        self.assertEqual(len(d.errors), 100)
        self.assertEqual(len(d.latencies), 256)
        self.assertEqual(d.counters["crc_errors"], 1000)

    def test_serial_meter(self):
        d = Diagnostics(AdvancedConfig())
        port = Mock()
        port.write.return_value = 3
        port.read_all.return_value = b"abc"
        proxy = SerialMeter(port, d.counters)
        self.assertEqual(proxy.write(b"abc"), 3)
        self.assertEqual(proxy.read_all(), b"abc")
        self.assertEqual(d.counters["serial_bytes_tx"], 3)
        self.assertEqual(d.counters["serial_bytes_rx"], 3)

    def test_counters_use_longer_freshness_limit(self):
        d = Diagnostics(AdvancedConfig.parse({"telemetry_interval":1, "heating_max_gap":120, "counter_max_gap":900,
                                              "heating_names":{"hours_counter":"h", "outside":"o"}}))
        d.observe("h", 54139.9, d.started, 1700000000)
        d.observe("o", -3.5, d.started, 1700000000)
        ok = {"h":SimpleNamespace(valid=True), "o":SimpleNamespace(valid=True)}
        snap = d.tick(d.started+300, 1700000300, ok, True, False, {}, Scheduler({}), 0)
        self.assertEqual(snap["heating"]["burner_hours_total"], 54139.9)
        self.assertIsNone(snap["heating"]["outside_c"])
        snap = d.tick(d.started+1000, 1700001000, ok, True, False, {}, Scheduler({}), 0)
        self.assertIsNone(snap["heating"]["burner_hours_total"])

    def test_snapshot_masks_invalid_points_and_limits_history(self):
        d = Diagnostics(AdvancedConfig.parse({"history_points":60, "telemetry_interval":1}))
        d.observe("x", 30, 1, 1)
        sched = Scheduler({})
        for n in range(1,100):
            snap = d.tick(d.started+n, 1700000000+n, {"x":SimpleNamespace(valid=False)},True,False,{},sched,0)
        self.assertIsNone(snap["datapoints"]["x"]["value"])
        self.assertEqual(len(snap["history"]),60)


class WriteTests(unittest.TestCase):
    def test_read_only_never_calls_bus(self):
        d = Diagnostics(AdvancedConfig.parse({"read_only":True}))
        execute, read = Mock(), Mock()
        result = guarded_write("write;0x1234;1;42","mqtt",d,read,execute,Mock())
        self.assertNotEqual(result[0],1)
        execute.assert_not_called()
        read.assert_not_called()

    def test_no_write_when_value_unchanged(self):
        d = Diagnostics(AdvancedConfig.parse({"verified_writes":True}))
        execute = Mock()
        result = guarded_write("write;0x1234;1;42","mqtt",d,Mock(return_value=(1,0x1234,b"*")),execute,Mock())
        self.assertEqual(result[0],1)
        execute.assert_not_called()
        self.assertEqual(d.audit[-1]["status"],"unchanged")

    def test_mismatch_no_retry(self):
        d = Diagnostics(AdvancedConfig.parse({"verified_writes":True}))
        read = Mock(side_effect=[(1,0x1234,b"\x01"),(1,0x1234,b"\x02")])
        execute = Mock(return_value=(1,b"",42,"ok"))
        result = guarded_write("write;0x1234;1;42","tcp",d,read,execute,Mock())
        self.assertEqual(result[0],0xFC)
        execute.assert_called_once()
        self.assertEqual(d.audit[-1]["status"],"verification_failed")

    def test_profile_fail_closed_and_raw_block(self):
        d = Diagnostics(AdvancedConfig.parse({"require_profile_for_writes":True}))
        execute = Mock()
        for command in ("write;0x1234;1;42","raw;41050001"):
            self.assertNotEqual(guarded_write(command,"tcp",d,Mock(),execute,Mock())[0],1)
        execute.assert_not_called()

    def test_verified_and_rate_limited(self):
        d = Diagnostics(AdvancedConfig.parse({"verified_writes":True,"write_min_interval":30}))
        read = Mock(side_effect=[(1,0x1234,b"\x01"),(1,0x1234,b"*")])
        execute = Mock(return_value=(1,b"",42,"ok"))
        self.assertEqual(guarded_write("write;0x1234;1;42","mqtt",d,read,execute,Mock())[0],1)
        self.assertEqual(d.audit[-1]["status"],"verified")
        self.assertNotEqual(guarded_write("write;0x1234;1;43","mqtt",d,read,execute,Mock())[0],1)
        execute.assert_called_once()


class WebTests(unittest.TestCase):
    def test_http_auth_readonly_and_assets(self):
        import threading
        store=SnapshotStore()
        store.update({"optolink_ready":True,"value":42})
        server=LimitedServer(("127.0.0.1",0),make_handler(store,"test-password"))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        url="http://127.0.0.1:%d" % server.server_address[1]
        auth="Basic "+base64.b64encode(b"optolink:test-password").decode()
        try:
            with self.assertRaises(HTTPError) as err:
                urlopen(url+"/api/status",timeout=2)
            self.assertEqual(err.exception.code,401)
            with urlopen(Request(url+"/api/status",headers={"Authorization":auth}),timeout=2) as response:
                self.assertEqual(json.load(response)["value"],42)
                self.assertIn("no-store",response.headers["Cache-Control"])
            with urlopen(Request(url+"/",headers={"Authorization":auth}),timeout=2) as response:
                self.assertIn(b"Control Center",response.read())
            with self.assertRaises(HTTPError) as err:
                urlopen(Request(url+"/api/status",data=b"{}",headers={"Authorization":auth}),timeout=2)
            self.assertEqual(err.exception.code,405)
        finally:
            stop_web((server,thread))

    def test_public_bind_without_password_allowed_when_opted_out(self):
        c=AdvancedConfig.parse({"web_host":"0.0.0.0","web_require_password":False})
        c.web_port=0
        with patch.object(c,"web_password",return_value=""):
            web=start_web(c,SnapshotStore())
        try:
            port=web[0].server_address[1]
            with urlopen("http://127.0.0.1:%d/api/status"%port,timeout=2) as r:
                self.assertEqual(r.status,200)
        finally:
            stop_web(web)
        with self.assertRaises(ValueError):
            AdvancedConfig.parse({"web_require_password":"no"})

    def test_public_bind_without_password_is_rejected(self):
        c=AdvancedConfig.parse({"web_host":"0.0.0.0"})
        with patch.object(c,"web_password",return_value=""), self.assertRaises(ValueError):
            start_web(c,SnapshotStore())


class IntegrationTests(unittest.TestCase):
    def test_device_identity_only_matches_exact_profile(self):
        a=AdvancedRuntime({"device_profiles":{"01020304":{"name":"verified","writable":{}}}})
        poll=SimpleNamespace(datapoint_metadata={})
        with patch("advanced_runtime.vs12_adapter.read_datapoint_ext",return_value=(1,0xf8,bytes([1,2,3,4]))):
            a.identify(Mock(),poll,Mock())
        self.assertEqual(a.diag.device["profile"],"verified")

    def test_request_telemetry_and_read_only(self):
        a=AdvancedRuntime({"read_only":True})
        with patch("advanced_runtime.requests_util.response_to_request",return_value=(1,bytearray([42]),42,"ok")) as request:
            self.assertEqual(a.request("read;0x1234;1",Mock(),"mqtt")[0],1)
            self.assertNotEqual(a.request("write;0x1234;1;42",Mock(),"mqtt")[0],1)
            request.assert_called_once()
        self.assertEqual(a.diag.counters["transactions"],2)


if __name__=="__main__":
    unittest.main()
