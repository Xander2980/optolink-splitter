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

import serial
import time
from queue import Queue, Empty, Full

import utils
import vs12_adapter
from c_logging import viconnlog

exit_flag = False

# Funktion zum Hinzufuegen von Bytes zum Puffer
def add_to_ringbuffer(buffer, new_bytes):
    for byte in new_bytes:
        buffer.pop(0)  # Entferne das erste Byte (das aelteste Byte)
        buffer.append(byte)  # Fuege das neue Byte am Ende hinzu


# VS detection ---------------
def detect_vs2(serVicon:serial.Serial, serOpto:serial.Serial, timeout:float) -> bool:
    bufferVicon = bytearray([0xFF, 0xFF, 0xFF])
    bufferOpto = bytearray([0xFF, 0xFF, 0xFF, 0xFF])

    timestart = time.monotonic()

    viconnlog.do_log("detect_vs2...")

    while True:
        # Lesen von Daten von beiden seriellen Schnittstellen
        dataVicon = serVicon.read()
        dataOpto = serOpto.read()

        # Ueberpruefen, ob Daten von ser1 empfangen wurden und dann auf ser2 schreiben
        if dataVicon:
            serOpto.write(dataVicon)
            add_to_ringbuffer(bufferVicon, dataVicon)
            viconnlog.do_log(dataVicon, "M")
            # reset optobuffer
            bufferOpto = bytearray([0xFF, 0xFF, 0xFF, 0xFF])

        # Ueberpruefen, ob Daten von ser2 empfangen wurden und dann auf ser1 schreiben
        if dataOpto:
            serVicon.write(dataOpto)
            add_to_ringbuffer(bufferOpto, dataOpto)
            viconnlog.do_log(dataOpto, "S")
            # check VS2
            if(bufferVicon == bytearray([0x16, 0x00, 0x00])): 
                if(dataOpto == b'\x06'):
                    # Initialisierungssequenz erkannt und positive Antwort
                    return True
            if(bufferOpto[0] == 0x06): 
                if(bufferOpto[1] == 0x41): 
                    if (bufferOpto[3] == 0x01):
                        # Antwort im VS2 Format erkannt
                        viconnlog.do_log("vs2 detected")
                        return True
        time.sleep(0.001)
        if(time.monotonic() > timestart + timeout):
            return False


def detect_vs1(serVicon:serial.Serial, serOpto:serial.Serial, timeout:float) -> bool:
    bufferVicon = [] #bytearray()
    bufferOpto = [] #bytearray()
    timestart = time.monotonic()
    expctdlen = -1

    while True:
        # Lesen von Daten von beiden seriellen Schnittstellen
        dataVicon = serVicon.read()
        dataOpto = serOpto.read()

        # Ueberpruefen, ob Daten von ser1 empfangen wurden und dann auf ser2 schreiben
        if dataVicon:
            serOpto.write(dataVicon)
            for byte in dataVicon:
                bufferVicon.append(byte)
            viconnlog.do_log(dataVicon, "M")
            # vs1 request received?
            if (len(bufferVicon) > 4) and (bufferVicon[0:2] == [0x01, 0xF7]):
                expctdlen = int(bufferVicon[4])
            elif (len(bufferVicon) > 3) and (bufferVicon[0] == 0xF7):
                expctdlen = int(bufferVicon[3])
            else:
                expctdlen = -1
            if(expctdlen >= 0):
                viconnlog.do_log([], f"expect {expctdlen} bytes")
            # reset opto buffer
            bufferOpto = []

        # Ueberpruefen, ob Daten von ser2 empfangen wurden und dann auf ser1 schreiben
        if dataOpto:
            serVicon.write(dataOpto)
            for byte in dataOpto:
                bufferOpto.append(byte)
            viconnlog.do_log(dataOpto, "S")
            if(expctdlen > 0):
                if(len(bufferOpto) == expctdlen):
                    # resonse according to len of vs1 read request detected
                    return True
            # reset viconn buffer
            bufferVicon = []
        time.sleep(0.001)
        if(time.monotonic() > timestart + timeout):
            return False


# Bound queued master requests and avoid a producer/consumer race.
_vicon_requests = Queue(maxsize=16)


def clear_requests() -> None:
    while True:
        try:
            _vicon_requests.get_nowait()
        except Empty:
            return


def listen_to_Vitoconnect(servicon: serial.Serial, pubcallback=None) -> None:
    timeout = 0
    while not exit_flag:
        retcode, _, data = vs12_adapter.receive_telegr(
            False, True, servicon, mqtt_publ_callback=pubcallback)
        if exit_flag:
            return
        if retcode == 0x01:
            try:
                _vicon_requests.put_nowait(data)
            except Full as exc:
                raise RuntimeError("Vitoconnect queue overflow; resync required") from exc
            utils.work_event.set()
            timeout = 0
        elif retcode == 0xff and timeout < 1:
            timeout += 1
            viconnlog.do_log(data, f"TO {timeout}")
        else:
            viconnlog.do_log(data, f"X {retcode:02x}")
            raise RuntimeError(f"Error {retcode:02x} receiving Vitoconnect request")


def get_vicon_request() -> bytearray:
    try:
        return _vicon_requests.get_nowait()
    except Empty:
        return bytearray()
