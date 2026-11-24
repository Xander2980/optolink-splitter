import unittest
import threading
import json
from unittest.mock import Mock, call, patch
from urllib.request import urlopen
from advanced_web import LimitedServer, make_handler, stop_web
from advanced_profiles import Catalog
from advanced_web import SnapshotStore
from types import SimpleNamespace
from advanced_runtime import AdvancedRuntime
from advanced_config import AdvancedConfig
from advanced_diagnostics import Diagnostics

class CatalogRuntimeTests(unittest.TestCase):
    def test_identifies_reported_device_and_reads_software_index(self):
        a = AdvancedRuntime({})
        poll = SimpleNamespace(datapoint_metadata={"flammenstatus": {
            "addr": 0x55de, "len": 1, "scale_type": 1, "list_index": 0}},
            items=[("a",)], cycle_groups={"a": 1})
        port = Mock()
        with patch("advanced_runtime.viconn_util.has_pending_requests", return_value=False), \
             patch("advanced_runtime.vs12_adapter.read_datapoint_ext", side_effect=[
                 (1, 0xf8, bytes.fromhex("20cb034f")), (1, 0xfb, b"\x4f")]) as read:
            a.identify(port, poll, Mock())
        self.assertEqual(read.call_args_list, [call(0xf8, 4, port), call(0xfb, 1, port)])
        self.assertEqual(a.diag.device["id"], "20cb034f")
        self.assertEqual(a.diag.device["catalog_profile"], "VScotHO1_72")
        self.assertEqual(a.diag.device["firmware_raw"], "4f")
        self.assertEqual(a.diag.heating_names, {"burner": "flammenstatus"})
        self.assertEqual(a.diag.burner_value(11), 1)
        self.assertIsNone(a.diag.profile)

    def test_unknown_hardware_warns_without_granting_a_profile(self):
        for enabled in (True, False):
            with self.subTest(auto_catalog_profile=enabled):
                a = AdvancedRuntime({"auto_catalog_profile": enabled})
                poll = SimpleNamespace(datapoint_metadata={})
                with patch("advanced_runtime.viconn_util.has_pending_requests", return_value=False), \
                     patch("advanced_runtime.vs12_adapter.read_datapoint_ext",
                           return_value=(1, 0xf8, bytes.fromhex("20cb044f"))), \
                     patch("advanced_runtime.logger.warning") as warning:
                    a.identify(Mock(), poll, Mock())
                self.assertIsNone(a.diag.device["catalog_profile"])
                self.assertEqual(a.diag.heating_names, {})
                self.assertIsNone(a.diag.profile)
                if enabled:
                    warning.assert_called_once()
                    self.assertEqual(warning.call_args.args[1:], ("20cb044f", 4, 79))
                else:
                    warning.assert_not_called()

    def test_burner_state_and_no_write_grant(self):
        a=AdvancedRuntime({})
        p=SimpleNamespace(datapoint_metadata={"flammenstatus":{"addr":0x55de,"len":1,"scale_type":1,"list_index":0}},items=[("a",)],cycle_groups={"a":1})
        a.apply_catalog_profile("20cb0148",p)
        self.assertEqual(a.diag.heating_names,{"burner":"flammenstatus"})
        self.assertEqual(a.diag.burner_value(11),1)
        self.assertEqual(a.diag.burner_value(0),0)
        self.assertIsNone(a.diag.burner_value(3))
        self.assertIsNone(a.diag.profile)
        a.apply_catalog_profile("20cb015a",p)
        self.assertEqual(a.diag.heating_names,{})

    def test_manual_override(self):
        a=Diagnostics(AdvancedConfig.parse({"heating_names":{"burner":"manual"},"burner_states":{"3":1}}))
        self.assertEqual(a.burner_value(3),1)
        self.assertIsNone(a.burner_value(11))

    def test_catalog_http_search(self):
        store = SnapshotStore(Catalog())
        server = LimitedServer(("127.0.0.1", 0), make_handler(store, ""))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:%d/api/catalog" % server.server_address[1]
        try:
            with urlopen(url, timeout=2) as response:
                self.assertTrue(any(x["name"] == "VScotHO1_72" for x in json.load(response)["lists"]))
            with urlopen(url + "?model=VScotHO1_72&q=0x0810&access=R", timeout=2) as response:
                result = json.load(response)
                self.assertGreater(result["count"], 0)
                self.assertTrue(all(x["address"] == "0x0810" for x in result["rows"]))
        finally:
            stop_web((server, thread))
