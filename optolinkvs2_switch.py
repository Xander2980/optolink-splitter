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

VERSION = "1.11.6.0"

import serial
import time
import threading
import importlib
import json
import signal
from dataclasses import dataclass, field
from typing import Any, Optional
from runtime_support import MeasurementTracker, reconnect_delay, freshness_deadline, valid_measurement

from c_settings_adapter import settings  # to be done first to get logger settings
from logger_util import logger
import vs12_adapter
import viconn_util
import viessdata_util
import c_tcpserver
import requests_util
from c_logging import viconnlog
from c_polllist import poll_list
import utils
import wo1c_energy
import c_LoggingSerial

@dataclass
class RuntimeState:
    mqtt: Any = None
    tcp: Any = None
    splitter_started: float = field(default_factory=time.time)
    last_vs1_comm: float = 0.0
    force_poll: bool = False
    reload_poll: bool = False
    num_vicon_tries: int = 0
    num_restarts: int = 0
    poll_pointer: int = 0
    poll_cycle: int = 0
    manual_poll: bool = False
    optolink_ready: bool = False
    measurements: MeasurementTracker = field(default_factory=MeasurementTracker)


state = RuntimeState()


def mqtt_connected() -> bool:
    return bool(state.mqtt is not None and state.mqtt.mqtt_client is not None
                and state.mqtt.mqtt_client.is_connected())


def record_measurement(name: str, addr: int, value: Any) -> bool:
    if not valid_measurement(value):
        state.measurements.invalidate(name)
        return False
    was_valid = state.measurements.values.get(name)
    force = was_valid is None or not was_valid.valid
    state.measurements.observe(name, value, time.monotonic(), time.time())
    if state.mqtt is not None:
        state.mqtt.publish_read(name, addr, value, force=force)
    return True


def quality_tick(now: float) -> None:
    deadlines = {
        item[1]: freshness_deadline(settings.poll_interval, poll_list.num_items,
                                    poll_list.cycle_groups[item[0]], settings.poll_stale_after)
        for item in poll_list.items
    }
    state.measurements.expire(now, deadlines)
    if mqtt_connected():
        for name in list(state.measurements.dirty)[:64]:
            if state.mqtt.publish_quality(name, state.measurements.values[name]):
                state.measurements.mark_sent(name, now)


def do_poll_item(poll_data: list, ser: serial.Serial, item_index: Optional[int] = None) -> int:  # retcode      # type: ignore
    # set item_index to force poll
    val = "?"
    item = "?"

#    print("do_poll_item",item_index)
    try:
        # handle PollCycle option +++++++++++++++++++++++
        # loop though poll items until find one to be done this cycle
        while(True):
            list_index = item_index if item_index is not None else state.poll_pointer
            item = poll_list.items[list_index]  # (PollCycleGroupKey, Name, DpAddr, Len, Scale/Type, Signed)
            item_cycle = poll_list.cycle_groups[item[0]]

            if(item_index is None) and ((item_cycle < 0) or ((item_cycle > 0) and (state.poll_cycle % item_cycle != 0)) or ((item_cycle == 0) and (state.poll_cycle != 0))):
                # ---------------------------------------
                # +++ do NOT poll this item this time +++
                # ---------------------------------------

                # leave poll_data[state.poll_pointer] unchanged

                state.poll_pointer += 1
                if(state.poll_pointer == poll_list.num_items):
                    # no further item this cycle
                    return 0xAB

            else:
                # remove PollCycleGroupKey for further processing
                item = item[1:]
                break

        for attempt in range(settings.poll_read_retries + 1):
            retcode, data, val, _ = requests_util.response_to_request(item, ser)
            if retcode == 0xAA:
                raise serial.SerialException("Optolink USB connection lost")
            if (retcode not in (0x15, 0x20, 0x41, 0xFC, 0xFD, 0xFE, 0xFF)
                    or item[1] in settings.w1sensors or attempt == settings.poll_read_retries
                    or utils.restart_event.is_set() or utils.shutdown_event.is_set()
                    or viconn_util.has_pending_requests()):
                break
            olbreath(retcode)

        if(retcode == 0x01):
            if not record_measurement(item[0], item[1], val):
                poll_data[list_index] = None
                return 0xFB
            poll_data[list_index] = val

            # probably more bytebit values of the same datapoint?!
            if(len(item) > 3):
                if(str(item[3]).lower().startswith('b:')):
                    # bytebit filter +++++++++
                    while((list_index + 1) < poll_list.num_items):
                        list_index += 1
                        next_item = poll_list.items[list_index]
                        #next_item_cycle = poll_list.cycle_groups[next_item[0]]
                        # remove PollCycleGroupKey
                        next_item = next_item[1:]
                        
                        # if next address same AND next len same AND next type starts with 'b:'
                        if((len(next_item) > 3) and (next_item[1] == item[1]) and (next_item[2] == item[2]) and (str(next_item[3]).lower()).startswith('b:')):
                            next_val = requests_util.perform_bytebit_filter_and_evaluate(data, next_item)

                            poll_data[list_index] = next_val if record_measurement(next_item[0], next_item[1], next_val) else None

                            if item_index is None:   # haus-fixes: forced readback darf den Poll-Durchlauf nicht verschieben
                                state.poll_pointer = list_index
                        else:
                            break
        else:
            state.measurements.invalidate(item[0])
            poll_data[list_index] = None
            logger.error(f"OL Error do_poll_item {state.poll_pointer}, Addr {item[1]:04X}, RetCode {retcode}, Data {val}")
        return retcode
    except (serial.SerialException, OSError) as e:
        # Schnittstellenfehler -> Re-Start wie bisher
        logger.error(f"Error do_poll_item {state.poll_pointer}, {item}: {e}")
        raise
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, ArithmeticError) as e:
        # haus-fixes: Auswertefehler eines einzelnen Items (Format, Laenge, ...) nur loggen und Item ueberspringen
        if isinstance(item, tuple) and item:
            state.measurements.invalidate(item[0])
            if 'list_index' in locals() and 0 <= list_index < len(poll_data):
                poll_data[list_index] = None
        logger.error(f"Error evaluating poll item {state.poll_pointer}, {item}: {e} - skipped")
        return 0xFB


# poll timing is managed by the main loop (monotonic clock, no timer thread).

def olbreath(retcode: int) -> None:
    """
    give vitotronic some time between comms to do other things
    """
    if(retcode <= 0x03):
        # success, err msg
        if(settings.vs1protocol):
            state.last_vs1_comm = time.monotonic()
            vs12_adapter.reset_vs1sync()
        time.sleep(settings.olbreath)
    elif(retcode in [0xFF, 0xAA, 0xAB]):
        # timeout, err_handle, final item skipped in cycle
        pass
    else:
        if(settings.vs1protocol):
            state.last_vs1_comm = time.monotonic()
            vs12_adapter.reset_vs1sync()
        # allow calming down
        time.sleep(2 * settings.olbreath)


# Vicon listener +++++++++++++++++++++++++++++
def vicon_thread_func(serViCon: serial.Serial, serViDev: serial.Serial) -> None:
    """
    Thread to receive Vitoconnect requests
    """
    print("running Vitoconnect listener")
    try:
        callback = mqtt_publ_viconn if settings.viconn_to_mqtt else None
        viconn_util.listen_to_Vitoconnect(serViCon, callback)
    except Exception as e:
        if viconn_util.exit_flag:
            return
        msg = f"Error in listen_to_Vitoconnect: {e}"
        viconnlog.do_log(msg)
        logger.error(f"{msg} -> re-init")
        mqtt_publ_debug(msg)
        viconn_util.exit_flag = True
        utils.restart_event.set()  # Hauptprogramm signalisiert, dass ein Neustart noetig ist
        return  # Thread wird beendet


# TCP loop +++++++++++++++++++++++++++++
def tcp_connection_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        session = None
        try:
            session = c_tcpserver.TcpServer(
                settings.tcpip_host, settings.tcpip_port,
                settings.tcp_verbose, settings.tcp_legacy_idle)
            session.command_callback = do_special_command
            state.tcp = session
            if not stop_event.is_set():
                session.run()
        except (OSError, ValueError):
            if not stop_event.is_set():
                logger.exception("TCP server failed")
        finally:
            if session is not None:
                session.stop()
            state.tcp = None
        stop_event.wait(1)


# utils +++++++++++++++++++++++++++++
def do_special_command(cmnd:str, source:int=1) -> bool:  # source: 1:MQTT, 2:TCP, 0:no response

    try:
        resp =  f"{cmnd} failed"
        cmnd = cmnd.replace(" ", "")
        if not cmnd: 
            return False
        parts = cmnd.split(";")
        #print("do_special_command",cmnd)
        if parts[0] in ('reset', 'resetrecent'):
            if(state.mqtt is not None):
                state.mqtt.reset_recent_list()
                resp = f"recent list cleared"
        elif parts[0] in ('forcepoll',):
            state.force_poll = True
            resp = f"{parts[0]} triggered"
        elif parts[0] in ('reloadpoll',):   
            state.reload_poll = True
            resp = f"{parts[0]} triggered"
        elif parts[0] in ('stats', 'getstats'):   
            resp = get_stats()
        elif parts[0] in ("exit", "resettcp"):
            if state.tcp:
                state.tcp.stop()
                resp = f"{parts[0]} triggered" if source != 2 else ''
        elif parts[0] in ("flushcsv",):
            if settings.write_viessdata_csv:
                viessdata_util.buffer_csv_line([], True)
                resp = f"{parts[0]} triggered"
        elif parts[0] in ("reini", "reloadini"):
            # some changes (like ser ports) will not take effect...
            transport_keys = ("port_optolink", "port_vitoconnect", "vs1protocol",
                              "mqtt_broker", "mqtt_user", "mqtt_topic", "mqtt_listen",
                              "mqtt_tls_enable", "mqtt_tls_skip_verify", "mqtt_tls_ca_certs",
                              "mqtt_tls_certfile", "mqtt_tls_keyfile", "tcpip_port",
                              "tcpip_host", "tcp_legacy_idle")
            previous = tuple(getattr(settings, key) for key in transport_keys)
            if not settings.set_settings(reload=True):
                raise ValueError("Settings reload failed")
            if previous != tuple(getattr(settings, key) for key in transport_keys):
                utils.restart_event.set()
            resp = "ini settings reloaded"
        elif parts[0] in ("setpollcycle", "setcycle"):
            if poll_list.set_pollcycle(parts[1], parts[2]):
                resp = f"cycle_group {parts[1]} set to {parts[2]}"
        elif parts[0] in ("setpollinterval", "setinterval"):
            interval = int(parts[1])
            if interval < -1:
                raise ValueError("poll interval must be >= -1")
            settings.poll_interval = interval
            resp = f"poll_interval set to {parts[1]}"
        else:
            return False
    except Exception as e:
        resp = str(e)
    # responde
    if(resp):
        if(source == 1):
            if(state.mqtt):
                state.mqtt.publish_response(resp)
        elif(source == 2):
            if(state.tcp):
                state.tcp.send(resp)
    return True


def publish_stats():
    if mqtt_connected():
        topic = settings.mqtt_topic + "/stats"
        msg = get_stats()
        state.mqtt.publish_smart(topic, msg)

def get_stats() -> str:
    jdata = {"Splitter Version" : VERSION,
            "Splitter started" : str(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.splitter_started))),
            "Settings Make" : str(utils.get_module_modified_datetime(settings._settings_obj)) if settings._settings_obj else "0", 
            "Poll List Make" : str(poll_list.module_date),
            "Poll List Items" : str(poll_list.num_items),
            "MQTT Connected": mqtt_connected(),
            "Optolink Ready": state.optolink_ready,
            "Communication Errors": utils.comm_errors}
    return json.dumps(jdata)


def mqtt_publ_debug(msg:str):
    if mqtt_connected():
        state.mqtt.mqtt_client.publish(settings.mqtt_topic + "/debug", msg)  


# MQTT publish callback VS2 +++++++++++++++++++++++++++++
def mqtt_publ_viconn(retcd, addr, data, msgid, msqn, fctcd, dlen):
    if mqtt_connected():
        if addr:
            topic = settings.mqtt_topic + f"/viconn/{addr:04X}/{get_msgid(msgid)}"
            jdata = {"retcode" : get_retcode(retcd),
                    "fctcode" : get_fctcode(fctcd),
                    "datalen" : dlen,
                    "data" : f"0x{utils.arr2hexstr(data)}" if data else "none"}
            state.mqtt.publish_smart(topic, json.dumps(jdata))

def get_msgid(val):
    if(val == 0): return "Vicon"
    elif(val in (1, 3)): return "Opto"
    else: return f"0x{val:02X}"

def get_retcode(val):
    strg = dicRetCodes.get(val) 
    if strg: return strg
    else: return f"0x{val:02X}"

def get_fctcode(val):
    strg = dicFunctionCodes.get(val) 
    if strg: return strg
    else: return f"{val}"

dicRetCodes = {
    0x01 : "success", 
    0x03 : "ErrMsg", 
    0x15 : "NACK", 
    0x20 : "UnknB0_Err", 
    0x41 : "STX_Err", 
    0xAA : "HandleLost", 
    0xFD : "PlLen_Err", 
    0xFE : "CRC_Err", 
    0xFF : "TimeOut"
}

dicFunctionCodes = {
    0 : "undefined",
    1 : "Virtual_READ",
    2 : "Virtual_WRITE",
    3 : "Physical_READ",
    4 : "Physical_WRITE",
    5 : "EEPROM_READ",
    6 : "EEPROM_WRITE",
    7 : "Remote_Procedure_Call",
    # 5 bits!?
    # 33 : "Virtual_MBUS",
    # 34 : "Virtual_MarktManager_READ",
    # 35 : "Virtual_MarktManager_WRITE",
    # 36 : "Virtual_WILO_READ",
    # 37 : "Virtual_WILO_WRITE",
    # 49 : "XRAM_READ",
    # 50 : "XRAM_WRITE",
    # 51 : "Port_READ",
    # 52 : "Port_WRITE",
    # 53 : "BE_READ",
    # 54 : "BE_WRITE",
    # 65 : "KMBUS_RAM_READ",
    # 67 : "KMBUS_EEPROM_READ",
    # 81 : "KBUS_DATAELEMENT_READ",
    # 82 : "KBUS_DATAELEMENT_WRITE",
    # 83 : "KBUS_DATABLOCK_READ",
    # 84 : "KBUS_DATABLOCK_WRITE",
    # 85 : "KBUS_TRANSPARENT_READ",
    # 86 : "KBUS_TRANSPARENT_WRITE",
    # 87 : "KBUS_INITIALISATION_READ",
    # 88 : "KBUS_INITIALISATION_WRITE",
    # 89 : "KBUS_EEPROM_LT_READ",
    # 90 : "KBUS_EEPROM_LT_WRITE",
    # 91 : "KBUS_CONTROL_WRITE",
    # 93 : "KBUS_MEMBERLIST_READ",
    # 94 : "KBUS_MEMBERLIST_WRITE",
    # 95 : "KBUS_VIRTUAL_READ",
    # 96 : "KBUS_VIRTUAL_WRITE",
    # 97 : "KBUS_DIRECT_READ",
    # 98 : "KBUS_DIRECT_WRITE",
    # 99 : "KBUS_INDIRECT_READ",
    # 100 : "KBUS_INDIRECT_WRITE",
    # 101 : "KBUS_GATEWAY_READ",
    # 102 : "KBUS_GATEWAY_WRITE",
    # 120 : "PROZESS_WRITE",
    # 123 : "PROZESS_READ",
    # 180 : "OT_Physical_Read",
    # 181 : "OT_Virtual_Read",
    # 182 : "OT_Physical_Write",
    # 183 : "OT_Virtual_Write",
    # 201 : "GFA_READ",
    # 202 : "GFA_WRITE",
}

# MQTT publish callback VS1 +++++++++++++++++++++++++++++

# digs = len(str(settings.viconnVS1_ringbuff))
# rb_pointer = 0
def mqtt_publ_viconnVS1(data, request:bool):
    if mqtt_connected():
        topic = settings.mqtt_topic + f"/viconn/{'Vicon' if request else 'Opto'}"
        state.mqtt.publish_smart(topic, utils.bbbstr(data))

def get_fctcodeVS1(val):
    strg = dicFunctionCodesVS1.get(val) 
    if strg: return strg
    else: return f"0x{val:02X}"

dicFunctionCodesVS1 = {
    0xF4 : "Virtuell_Write", 
    0xF7 : "Virtuell_Read",
    0x68 : "GFA_Write", 
    0x6B : "GFA_Read", 
    0x78 : "PROZESS_WRITE", 
    0x7B : "PROZESS_READ" 
}


# signal handling
def handle_exit(sig, frame):
    logger.info(f"received signal {sig}")
    utils.shutdown_event.set()
    utils.restart_event.set()
    utils.work_event.set()
    #os._exit(0)


# ------------------------
# Main
# ------------------------
def main() -> None:

    settings.validate()
    # Signale abfangen fuer sauberes Beenden
    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)


    # some variables ++++++
    serOptolink = None  # Viessmann Device (Slave)
    serVitoConnnect = None  # Vitoconnect (Master)

    excptn = None
    first_time = True
    reset_retry_counters_time = -1
    usb_failure = False
    non_usb_failures = 0
    worker_failed = False
    tcp_thread = None
    vicon_thread = None
    tcp_stop_event = threading.Event()

    # === the method for a clean exit =============================
    def close_everything():
        """
        sauber beenden: Tasks stoppen, VS1 Protokoll aktivieren(?), alle Verbindungen trennen        
        """
        nonlocal serOptolink, serVitoConnnect, excptn, worker_failed
        
        # Schliessen der seriellen Schnittstellen, Ausgabedatei, PollTimer, 
        logger.info("exit close...")
        state.optolink_ready = False
        state.measurements.invalidate_all()
        if state.mqtt is not None:
            try:
                state.mqtt.set_optolink_ready(False)
                quality_tick(time.monotonic())
            except Exception:
                logger.exception("publishing offline state")
        tcp_stop_event.set()
        viconn_util.exit_flag = True
        session = state.tcp
        if session is not None:
            session.stop()
        # Closing the ports interrupts a blocked listener. Always join the old
        # threads before a new session can reset their stop flags.
        if serVitoConnnect is not None:
            try:
                serVitoConnnect.close()
            except Exception:
                logger.exception("closing Vitoconnect")
            serVitoConnnect = None
        for worker in (tcp_thread, vicon_thread):
            if worker is not None:
                worker.join(timeout=5)
                if worker.is_alive():
                    logger.error("Worker did not stop; refusing in-process restart")
                    worker_failed = True
        if serOptolink is not None:
            try:
                if serOptolink.is_open and not isinstance(excptn, OSError):
                    serOptolink.write(bytes([0x04]))
            except Exception:
                logger.exception("reset Optolink protocol")
            finally:
                try:
                    serOptolink.close()
                except Exception:
                    logger.exception("closing Optolink")
                serOptolink = None
        if state.mqtt is not None:
            try:
                state.mqtt.exit_mqtt()
            except Exception:
                logger.exception("closing MQTT")
            finally:
                state.mqtt = None
        if settings.write_viessdata_csv:
            viessdata_util.buffer_csv_line([], True)
        if(viconnlog.log_handle is not None):
            logger.info("closing viconnlog")
            viconnlog.close_log()


    logger.info(f"Version {VERSION} starting...")

    # ++++++++++++++++++++
    # the re-start loop
    # ++++++++++++++++++++      
    while not utils.shutdown_event.is_set():
        tcp_thread = None
        vicon_thread = None
        tcp_stop_event = threading.Event()
        try:
        #if True:

            if first_time:
                # Fail fast on a missing or malformed initial poll list.
                # ---------------------
                # init the poll list 
                # ---------------------
                if not poll_list.make_list():
                    raise SystemExit("Initial poll list could not be loaded")
                state.measurements.configure(poll_list.datapoint_metadata)
                # buffer for read data for writing viessdata.csv 
                poll_data = [None] * poll_list.num_items

                first_time = False
            else:
                # === this is a re-start ======
                state.num_restarts += 1
                if state.num_vicon_tries > settings.max_vicon_tries:
                    # continue without vitoconnect 
                    settings.port_vitoconnect = None
                utils.restart_event.clear()
                utils.reset_comm_errors()
                state.poll_pointer = 0
                state.poll_cycle = 0
                state.manual_poll = False
                poll_data = [None] * poll_list.num_items
                state.measurements.configure(poll_list.datapoint_metadata)
                excptn = None
                #reset_retry_counters_in(settings.retry_counters_reset)
                reset_retry_counters_time = time.monotonic() + (60 * settings.retry_counters_reset)
                logger.warning(f"re-start #{state.num_restarts}")

            usb_failure = False
            # ---------------------
            # open serial ports 
            # ---------------------

            # serielle Verbindungen mit dem Optolink Kopf oeffnen ++++++++++++++
            if(settings.port_optolink is not None):
                serial_args = dict(
                    url=settings.port_optolink,
                    baudrate=4800,
                    parity=serial.PARITY_EVEN,
                    stopbits=serial.STOPBITS_TWO,
                    bytesize=serial.EIGHTBITS,
                    exclusive=True,
                    timeout=0,
                    write_timeout=3,
                )

                if settings.log_optolink:
                    logger.info("Optolink using LoggingSerial")
                    serOptolink = c_LoggingSerial.LoggingSerial(**serial_args,
                            # specials
                            logger_name="optolink",
                            logger_fmt="%(relativeCreated)d: %(message)s",
                            logger_no_console = True,
                            logger_max_bytes = 25 * 1024 * 1024  # 25 MB
                        )
                else:
                    serOptolink = serial.serial_for_url(**serial_args)      # type: ignore

                # open went fine
                logger.info(f"Optolink serial port opened on {settings.port_optolink}")
            else:
                utils.shutdown_event.set()
                raise Exception("ERROR: Optolink device is mandatory!")

            # serielle Verbindungen mit dem Vitoconnect oeffnen ++++++++++++++
            if(settings.port_vitoconnect is not None):
                serVitoConnnect = serial.serial_for_url(
                            url=settings.port_vitoconnect,
                            baudrate=4800,
                            parity=serial.PARITY_EVEN,
                            stopbits=serial.STOPBITS_TWO,
                            bytesize=serial.EIGHTBITS,
                            exclusive=True,
                            timeout=0, write_timeout=3)
                # open went fine
                logger.info(f"Vitoconnect serial port opened on {settings.port_vitoconnect}")

            # -------------------------------------------------------------
            # run the receive tasks of 'secondary masters' (MQTT, TcpIp) 
            # -------------------------------------------------------------

            # MQTT --------
            state.mqtt = None
            if(settings.mqtt_broker is not None):
                # avoid paho.mqtt required if not used
                state.mqtt = importlib.import_module("mqtt_util")
                state.mqtt.connect_mqtt()
                state.mqtt.command_callback = do_special_command      # type: ignore


            # TCP/IP connection --------
            if(settings.tcpip_port is not None):
                tcp_thread = threading.Thread(target=tcp_connection_loop, args=(tcp_stop_event,), daemon=True)
                tcp_thread.start()

            # ---------------------
            # some inits 
            # ---------------------

            # one wire value check init
            requests_util.init_w1_values_check()

            # publish viconn or not
            vicon_publ_callback = mqtt_publ_viconn if settings.viconn_to_mqtt else None

            # show what we have
            publish_stats()

            # ----------------------
            # run VS2 connection 
            # ----------------------

            if(serVitoConnnect is not None):
                state.num_vicon_tries += 1

                # reset vicon_request buffer
                viconn_util.clear_requests()

                # Vitoconncet logging
                if(settings.log_vitoconnect):
                    if(viconnlog.log_handle is None):
                        viconnlog.open_log()

                # detect/init Protokol ++++++++++++
                logger.info("awaiting Vitoconnect being operational...")
                if not vs12_adapter.wait_for_vicon(serVitoConnnect, serOptolink, settings.vs2timeout):      # type: ignore
                    raise Exception("Vitoconnect not detected operational within timeout")
                msg = "Vitoconnect detected operational"
                viconnlog.do_log(msg)           
                logger.info(msg)

                # listen to vicon ++++++++++++
                # run reception thread
                
                viconn_util.exit_flag = False
                vicon_thread = threading.Thread(target=vicon_thread_func, args=(serVitoConnnect, serOptolink), daemon=True)
                vicon_thread.start()

            else:
                # Protokoll/Kommunikation am Slave initialisieren
                spr = "VS2/300" if not settings.vs1protocol else "VS1/KW"
                if(not vs12_adapter.init_protocol(serOptolink)):        # type: ignore
                    raise Exception(f"init_protocol {spr} failed")  # schlecht fuer KW Protokoll
                logger.info(f"{spr} protocol initialized")


            if utils.shutdown_event.is_set():
                break
            state.optolink_ready = True
            if state.mqtt is not None:
                state.mqtt.set_optolink_ready(True)
            # Preserve periodic cycle semantics without recurring Timer threads.
            poll_interval = settings.poll_interval
            next_poll_at = time.monotonic() + max(0, poll_interval)
            next_quality_at = 0.0
            mqtt_generation = -1

            # main loop initialisieren --------
            num_tasks = 3
            request_pointer = 0
            #tprev = int(time.time()*10000)
            logger.info("enter main loop")

            # +++++++++++++++++++++++++++            
            # main loop - Sachen abarbeiten 
            # +++++++++++++++++++++++++++            
            while not utils.restart_event.is_set() and not utils.shutdown_event.is_set():
                utils.work_event.clear()
                now = time.monotonic()
                if state.mqtt is not None and mqtt_generation != state.mqtt.connection_generation:
                    mqtt_generation = state.mqtt.connection_generation
                    state.measurements.invalidate_all()
                    state.measurements.republish()
                    # Refresh all values, including once-only items; do not mark stale values online.
                    state.poll_pointer = 0
                    state.poll_cycle = 0
                if now >= next_quality_at:
                    quality_tick(now)
                    next_quality_at = now + 1.0
                if settings.poll_interval != poll_interval:
                    poll_interval = settings.poll_interval
                    next_poll_at = now + max(0, poll_interval)
                if poll_interval > 0 and now >= next_poll_at:
                    if state.poll_pointer > poll_list.num_items:
                        state.poll_pointer = 0
                    next_poll_at = now + poll_interval
                elif poll_interval == 0 and state.poll_pointer > poll_list.num_items:
                    state.poll_pointer = 0
                # inits
                did_vicon_request = False
                did_secodary_request = False
                retcode = 1
                is_on = request_pointer

                ### first Vitoconnect request -------------------
                if(serVitoConnnect is not None):
                    vidata = viconn_util.get_vicon_request()
                    if(vidata):
                        serOptolink.reset_input_buffer()
                        serOptolink.write(vidata)
                        viconnlog.do_log(vidata, "M")
                        # recive response an pass bytes directly back to VitoConnect, 
                        # returns when response is complete (or error or timeout) 
                        retcode, _, redata = vs12_adapter.receive_telegr(True, True, serOptolink, serVitoConnnect, vicon_publ_callback)     # type: ignore
                        if retcode == 0xAA:
                            raise serial.SerialException("Optolink USB connection lost")
                        viconnlog.do_log(redata, f"S {retcode:02x}")
                        olbreath(retcode)
                        did_vicon_request = True

                ### secondary requests ------------------
                #TODO ueberlegen/testen, ob Vitoconnect request nicht auch in der Reihe reicht
                
                for i in range(num_tasks):
                    is_on = (request_pointer + i) % num_tasks
                    #print(f"{((tnow := int(time.time()*10000)) - tprev)} io {is_on}"); tprev = tnow

                    # polling list --------
                    if(is_on == 0):
                        if(poll_list.items or state.reload_poll):
                            # === action commands =================
                            # force poll including onceonlies
                            if state.force_poll:
                                state.manual_poll = True
                                state.poll_pointer = 0
                                state.poll_cycle = 0
                                state.force_poll = False
                                if(state.mqtt): 
                                    state.mqtt.lst_force_refresh = []     # type: ignore
                            # reload poll list, including onceonlies
                            if state.reload_poll:
                                if state.mqtt:
                                    state.mqtt.clear_pending_requests()
                                if poll_list.make_list(reload=True):
                                    state.measurements.configure(poll_list.datapoint_metadata)
                                    poll_data = [None] * poll_list.num_items
                                else:
                                    logger.error("Poll reload rejected; retaining previous list")
                                publish_stats()
                                state.poll_pointer = 0
                                state.poll_cycle = 0
                                state.reload_poll = False
                                if(state.mqtt): 
                                    state.mqtt.lst_force_refresh = []     # type: ignore

                            # === check if something is forced =================
                            if state.mqtt and ((force_refresh_index := state.mqtt.is_forced()) is not None):
                                retcode = do_poll_item(poll_data, serOptolink, item_index=force_refresh_index)      # type: ignore
                                # we did something
                                did_secodary_request = True

                            # === else do common poll if is on =================
                            elif((settings.poll_interval >= 0 or state.manual_poll) and 0 <= state.poll_pointer < poll_list.num_items):
                                retcode = do_poll_item(poll_data, serOptolink)      # type: ignore
                                # increment poll pointer
                                state.poll_pointer += 1

                                # +++ everything to be done after poll cycle completed ++++++++++
                                if(state.poll_pointer >= poll_list.num_items):
                                    state.manual_poll = False
                                    # Viessdata csv
                                    if(settings.write_viessdata_csv):
                                        viessdata_util.buffer_csv_line([
                                            value if state.measurements.values.get(item[1]) and state.measurements.values[item[1]].valid else None
                                            for item, value in zip(poll_list.items, poll_data)
                                        ])
                                    
                                    # wo1c energy
                                    if(settings.wo1c_energy > 0) and (state.poll_cycle % settings.wo1c_energy == 0):
                                        if(not settings.vs1protocol):
                                            olbreath(retcode)
                                            retcode = wo1c_energy.read_energy(serOptolink)
                                            if retcode == 0xAA:
                                                raise serial.SerialException("Optolink USB connection lost")
                                        else:
                                            logger.warning("wo1c_energy not supported with VS1/KW protocol")
                                            settings.wo1c_energy = 0
                                    
                                    # poll cycle control
                                    state.poll_cycle += 1
                                    if(state.poll_cycle == 479001600):  # 1*2*3*4*5*6*7*8*9*10*11*12 < 32 bits
                                        state.poll_cycle = 0

                                    # poll pointer control
                                    state.poll_pointer += 1  # wegen  on_polltimer(): if(state.poll_pointer > poll_list.num_items)
                                    if(settings.poll_interval == 0):
                                        # continuous polling
                                        state.poll_pointer = 0  # else: state.poll_pointer gets reset by timer
                                
                                # we did something
                                did_secodary_request = True

                    # MQTT request --------
                    elif(is_on == 1):
                        if(state.mqtt is not None):
                            msg, readback = state.mqtt.get_mqtt_request()
                            if(msg):
                                try:
                                    retcode, _, _, resp = requests_util.response_to_request(msg, serOptolink)
                                    if retcode == 0xAA:
                                        raise serial.SerialException("Optolink USB connection lost")
                                    state.mqtt.publish_response(resp)
                                    if retcode == 1 and readback is not None:
                                        state.mqtt.force_delayed(readback, settings.readback_delay_set)
                                except (serial.SerialException, OSError):
                                    raise
                                except (ValueError, TypeError, IndexError, OverflowError, NotImplementedError) as e:
                                    state.mqtt.publish_response(f"Error: {e}")
                                    logger.warning(f"Error handling MQTT request {msg}: {e}")
                                did_secodary_request = True

                    # TCP/IP request --------
                    elif(is_on == 2):
                        session = state.tcp
                        if session is not None:
                            msg = session.get_request()
                            if(msg):
                                #print(f"recd tcp msg: {msg}")  #temp
                                try:
                                    if session.command_callback and session.command_callback(msg.lower(), 2):
                                        pass
                                    elif (not settings.cmnd_write_allowed) and requests_util.is_write_command(msg):
                                        # haus-fixes: Schreibbefehle per TCP gesperrt
                                        logger.warning(f"TCP write command rejected (cmnd_write_allowed=False): {msg}")
                                        session.send("write commands disabled")
                                    else:
                                        retcode, _, _, resp = requests_util.response_to_request(msg, serOptolink)
                                        if retcode == 0xAA:
                                            raise serial.SerialException("Optolink USB connection lost")
                                        #print(f"retcode {retcode}, try to send tcp: {resp}")  #temp
                                        session.send(resp)
                                except (serial.SerialException, OSError):
                                    raise
                                except (ValueError, TypeError, IndexError, OverflowError, NotImplementedError) as e:
                                    logger.warning(f"Error handling TCP request {msg}: {e}")
                                    session.send(f"Error: {e}")
                                did_secodary_request = True
        
                    #print(f"{((tnow := int(time.time()*10000)) - tprev)} ds {did_secodary_request}"); tprev = tnow
                            
                    if (did_secodary_request):
                        olbreath(retcode)
                        break

                # next time start with cheching next task first
                request_pointer = (is_on + 1) % num_tasks
                #print(f"{((tnow := int(time.time()*10000)) - tprev)} rp {request_pointer}"); tprev = tnow

                # keep-alive with vs1 
                if(settings.vs1protocol):
                    if(time.monotonic() > state.last_vs1_comm + 0.5):
                        retcode,_,_ = vs12_adapter.read_datapoint_ext(0xf8, 2, serOptolink)     # type: ignore
                        if retcode == 0xAA:
                            raise serial.SerialException("Optolink USB connection lost")
                        olbreath(retcode)
                        did_secodary_request = True

                # let cpu take a breath if there was nothing to do
                if not (did_vicon_request or did_secodary_request):
                    utils.work_event.wait(0.1)
                
                # reset retry couters in case
                if(reset_retry_counters_time > 0) and (time.monotonic() >= reset_retry_counters_time):
                    state.num_restarts = 0
                    non_usb_failures = 0
                    state.num_vicon_tries = 0
                    reset_retry_counters_time = -1
                
        except SystemExit:
            raise
        except (serial.SerialException, OSError) as e:
            usb_failure = True
            excptn = e
            logger.warning(f"Serial/IO connection failed: {e}")
        except Exception as e:
            non_usb_failures += 1
            logger.exception("main")
            excptn = e
        finally:
            close_everything()

        if worker_failed:
            raise SystemExit(1)
        if utils.shutdown_event.is_set():
            return
        # An unplugged USB cable must not consume the non-USB restart budget.
        if not usb_failure and excptn is None:
            non_usb_failures += 1
        if ((usb_failure and not settings.usb_reconnect_forever and state.num_restarts >= settings.max_restarts)
                or (not usb_failure and non_usb_failures > settings.max_restarts)):
            logger.error("too many restarts - exit script")
            raise SystemExit(1)
        delay = reconnect_delay(settings.restart_delay, settings.restart_max_delay, state.num_restarts + 1)
        logger.info(f"waiting {delay}s before re-start...")
        utils.shutdown_event.wait(delay)


if __name__ == "__main__":
    main()
