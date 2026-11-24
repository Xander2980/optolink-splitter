"""Regression tests for the local robustness changes (no hardware required)."""
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import c_tcpserver
import mqtt_util
import optolinkvs2
import optolinkvs1
import serial
from c_polllist import cPollList
from c_settings_adapter import SettingsAdapter, parse_broker
from runtime_support import MeasurementTracker, reconnect_delay, freshness_deadline
import utils
import optolinkvs2_switch as switch
from c_w1value import W1Value


class TcpTests(unittest.TestCase):
    def test_fragmented_and_multiple_frames(self):
        server = c_tcpserver.TcpServer("localhost", 0, legacy_idle=None)
        server.client_socket = Mock()
        server.client_socket.recv.side_effect = [b"read;0x", b"0804;1\nread;0x0805;1\n", b""]
        server._listen()
        self.assertEqual(server.get_request(), "read;0x0804;1")
        self.assertEqual(server.get_request(), "read;0x0805;1")
        self.assertEqual(server.get_request(), "")

    def test_legacy_idle_can_be_disabled(self):
        import socket
        server = c_tcpserver.TcpServer("localhost", 0, legacy_idle=None)
        server.client_socket = Mock()
        server.client_socket.recv.side_effect = [
            b"read;0x", socket.timeout(), b"0804;1\r\n", b""
        ]
        server._listen()
        self.assertEqual(server.get_request(), "read;0x0804;1")
        self.assertEqual(server.get_request(), "")

    def test_sendall(self):
        server = c_tcpserver.TcpServer("localhost", 0)
        server.client_socket = Mock()
        server.send("response")
        server.client_socket.sendall.assert_called_once_with(b"response\n")


class MqttTests(unittest.TestCase):
    def setUp(self):
        mqtt_util.clear_pending_requests()
        mqtt_util.reset_recent_list()

    def tearDown(self):
        mqtt_util.clear_pending_requests()
        mqtt_util.reset_recent_list()

    def test_retained_command_is_ignored(self):
        with patch.object(mqtt_util.settings, "mqtt_listen", "Vito/cmnd"):
            mqtt_util.on_message(None, None, SimpleNamespace(
                topic="Vito/cmnd", payload=b"write;0x1234;1;10", retain=True))
        self.assertEqual(mqtt_util.get_mqtt_request(), ("", None))

    def test_publish_failure_not_cached(self):
        client = Mock()
        client.is_connected.return_value = True
        client.publish.side_effect = [SimpleNamespace(rc=4), SimpleNamespace(rc=0)]
        with patch.object(mqtt_util, "mqtt_client", client), patch.object(
            mqtt_util.settings, "mqtt_no_redundant", True
        ):
            mqtt_util.publish_smart("test", 42)
            mqtt_util.publish_smart("test", 42)
            mqtt_util.publish_smart("test", 42)
        self.assertEqual(client.publish.call_count, 2)

    def test_allowlist_denies_unlisted_datapoint(self):
        with patch.object(mqtt_util.settings, "set_write_allowlist", set()), patch.object(
            mqtt_util.poll_list, "find_datapoint_by_name"
        ) as lookup:
            self.assertIsNone(mqtt_util.handle_set_topic("Vito/test/set", b"20"))
            lookup.assert_not_called()

    def test_commands_are_processed_in_fifo_order(self):
        with patch.object(mqtt_util, "command_callback", None):
            mqtt_util.enqueue_request("read;0x1234;1")
            mqtt_util.enqueue_request("read;0x1235;1")
            self.assertEqual(mqtt_util.get_mqtt_request(), ("read;0x1234;1", None))
            self.assertEqual(mqtt_util.get_mqtt_request(), ("read;0x1235;1", None))

    def test_readback_queue_coalesces(self):
        mqtt_util.force_delayed("temperature", 1)
        mqtt_util.force_delayed("temperature", 1)
        self.assertEqual(len(mqtt_util.lst_force_refresh), 1)


class Vs2Tests(unittest.TestCase):
    def test_wrong_function_rejected(self):
        packet = bytearray([0x41, 6, 1, 2, 0x12, 0x34, 1, 42, 0])
        packet[-1] = optolinkvs2.calc_crc(packet)
        ser = Mock()
        ser.read_all.return_value = b"\x06" + packet
        with patch.object(optolinkvs2.utils, "comm_error"):
            code, _, _ = optolinkvs2.receive_telegr(
                True, False, ser, expected_function=1,
                expected_address=0x1234, expected_length=1)
        self.assertEqual(code, 0xFC)


class RuntimeTests(unittest.TestCase):
    def test_poll_index_builds_all_entries(self):
        poll = cPollList()
        poll.items = [("g", "one", 0x1234, 2, 0.1, True),
                      ("g", "two", 0x2345, 1, "b:0:0", 1, False)]
        poll.build_index()
        self.assertEqual(poll.find_datapoint_by_name("one")["list_index"], 0)
        self.assertEqual(poll.find_datapoint_by_name("two")["bbfilter"], "b:0:0")
        self.assertIsNone(poll.find_datapoint_by_name("missing"))

    def test_backoff_capped(self):
        self.assertEqual([reconnect_delay(10, 120, n) for n in range(1, 7)],
                         [10, 20, 40, 80, 120, 120])

    def test_freshness_and_invalid_measurements(self):
        tracker = MeasurementTracker()
        tracker.configure(["temperature"])
        tracker.observe("temperature", 21.5, 100.0, 1000.0)
        self.assertTrue(tracker.values["temperature"].valid)
        tracker.expire(110.0, {"temperature": 10})
        self.assertFalse(tracker.values["temperature"].valid)
        tracker.observe("temperature", float("nan"), 120, 1020)
        self.assertFalse(tracker.values["temperature"].valid)
        tracker.observe("temperature", 22.0, 130, 1030)
        self.assertTrue(tracker.values["temperature"].valid)
        self.assertIsNone(freshness_deadline(30, 10, 0, 0))
        tracker.invalidate_all()
        self.assertFalse(tracker.values["temperature"].valid)

    def test_configuration_rejects_invalid_values(self):
        config = SettingsAdapter()
        config.poll_read_retries = 4
        with self.assertRaises(ValueError):
            config.validate()
        self.assertEqual(parse_broker("[::1]:1883"), ("::1", 1883))

    def test_usb_disconnect_is_reported(self):
        ser = Mock()
        ser.read_all.side_effect = serial.SerialException("removed")
        self.assertEqual(utils.receive_raw_serial(0.05, 1, ser)[0], 0xAA)


class Vs1Tests(unittest.TestCase):
    def test_read_response_fragmented(self):
        ser = Mock()
        ser.read_all.side_effect = [bytes([1]), bytes([2])]
        with patch.object(optolinkvs1, "sync_elapsed", return_value=False), patch.object(utils, "comm_error"):
            code, addr, data = optolinkvs1.read_datapoint_ext(0x0804, 2, ser)
        self.assertEqual((code, addr, data), (1, 0x0804, bytearray([1, 2])))
        ser.write.assert_called_once_with(bytearray([0xf7, 0x08, 0x04, 2]))

    def test_failed_sync_does_not_send_request(self):
        ser = Mock()
        with patch.object(optolinkvs1, "sync_elapsed", return_value=True), patch.object(
                optolinkvs1, "re_init", return_value=False), patch.object(utils, "comm_error"):
            self.assertEqual(optolinkvs1.read_datapoint_ext(0x0804, 2, ser)[0], 0xFF)
        ser.write.assert_not_called()


class IntegrationTests(unittest.TestCase):
    def test_poll_retries_only_transient_reads(self):
        fake_poll = cPollList()
        fake_poll.items = [("g", "temperature", 0x0804, 2, 0.1)]
        fake_poll.cycle_groups = {"g": 1}
        fake_poll.num_items = 1
        fake_poll.build_index()
        poll_data = [None]
        with patch.object(switch, "state", switch.RuntimeState()), patch.object(
            switch, "poll_list", fake_poll
        ), patch.object(switch.settings, "poll_read_retries", 1), patch.object(
            switch.requests_util, "response_to_request",
            side_effect=[(0xff, bytearray(), "?", ""), (1, bytearray([210, 0]), 21.0, "")]
        ) as response, patch.object(switch, "olbreath"):
            code = switch.do_poll_item(poll_data, Mock())
            self.assertEqual(code, 1)
            self.assertEqual(response.call_count, 2)
            self.assertTrue(switch.state.measurements.values["temperature"].valid)
            self.assertEqual(poll_data, [21.0])

    def test_usb_reopens_after_removal(self):
        fake_poll = cPollList()
        fake_serial = Mock()
        fake_serial.is_open = True
        def init(_):
            utils.shutdown_event.set()
            return True
        utils.shutdown_event.clear()
        utils.restart_event.clear()
        try:
            with patch.object(switch, "state", switch.RuntimeState()), patch.object(
                switch, "poll_list", fake_poll
            ), patch.object(fake_poll, "make_list", return_value=True), patch.object(
                switch.settings, "port_vitoconnect", None
            ), patch.object(switch.settings, "mqtt_broker", None), patch.object(
                switch.settings, "tcpip_port", None
            ), patch.object(switch.settings, "usb_reconnect_forever", True), patch.object(
                switch.settings, "max_restarts", 0
            ), patch.object(switch.serial, "serial_for_url",
                           side_effect=[serial.SerialException("USB removed"), fake_serial]
            ) as open_serial, patch.object(switch.vs12_adapter, "init_protocol", side_effect=init), patch.object(
                utils.shutdown_event, "wait", return_value=False
            ), patch.object(switch.signal, "signal"):
                switch.main()
                self.assertEqual(open_serial.call_count, 2)
                fake_serial.close.assert_called_once()
        finally:
            utils.shutdown_event.clear()
            utils.restart_event.clear()

    def test_mqtt_reconnect_invalidates_publish_cache(self):
        client = Mock()
        with patch.object(mqtt_util, "optolink_ready", True), patch.object(
            mqtt_util.settings, "mqtt_topic", "Vito"
        ):
            mqtt_util.recent_posts["test"] = 42
            before = mqtt_util.connection_generation
            mqtt_util.on_connect(client, None, {}, 0, None)
            self.assertEqual(mqtt_util.connection_generation, before + 1)
            self.assertEqual(mqtt_util.recent_posts, {})
            client.publish.assert_any_call("Vito/LWT", "online", qos=1, retain=True)

    def test_onewire_outlier_is_not_fresh(self):
        value = W1Value(1, max_change=10, max_ignore=2)
        self.assertEqual(value.checked(20), 20)
        self.assertEqual(value.checked(80), 20)
        self.assertFalse(value.last_accepted)
        self.assertEqual(value.checked(80), 20)
        self.assertEqual(value.checked(80), 80)
        self.assertTrue(value.last_accepted)


if __name__ == "__main__":
    unittest.main()
