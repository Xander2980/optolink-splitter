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
        ser.read_all.side_effect = [b"\\x01", b"\\x02"]
        with patch.object(optolinkvs1, "sync_elapsed", return_value=False), patch.object(utils, "comm_error"):
            code, addr, data = optolinkvs1.read_datapoint_ext(0x0804, 2, ser)
        self.assertEqual((code, addr, data), (1, 0x0804, bytearray(b"\\x01\\x02")))
        ser.write.assert_called_once_with(bytearray([0xf7, 0x08, 0x04, 2]))

    def test_failed_sync_does_not_send_request(self):
        ser = Mock()
        with patch.object(optolinkvs1, "sync_elapsed", return_value=True), patch.object(
                optolinkvs1, "re_init", return_value=False), patch.object(utils, "comm_error"):
            self.assertEqual(optolinkvs1.read_datapoint_ext(0x0804, 2, ser)[0], 0xFF)
        ser.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
