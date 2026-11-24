"""Regression tests for the local robustness changes (no hardware required)."""
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import c_tcpserver
import mqtt_util
import optolinkvs2


class TcpTests(unittest.TestCase):
    def test_fragmented_and_multiple_frames(self):
        server = c_tcpserver.TcpServer("localhost", 0, legacy_idle=None)
        server.client_socket = Mock()
        server.client_socket.recv.side_effect = [b"read;0x", b"0804;1\nread;0x0805;1\n", b""]
        server._listen()
        self.assertEqual(server.get_request(), "read;0x0804;1")
        self.assertEqual(server.get_request(), "read;0x0805;1")
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


if __name__ == "__main__":
    unittest.main()
