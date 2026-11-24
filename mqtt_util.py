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

import time
from queue import Queue, Empty, Full
import math
import json
from typing import Optional, Any
import paho.mqtt.client as paho

from c_settings_adapter import settings, parse_broker
from logger_util import logger
from c_polllist import poll_list
import utils
import requests_util   # haus-fixes: is_write_command()



verbose = False

mqtt_client = None
cmnd_queue = Queue(maxsize=128)  # (command/topic, optional /set payload, received time)
optolink_ready = False
connection_generation = 0

recent_posts = {}
_sentinel = object()  # eindeutiger Wert fuer "nicht vorhanden"

# callback for 'special' commands
command_callback = None


def on_connect(client, userdata, flags, reason_code, properties):
    # haus-fixes: abgelehnten Connect nicht als "online" melden; nach Reconnect alles neu senden – upstream #261
    if getattr(reason_code, 'is_failure', False):
        logger.error(f"MQTT connect refused: {reason_code}")
        return
    global connection_generation
    connection_generation += 1
    utils.work_event.set()
    reset_recent_list()
    # publish LWT online
    publish_connection_status(client)
    # Subscribe to /set topics for writable datapoints
    subscriptions = [
        (settings.mqtt_topic + "/+/set", 0),
        (settings.mqtt_topic + "/+/+/set", 0),
        (settings.mqtt_topic + "/+/+/+/set", 0),
    ]
    if settings.mqtt_listen != None:
        subscriptions.append((settings.mqtt_listen, 0))
    client.subscribe(subscriptions)
    logger.debug(f"Subscribed to topic patterns: {subscriptions}")
    
def on_disconnect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        logger.warning('mqtt broker disconnected. reason_code = ' + str(reason_code))
    # Broker publishes the configured Last Will on unexpected disconnect.
    utils.work_event.set()

def on_message(client, userdata, msg):
    logger.debug(f"MQTT recd: {msg.topic}, {msg.payload}")
    if(settings.mqtt_listen is None):
        logger.warning(f"MQTT recd: Topic = {msg.topic}, Payload = {msg.payload}")  # ErrMsg oder so?
        return
    if msg.retain:
        logger.warning(f"Ignoring retained MQTT command on {msg.topic}")
        return
    if len(msg.payload) > 8192:
        logger.warning("MQTT command too long")
        return
    topic = str(msg.topic)            # Topic in String umwandeln
    
    # Check if this is a /set topic
    if topic.endswith('/set') and settings.mqtt_topic and topic.startswith(settings.mqtt_topic + '/'):
        enqueue_request(topic, msg.payload)
    elif topic == settings.mqtt_listen:
        try:
            rec = utils.bstr2str(msg.payload)
        except UnicodeError:
            logger.warning("Invalid UTF-8 MQTT command")
            return
        rec = rec.replace(' ','').replace('\0','').replace('\n','').replace('\r','').replace('"','').replace("'","")
        enqueue_request(rec)
    else:
        # Ausgabe anderer eingehenden MQTT-Nachrichten
        logger.warning(f"MQTT recd: Topic = {msg.topic}, Payload = {msg.payload}")


def on_subscribe(client, userdata, mid, reason_code_list, properties=None):
    for reason in reason_code_list:
        if getattr(reason, "is_failure", False) or reason == 128:
            logger.error(f"MQTT subscription rejected: {reason}")


def on_log(client, userdata, level, buf):
    logger.info(f"MQTT [{level}]: {buf}")


def connect_mqtt(): 
    global mqtt_client
    #try:  # exception handled in calling proc
    # Verbindung zu MQTT Broker herstellen ++++++++++++++
    clientid = "olswitch_" + settings.mqtt_topic.replace("/", "").replace(" ", "")
    # paho 1.x and 2.x use different callback signatures.
    if hasattr(paho, "CallbackAPIVersion"):
        mqtt_client = paho.Client(paho.CallbackAPIVersion.VERSION2, clientid)
    else:
        mqtt_client = paho.Client(client_id=clientid)

    # MQTT Username/Password (mqtt_user = "<user>:<pwd>" or None for anonymous)
    creds = settings.mqtt_user
    if creds is not None:
        creds = str(creds).strip()
        if creds != "":
            if ":" in creds:
                user, pwd = creds.split(":", 1)  # split once; allows ":" inside password
                mqtt_client.username_pw_set(user, password=(pwd if pwd != "" else None))
            else:
                mqtt_client.username_pw_set(creds, password=None)
    if hasattr(paho, "CallbackAPIVersion"):
        mqtt_client.on_connect = on_connect
        mqtt_client.on_disconnect = on_disconnect
    else:
        def legacy_connect(client, userdata, flags, rc):
            if rc == 0:
                on_connect(client, userdata, flags, rc, None)
            else:
                logger.error(f"MQTT connect refused: {rc}")
        mqtt_client.on_connect = legacy_connect
        mqtt_client.on_disconnect = lambda client, userdata, rc: on_disconnect(client, userdata, None, rc, None)
    mqtt_client.on_message = on_message
    mqtt_client.will_set(settings.mqtt_topic + "/LWT", "offline", qos=1, retain=True)
    if(settings.mqtt_listen != None):
        mqtt_client.on_subscribe = on_subscribe
    if(settings.mqtt_logging):
        mqtt_client.on_log = on_log
        mqtt_client.enable_logger()  # Muss VOR dem connect() aufgerufen werden
        mqtt_client._logger.setLevel("DEBUG")  # Optional – Level auf DEBUG setzen  # type: ignore
    # Optional TLS / SSL
    if settings.mqtt_tls_enable:
        import ssl
        skip = bool(settings.mqtt_tls_skip_verify)
        ca_path = settings.mqtt_tls_ca_certs
        certfile = settings.mqtt_tls_certfile
        keyfile  = settings.mqtt_tls_keyfile
        if (certfile is not None and keyfile is None) or (keyfile is not None and certfile is None):
            raise Exception("For mTLS you must set mqtt_tls_certfile AND mqtt_tls_keyfile")
        mqtt_client.tls_set(
            ca_certs=ca_path,  # None => OS CA store
            certfile=certfile,
            keyfile=keyfile,
            cert_reqs=(ssl.CERT_NONE if skip else ssl.CERT_REQUIRED),
            tls_version=getattr(ssl, "PROTOCOL_TLS_CLIENT", ssl.PROTOCOL_TLS),
        )
        # IP / hostname mismatch and for "skip verify" mode
        mqtt_client.tls_insecure_set(skip)
    host, port = parse_broker(settings.mqtt_broker)
    mqtt_client.max_queued_messages_set(256)
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
    # Broker outages must not stop local serial/TCP operations.
    mqtt_client.connect_async(host, port)
    mqtt_client.loop_start()
    # preparations
    if(settings.mqtt_fstr is None):
        settings.mqtt_fstr = "{dpname}"
    #except Exception as e:
    #    raise Exception("Error connecting MQTT: " + str(e))


def enqueue_request(command: str, payload: Optional[bytes] = None) -> bool:
    try:
        cmnd_queue.put_nowait((command, payload, time.monotonic()))
        utils.work_event.set()
        return True
    except Full:
        logger.warning("MQTT command queue full")
        publish_response("command queue full")
        return False


def get_mqtt_request() -> tuple[str, Optional[str]]:
    try:
        command, payload, received = cmnd_queue.get_nowait()
    except Empty:
        return "", None
    if time.monotonic() - received > 60:
        logger.warning("Expired MQTT command discarded")
        publish_response("command expired")
        return "", None
    if payload is not None:
        return handle_set_topic(command, payload) or ("", None)
    if command_callback and command_callback(command):
        return "", None
    if not settings.cmnd_write_allowed and requests_util.is_write_command(command):
        logger.warning("MQTT write command rejected (cmnd_write_allowed=False)")
        publish_response("write commands disabled")
        return "", None
    return command, None


def publish_connection_status(client) -> None:
    # LWT remains the legacy combined availability. The additional protocol
    # status distinguishes a live broker session from a disconnected heater.
    client.publish(settings.mqtt_topic + "/status/optolink",
                   "online" if optolink_ready else "offline", qos=1, retain=True)
    client.publish(settings.mqtt_topic + "/LWT",
                   "online" if optolink_ready else "offline", qos=1, retain=True)


def set_optolink_ready(ready: bool) -> None:
    global optolink_ready
    optolink_ready = bool(ready)
    client = mqtt_client
    if client is not None and client.is_connected():
        publish_connection_status(client)


def publish_quality(name: str, measurement) -> bool:
    client = mqtt_client
    if client is None or not client.is_connected():
        return False
    base = settings.mqtt_topic + "/quality/" + name
    a = client.publish(base, "online" if measurement.valid else "offline", qos=1, retain=True)
    b = client.publish(base + "/last_success",
                       json.dumps(measurement.observed_at), qos=1, retain=True)
    return a.rc == paho.MQTT_ERR_SUCCESS and b.rc == paho.MQTT_ERR_SUCCESS


def clear_pending_requests() -> None:
    while True:
        try:
            cmnd_queue.get_nowait()
        except Empty:
            break
    lst_force_refresh.clear()


def publish_read(name: str, addr: int, value: Any, force: bool = False):
    if mqtt_client:
        publishStr = settings.mqtt_fstr.format(dpaddr = addr, dpname = name)
        # send
        ret = publish_smart(settings.mqtt_topic + "/" + publishStr, value, retain=settings.mqtt_retain, force=force)    
        if(verbose): print(ret)


def publish_response(resp:str):
    if mqtt_client and settings.mqtt_respond:
        # always publish responses (unless mqtt_respond is set to None)
        ret = mqtt_client.publish(settings.mqtt_respond, resp)    
        if(verbose): print(ret)


def publish_smart(topic: str, value: Any, qos: int = 0, retain: bool = False, force: bool = False):
    client = mqtt_client
    if client is None or not client.is_connected():
        return
    # Keep the cache object local so a reconnect cannot repopulate a new cache
    # with an entry from an earlier MQTT session.
    cache = recent_posts
    if not force and settings.mqtt_no_redundant and cache.get(topic, _sentinel) == value:
        return
    result = client.publish(topic, value, qos=qos, retain=retain)
    if result.rc == paho.MQTT_ERR_SUCCESS and settings.mqtt_no_redundant:
        cache[topic] = value
    elif result.rc != paho.MQTT_ERR_SUCCESS:
        logger.warning(f"MQTT publish failed on {topic}: {result.rc}")
    return result


def reset_recent_list():
    global recent_posts
    recent_posts = {}


def exit_mqtt():
    global mqtt_client, optolink_ready
    optolink_ready = False
    client = mqtt_client
    if client is None:
        return
    try:
        if client.is_connected():
            publish_connection_status(client)
            info = client.publish(settings.mqtt_topic + "/LWT", "offline", qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError):
                logger.warning("MQTT offline publish not confirmed")
        client.disconnect()
    finally:
        client.loop_stop()
        mqtt_client = None
        clear_pending_requests()


######################################################
# ++  /set funtionality  +++++++++++++++++++++++++++++
# ++  Copyright 2026 manuboek, modified by philippoo66


#TODO
# - bbFilter sachen abfangen
# - strings schreibbar


# list of indexes of poll_list items to get refreshed immediately after got written
lst_force_refresh = []


def is_forced() -> Optional[int]:
    now = time.monotonic()
    for i, (deadline, name) in enumerate(lst_force_refresh):
        if deadline <= now:
            lst_force_refresh.pop(i)
            info = poll_list.find_datapoint_by_name(name)
            if info is not None:
                return info["list_index"]
            break
    return None


def force_delayed(name: str, delay: float = 1) -> None:
    # Only the serial worker schedules and consumes readbacks.
    # Coalesce repeated writes to the same datapoint; no thread per write.
    lst_force_refresh[:] = [(due, dp) for due, dp in lst_force_refresh if dp != name]
    if len(lst_force_refresh) >= 128:
        logger.warning("Readback queue full")
        return
    lst_force_refresh.append((time.monotonic() + max(0, delay), name))


def handle_set_topic(topic: str, payload: bytes) -> Optional[tuple[str, str]]:
    """
    Handle /set topic messages for writable datapoints.
    Topic format: {mqtt_topic}/{dpname}/set
    Converts human-readable values back to write commands.
    """
    try:
        # Extract datapoint name from topic
        # e.g., "vito/c1_temp_room_setpoint/set" -> "c1_temp_room_setpoint"
        topic_parts = topic.split('/')
        if len(topic_parts) < 3 or topic_parts[-1] != 'set':
            logger.warning(f"Invalid /set topic format: {topic}")
            return
        
        prefix = settings.mqtt_topic + "/"
        if not topic.startswith(prefix):
            return None
        relative = topic[len(prefix):-4]
        if settings.set_write_allowlist is not None and not settings.set_write_allowlist:
            publish_response(f"/set {relative} rejected: writes disabled")
            return None
        dpname = relative
        datapoint_info = poll_list.find_datapoint_by_name(dpname)
        if datapoint_info is None:
            for name, info in poll_list.datapoint_metadata.items():
                if settings.mqtt_fstr.format(dpname=name, dpaddr=info["addr"]) == relative:
                    dpname, datapoint_info = name, info
                    break
        value_str = utils.bstr2str(payload).strip()
        
        logger.debug(f"Received /set request: {dpname} = {value_str}")
        
        allowed = settings.set_write_allowlist
        if allowed is not None and dpname not in allowed:
            logger.warning(f"/set datapoint {dpname!r} not allowed")
            publish_response(f"/set {dpname} rejected: not writable")
            return
        if datapoint_info is None:
            logger.warning(f"/set Datapoint '{dpname}' not found in poll_list")
            return
        
        if datapoint_info['bbfilter']:
            logger.warning(f"/set not possible with bb-filter datapoint '{dpname}'")
            return

        # haus-fixes: optionaler Wertebereich je Datenpunkt (settings.write_limits = {'name': (min, max)})
        limits = (settings.write_limits or {}).get(dpname)
        if limits is not None:
            try:
                fval = float(value_str)
            except ValueError:
                logger.warning(f"/set '{dpname}': value '{value_str}' not numeric, rejected (write_limits)")
                return
            lo, hi = limits
            if not math.isfinite(fval) or (lo is not None and fval < lo) or (hi is not None and fval > hi):
                logger.warning(f"/set '{dpname}': value {fval} outside {limits}, rejected")
                publish_response(f"/set {dpname}={value_str} rejected: outside {limits}")
                return

        # datapoint_info: (Name, DpAddr, Len, Scale/Type, Signed) or with PollCycle
        addr = datapoint_info['addr']
        length = datapoint_info['len']
        scale_type = datapoint_info.get('scale_type')
        signed = utils.get_bool(datapoint_info.get('signed', False))
        
        # Convert value to bytes
        bytes_value, write_raw = convert_value_to_bytes(value_str, length, scale_type, signed)
        if bytes_value is None:
            logger.error(f"Failed to convert value '{value_str}' for datapoint '{dpname}'")
            return
        
        if write_raw:
            write_cmd = f"writeraw;{addr:#x};{bytes_value.hex()}"
        else:
            # Create write command in the format: write;addr;len;value
            # For multi-byte values, we need to convert to integer
            int_value = int.from_bytes(bytes_value, byteorder='little', signed=signed)
            write_cmd = f"write;{addr:#x};{length};{int_value}"
        
        logger.debug(f"Generated write command: {write_cmd}")
        return write_cmd, dpname

    except (ValueError, TypeError, OverflowError, KeyError, IndexError):
        logger.exception(f"handle_set_topic {topic}, {payload}")
        #logger.error(f"Error handling /set topic '{topic}': {e}")


def convert_value_to_bytes(value_str: str, length: int, scale_type: Any, signed: bool) -> tuple[Optional[bytes], bool]:  # tuple of bytes, bool <- writeraw if True
    """
    Convert human-readable value string to bytes for writing.
    Reverse operation of requests_util.get_value()
    """
    try:
        # Handle different format types
        scale_type_str = str(scale_type).lower() if scale_type else ''
        
        # Boolean types   
        if scale_type_str in ('bool', 'boolinv', 'onoff', 'offon'): 
            # Parse boolean-like values
            value_upper = value_str.upper()
            is_true = value_upper in ('1', 'TRUE', 'ON', 'YES')
            is_false = value_upper in ('0', 'FALSE', 'OFF', 'NO')
            
            if not (is_true or is_false):
                logger.warning(f"Invalid boolean value: {value_str}")         #TODO make other nummerical values possibble
                return None, False
            
            # Apply inverse logic
            if scale_type_str in ('boolinv', 'offon'): 
                bool_val = is_false  # inverted
            else:
                bool_val = is_true
            
            int_val = 1 if bool_val else 0                                     # ATTENTION! True may be anything except 0!!
            return int_val.to_bytes(length, byteorder='little', signed=False), False
        
        # Numeric types with scaling
        scale = utils.to_number(scale_type)
        if scale is not None:
            # Parse numeric value and reverse scaling
            float_val = float(value_str)
            if not math.isfinite(float_val) or not math.isfinite(scale) or scale == 0:
                return None, False
            int_val = int(round(float_val / scale))
            return int_val.to_bytes(length, byteorder='little', signed=signed), False


        # schedules... write_raw
        if scale_type_str == 'schedvcal':
            return utils.schedvcal2bytes(value_str, length), True

        if scale_type_str == 'schedvdens':
            return utils.schedvdens2bytes(value_str, length), True

        
        # String types - not typically writable, but handle anyway #TEMP
        if scale_type_str in ('utf8', 'utf16'):
            logger.warning(f"String types not typically writable: {scale_type_str}")
            return None, False
        
        # Default: treat as raw integer
        int_val = utils.get_int(value_str)
        return int_val.to_bytes(length, byteorder='little', signed=signed), True
        
    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
        logger.exception(f"convert_value_to_bytes {value_str}, {scale_type}")
        #logger.error(f"Error converting value '{value_str}' with scale_type '{scale_type}': {e}")
        return None, False


# ------------------------
# main for test only
# ------------------------
def main():
    try:
        connect_mqtt()
        print("connect ok")
        while(True):
            for i in range(10):
                publish_read("TestVal", 0x0123, i)
                time.sleep(3)
        # else:
        #     print("fail")
    except Exception as e:
        print(e)
    finally:
        exit_mqtt()


if __name__ == "__main__":
    main()

