'''
   Copyright 2024 philippoo66
   
   Licensed under the GNU GENERAL PUBLIC LICENSE, Version 3 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       https://www.gnu.org/licenses/gpl-3.0.html

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
'''

###########################################
##  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  ##     
##  ATTENTION! DO NOT EDIT THIS MODULE!  ##
##  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~  ##     
###########################################

import importlib
import math
#from logger_util import logger  # circular import


SETTINGS_MODULE = "settings_ini"

class SettingsAdapter:
    def __init__(self):
        self._settings_obj = None

        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        #  Here we define all the settings and their default values 
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        # These settings might get overwritten later by
        # - settings.setting = value
        # - calling set_settings("another_module")

        # Serial Ports +++++++++++++++++++
        self.port_optolink = '/dev/ttyUSB0'     # Serial port for Optolink device (mandatory, default: '/dev/ttyUSB0')
        self.port_vitoconnect = None            # Serial port for Vitoconnect (optional, default: '/dev/ttyAMA0', set None if no Vitoconnect) Pls check https://github.com/philippoo66/optolink-splitter/wiki/520-termios.error:-(22,-'Invalid-argument')
        self.vs2timeout = 120                   # Timeout (seconds) for VS2 protocol detection with Vitoconnect (default: 120)
        self.vs1protocol = False                # if True, VS1/KW protocol used

        # MQTT Connection ++++++++++++++++
        self.mqtt_broker = None                 # MQTT broker address (default: "192.168.0.123:1883", set None to disable MQTT)
        self.mqtt_user = None                   # MQTT user credentials: "meuser:mypwd" (default: None for anonymous access)
        self.mqtt_tls_enable = False            # True = connect via TLS (MQTT over TLS) (default: False)
        self.mqtt_tls_skip_verify = False       # Disables TLS cert + hostname verification (INSECURE; self-signed / lab only; Default = False)
        self.mqtt_tls_ca_certs = None           # MQTT TLS CA bundle path (default: None to use OS CA store)
        self.mqtt_tls_certfile =  None          # MQTT TLS client cert (mTLS) path (default: None for no client cert)
        self.mqtt_tls_keyfile  = None           # MQTT TLS client key (mTLS) path (default: None for
        self.mqtt_logging = False               # Enable/Disables logging of paho.mqtt (default: False)

        # MQTT Topics ++++++++++++++++++++
        self.mqtt_topic = "Vito"                # MQTT topic for publishing data (default: "Vito")
        self.mqtt_listen = "Vito/cmnd"          # MQTT topic for incoming commands (default: "Vito/cmnd", set None to disable)
        self.mqtt_respond = "Vito/resp"         # MQTT topic for responses (default: "Vito/resp", set None to disable)
        self.mqtt_fstr = "{dpname}"             # Format string for MQTT messages (default: "{dpname}", alternative e.g.: "{dpaddr:04X}_{dpname}")
        self.mqtt_retain = False                # Publish retained messages. Last message per topic is stored on broker and sent to new/reconnecting subscribers. (default: False)
        self.mqtt_no_redundant = False          # if True, no previously published unchanged messages

        # Write protection (haus-fixes) +++
        self.cmnd_write_allowed = True          # False: MQTT/TCP commands write/writeraw/raw/request/fullraw are rejected (only reads allowed; /set stays possible)
        self.set_write_allowlist = None        # None: legacy; empty collection: disable /set
        self.write_limits = {}                  # optional per datapoint name: {'neigung_hk1': (0.2, 3.5), ...} – /set values outside are rejected

        # TCP/IP ++++++++++++++++++++++++++
        self.tcpip_port =  65234                # TCP/IP port for communication (default: 65234, used by Viessdata; set None to disable TCP/IP)
        self.tcp_legacy_idle = 0.1             # None requires newline/NUL framing
        self.tcpip_host = "0.0.0.0"             # interface to bind, e.g. "127.0.0.1" for local only (haus-fixes)
        self.tcp_verbose = False                # TCP verbose logging if True

        # Optolink Communication Timing ++++
        self.fullraw_eot_time =  0.05           # Timeout (seconds) to determine end of telegram (default: 0.05)
        self.fullraw_timeout =  2               # Overall timeout (seconds) for receiving data (default: 2)
        self.olbreath =  0.05                   # Pause (seconds) after a request-response cycle (default: 0.05)

        # Optolink Logging ++++++++++++++
        self.show_opto_rx = True                # Console output of received Optolink data (default: True). Set False when run as a service, otherwise every telegram ends up in the journal
        self.log_optolink = False               # Enable logging of Optolink rx+tx communication (default: False)
        self.log_vitoconnect = False            # Enable logging of Vitoconnect Optolink rx+tx telegram communication (default: False)
        self.viconn_to_mqtt = True              # Vitoconnect traffic published on MQTT

        # Data Formatting +++++++++++++++
        self.max_decimals = 4                   # Max decimal places for float values (default: 4)
        self.data_hex_format = '02x'            # Hexadecimal formatting (set '02X' for uppercase formatting, default: '02x')
        self.resp_addr_format = 'x'             # Format of DP addresses in MQTT/TCPIP request responses ('d' for decimal, e.g. '04X' for 4-digit hex, default: 'x')
        self.retcode_format =  'd'              # Format of return code in MQTT/TCPIP requests ('d' for decimal, e.g. '02X' for 2-digit hex, default: 'd')

        # Viessdata Utilities +++++++++++
        self.write_viessdata_csv =  False       # Enable writing Viessdata to CSV (default: False)
        self.viessdata_csv_path =  ""           # File path for Viessdata CSV output (default: "")
        self.buffer_to_write =  60              # Buffer size before writing CSV (default: 60)
        self.dec_separator =  ","               # Decimal separator for CSV output (default: ",")

        # General Settings +++++++++++
        self.no_logger_file =  False            # if True the optolinksvs2_switch.log will not get written
        self.log_level = 20                     # DEBUG=10, INFO=20, WARNING=30, ERROR=40, CRITICAL=50,
        self.max_restarts = 5                   # re-start limit. counter gets reset after sucessful {retry_counters_reset} minutes successful operation 
        self.usb_reconnect_forever = True
        self.restart_max_delay = 120
        self.poll_read_retries = 1
        self.poll_stale_after = 0
        self.restart_delay = 10                 # seconds delay before re-starting 
        self.max_vicon_tries = 3                # limit for vicon tries. if exceeded, continuing without vitoconnect. should be less than max_restarts
        self.max_comm_errors = 10               # optolink comm error threshold to init restart
        self.retry_counters_reset = 30          # minutes of sucessful operation to reset the retry counters 
        self.readback_delay_set = 1             # seconds delay between wirte via /set and reading back 

        # special for wo1c: read daily/weekly energy statistics +++++++++++
        self.wo1c_energy = 0                    # 0:disabled, €N: every n-th cycle

        # 1-Wire Sensors +++++++++++++++
        self.w1sensors = {}                  

        # Datapoint Polling List +++++++++
        self.poll_interval = 30                 # Polling interval (seconds), 0 for continuous, -1 to disable (default: 30)

        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        #  now we apply given settings from settings_ini.py or any other parser
        self.set_settings(SETTINGS_MODULE)


    def set_settings(self, settings_module:str = '', reload:bool = False) -> bool:
        previous = self.__dict__.copy()
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        #  Here we take the settings from a module if exist there, 
        #  otherwise keep value unchanged 
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        if settings_module:
            try:
                self._settings_obj = importlib.import_module(settings_module)
            except Exception as e:
                print(f"ERROR importing settings module: {e}")
                return False
        
        # if not self._settings_obj:
        #     logger.error("set_settings: no settings object set")
        #     return
        
        if reload:
            try:
                self._settings_obj = importlib.reload(self._settings_obj)
            except Exception as e:
                print(f"ERROR reload settings module: {e}")
                return False


        # General Settings +++++++++++
        # a later change of no_logger_file will not be effective since logger is already up then 
        self.no_logger_file = getattr(self._settings_obj, 'no_logger_file', self.no_logger_file)
        self.log_level = getattr(self._settings_obj, 'log_level', self.log_level)
        self.max_restarts = getattr(self._settings_obj, 'max_restarts', self.max_restarts)
        self.usb_reconnect_forever = getattr(self._settings_obj, 'usb_reconnect_forever', self.usb_reconnect_forever)
        self.restart_max_delay = getattr(self._settings_obj, 'restart_max_delay', self.restart_max_delay)
        self.poll_read_retries = getattr(self._settings_obj, 'poll_read_retries', self.poll_read_retries)
        self.poll_stale_after = getattr(self._settings_obj, 'poll_stale_after', self.poll_stale_after)
        self.restart_delay = getattr(self._settings_obj, 'restart_delay', self.restart_delay)
        self.max_vicon_tries = getattr(self._settings_obj, 'max_vicon_tries', self.max_vicon_tries)
        self.max_comm_errors = getattr(self._settings_obj, 'max_comm_errors', self.max_comm_errors)               
        self.retry_counters_reset = getattr(self._settings_obj, 'retry_counters_reset', self.retry_counters_reset)
        self.readback_delay_set = getattr(self._settings_obj, 'readback_delay_set', self.readback_delay_set)   # haus-fixes: wurde nie eingelesen

        # Serial Ports +++++++++++++++++++
        self.port_optolink = getattr(self._settings_obj, 'port_optolink', self.port_optolink)
        self.port_vitoconnect = getattr(self._settings_obj, 'port_vitoconnect', self.port_vitoconnect)
        self.vs2timeout = getattr(self._settings_obj, 'vs2timeout', self.vs2timeout)
        self.vs1protocol = getattr(self._settings_obj, 'vs1protocol', self.vs1protocol)

        # MQTT Connection ++++++++++++++++
        self.mqtt_broker = getattr(self._settings_obj, 'mqtt', self.mqtt_broker)  # old
        self.mqtt_broker = getattr(self._settings_obj, 'mqtt_broker', self.mqtt_broker)
        self.mqtt_user = getattr(self._settings_obj, 'mqtt_user', self.mqtt_user)
        self.mqtt_tls_enable = getattr(self._settings_obj, 'mqtt_tls_enable', self.mqtt_tls_enable)
        self.mqtt_tls_skip_verify = getattr(self._settings_obj, 'mqtt_tls_skip_verify', self.mqtt_tls_skip_verify)
        self.mqtt_tls_ca_certs = getattr(self._settings_obj, 'mqtt_tls_ca_certs', self.mqtt_tls_ca_certs)
        self.mqtt_tls_certfile = getattr(self._settings_obj, 'mqtt_tls_certfile', self.mqtt_tls_certfile)
        self.mqtt_tls_keyfile  = getattr(self._settings_obj, 'mqtt_tls_keyfile', self.mqtt_tls_keyfile)
        self.mqtt_logging = getattr(self._settings_obj, 'mqtt_logging',  self.mqtt_logging)

        # MQTT Topics ++++++++++++++++++++
        self.mqtt_topic = getattr(self._settings_obj, 'mqtt_topic', self.mqtt_topic)
        self.mqtt_listen = getattr(self._settings_obj, 'mqtt_listen', self.mqtt_listen)
        self.mqtt_respond = getattr(self._settings_obj, 'mqtt_respond', self.mqtt_respond)
        self.mqtt_fstr = getattr(self._settings_obj, 'mqtt_fstr',self.mqtt_fstr)
        self.mqtt_retain = getattr(self._settings_obj, 'mqtt_retain', self.mqtt_retain)
        self.mqtt_no_redundant = getattr(self._settings_obj, 'mqtt_no_redundant', self.mqtt_no_redundant)

        # Write protection (haus-fixes) +++
        self.cmnd_write_allowed = getattr(self._settings_obj, 'cmnd_write_allowed', self.cmnd_write_allowed)
        self.set_write_allowlist = getattr(self._settings_obj, 'set_write_allowlist', self.set_write_allowlist)
        self.write_limits = getattr(self._settings_obj, 'write_limits', self.write_limits)

        # TCP/IP ++++++++++++++++++++++++++
        self.tcpip_port = getattr(self._settings_obj, 'tcpip_port', self.tcpip_port)
        self.tcp_legacy_idle = getattr(self._settings_obj, 'tcp_legacy_idle', self.tcp_legacy_idle)
        self.tcpip_host = getattr(self._settings_obj, 'tcpip_host', self.tcpip_host)
        self.tcp_verbose = getattr(self._settings_obj, 'tcp_verbose', self.tcp_verbose)

        # Optolink Communication Timing ++++
        self.fullraw_eot_time = getattr(self._settings_obj, 'fullraw_eot_time', self.fullraw_eot_time)
        self.fullraw_timeout = getattr(self._settings_obj, 'fullraw_timeout', self.fullraw_timeout)
        self.olbreath = getattr(self._settings_obj, 'olbreath', self.olbreath)

        # Optolink Logging ++++++++++++++
        self.show_opto_rx = getattr(self._settings_obj, 'show_opto_rx', self.show_opto_rx)
        self.log_optolink = getattr(self._settings_obj, 'log_optolink', self.log_optolink)
        self.log_vitoconnect = getattr(self._settings_obj, 'log_vitoconnect', self.log_vitoconnect)
        self.viconn_to_mqtt = getattr(self._settings_obj, 'viconn_to_mqtt', self.viconn_to_mqtt)

        # Data Formatting +++++++++++++++
        self.max_decimals = getattr(self._settings_obj, 'max_decimals', self.max_decimals)
        self.data_hex_format = getattr(self._settings_obj, 'data_hex_format', self.data_hex_format)
        self.resp_addr_format = getattr(self._settings_obj, 'resp_addr_format', self.resp_addr_format)
        self.retcode_format = getattr(self._settings_obj, 'retcode_format', self.retcode_format)

        # Viessdata Utilities +++++++++++
        self.write_viessdata_csv = getattr(self._settings_obj, 'write_viessdata_csv', self.write_viessdata_csv)
        self.viessdata_csv_path = getattr(self._settings_obj, 'viessdata_csv_path', self.viessdata_csv_path)
        self.buffer_to_write = getattr(self._settings_obj, 'buffer_to_write', self.buffer_to_write)
        self.dec_separator = getattr(self._settings_obj, 'dec_separator', self.dec_separator)

        # special for wo1c: read daily/weekly energy statistics +++++++++++
        self.wo1c_energy = getattr(self._settings_obj, 'wo1c_energy', self.wo1c_energy)

        # 1-Wire Sensors +++++++++++++++
        self.w1sensors = getattr(self._settings_obj, 'w1sensors', self.w1sensors)

        # Datapoint Polling Interval +++++++++
        foreign_interval = getattr(self._settings_obj, 'poll_interval', None)
        if foreign_interval is not None:
            self.poll_interval = foreign_interval
        try:
            self.validate()
        except ValueError:
            self.__dict__.update(previous)
            raise
        return True

    def validate(self) -> None:
        def number(name, minimum, integer=False):
            value = getattr(self, name)
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or value < minimum or (integer and type(value) is not int)):
                raise ValueError(f"Invalid {name}: {value!r}")
        for name in ("vs2timeout", "fullraw_timeout", "fullraw_eot_time", "restart_delay", "restart_max_delay"):
            number(name, 0.001)
        for name in ("olbreath", "readback_delay_set", "poll_stale_after", "retry_counters_reset"):
            number(name, 0)
        for name in ("max_restarts", "max_vicon_tries", "poll_read_retries", "wo1c_energy", "max_decimals"):
            number(name, 0, True)
        for name in ("max_comm_errors", "buffer_to_write"):
            number(name, 1, True)
        number("poll_interval", -1, True)
        if self.poll_read_retries > 3:
            raise ValueError("poll_read_retries must be 0..3")
        if self.restart_max_delay < self.restart_delay:
            raise ValueError("restart_max_delay must be >= restart_delay")
        if self.fullraw_eot_time > self.fullraw_timeout:
            raise ValueError("fullraw_eot_time exceeds fullraw_timeout")
        if self.tcp_legacy_idle is not None:
            number("tcp_legacy_idle", 0.001)
        if self.tcpip_port is not None and (type(self.tcpip_port) is not int or not 1 <= self.tcpip_port <= 65535):
            raise ValueError("tcpip_port must be None or 1..65535")
        if not isinstance(self.tcpip_host, str) or not isinstance(self.port_optolink, str) or not self.port_optolink:
            raise ValueError("Invalid serial port or TCP bind host")
        if self.port_vitoconnect is not None and (not isinstance(self.port_vitoconnect, str) or not self.port_vitoconnect):
            raise ValueError("Invalid port_vitoconnect")
        if self.mqtt_broker is not None:
            parse_broker(self.mqtt_broker)
            if not isinstance(self.mqtt_topic, str) or not self.mqtt_topic or any(c in self.mqtt_topic for c in "#+\0"):
                raise ValueError("Invalid MQTT topic")
        if (self.mqtt_tls_certfile is None) != (self.mqtt_tls_keyfile is None):
            raise ValueError("MQTT client certificate and key must be configured together")
        if self.set_write_allowlist is not None and (not isinstance(self.set_write_allowlist, (set, frozenset, list, tuple)) or not all(isinstance(x, str) for x in self.set_write_allowlist)):
            raise ValueError("set_write_allowlist must be a collection of names or None")
        if not isinstance(self.write_limits, dict):
            raise ValueError("write_limits must be a dictionary")
        for name, limits in self.write_limits.items():
            if not isinstance(name, str) or not isinstance(limits, (tuple, list)) or len(limits) != 2:
                raise ValueError("Invalid write_limits entry")
            lo, hi = limits
            if any(x is not None and (type(x) not in (int, float) or not math.isfinite(x)) for x in limits):
                raise ValueError(f"Invalid write limits for {name}")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"Reversed write limits for {name}")
        for name in ("usb_reconnect_forever", "cmnd_write_allowed", "vs1protocol", "mqtt_retain", "mqtt_no_redundant", "mqtt_tls_enable", "mqtt_tls_skip_verify"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")


def parse_broker(value: str) -> tuple[str, int]:
    if not isinstance(value, str):
        raise ValueError("mqtt_broker must be host:port")
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("mqtt_broker must be host:port")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host, int(port)


# === for global use ==================
settings = SettingsAdapter()