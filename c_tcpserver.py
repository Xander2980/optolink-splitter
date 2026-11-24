import socket
from queue import Queue, Empty, Full

from logger_util import logger
import utils

#logger = logging.getLogger(__name__)
#logger = logging.getLogger("tcptest.txt")

class TcpServer:
    def __init__(self, host: str, port: int, verbose: bool = False, legacy_idle: float = 0.1):
        self.host = host
        self.port = port
        self.verbose = verbose
        # callback for 'special' commands
        self.command_callback = None  

        self.server_socket = None
        self.client_socket = None
        self.client_address = None

        self.requests = Queue(maxsize=128)
        self.legacy_idle = legacy_idle
        self.exit_flag = False

    # ---------------------------------------------------------
    # Startet den Server
    # ---------------------------------------------------------
    def run(self):
        self.exit_flag = False
        self.requests = Queue(maxsize=128)

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Sofortige Port-Wiederverwendung
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Binden + Listen
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)

        logger.info(f"TCP Server listening on {self.host}:{self.port}")
        #print(f"TCP Server listening on {self.host}:{self.port}")

        # warten auf Client
        self._wait_for_client()

        if not self.server_socket: return

        # Server-Socket nicht mehr noetig
        self.server_socket.close()
        self.server_socket = None

        # empfangen, bis exit oder FIN
        self._listen()

        # Nach Empfang -> alles schliessen
        self.stop()

    
    # ---------------------------------------------------------
    # auf Verbindung warten (Endlosschleife)
    # ---------------------------------------------------------
    def _wait_for_client(self):
        if not self.server_socket or self.exit_flag:
            return
        
        self.server_socket.settimeout(0.5)   # akzeptiere max 0.5s blockierend

        while self.server_socket and not self.exit_flag:
            try:
                self.client_socket, self.client_address = self.server_socket.accept()
                logger.info(f"TCP Connection from {self.client_address}")
                break
            except socket.timeout:
                continue   # pruefe exit_flag, weiter warten
            except OSError as e:
                # falls socket von aussen closed wurde -> abbrechen
                logger.info(f"accept() aborted: {e}")
                break

    # ---------------------------------------------------------
    # Empfangen (Endlosschleife)
    # ---------------------------------------------------------
    def _enqueue(self, data):
        msg = data.decode("utf-8").strip().replace('"', "").replace("'", "")
        if msg:
            self.requests.put_nowait(msg)

    def _listen(self):
        if not self.client_socket or self.exit_flag:
            return
        sock = self.client_socket
        sock.settimeout(self.legacy_idle if self.legacy_idle is not None else 0.5)
        buffer = bytearray()
        try:
            while not self.exit_flag:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    # Compatibility for old clients which send no delimiter.
                    # Set legacy_idle=None for reliable newline/NUL framing.
                    if buffer and self.legacy_idle is not None:
                        self._enqueue(buffer)
                        buffer.clear()
                    continue
                if not chunk:
                    break
                buffer.extend(chunk.replace(b"\x00", b"\n"))
                while b"\n" in buffer:
                    frame, _, remainder = buffer.partition(b"\n")
                    if len(frame) > 8192:
                        raise ValueError("TCP frame too long")
                    buffer = bytearray(remainder)
                    self._enqueue(frame)
                if len(buffer) > 8192:
                    raise ValueError("TCP frame too long")
        except (OSError, UnicodeError, ValueError, Full) as exc:
            logger.warning(f"TCP session closed: {exc}")

    # ---------------------------------------------------------
    # Sendet Antwort an den Client
    # ---------------------------------------------------------
    def send(self, data):
        if isinstance(data, str):
            if self.verbose:
                logger.info(f"TCP send: {data}")
            data = data.encode("utf-8") + b"\n"

        try:
            self.client_socket.sendall(data)
            if self.verbose:
                logger.info(f"TCP sent: {utils.bbbstr(data)}")
        except Exception as e:
            logger.warning(f"TCP send failed: {e}, data: {repr(str(data))}")

    # ---------------------------------------------------------
    # Holt gespeicherte Nachricht
    # ---------------------------------------------------------
    def get_request(self) -> str:
        try:
            return self.requests.get_nowait() if not self.exit_flag else ""
        except Empty:
            return ""

    # ---------------------------------------------------------
    # Schliesst alles
    # ---------------------------------------------------------
    def stop(self):
        if self.exit_flag: 
            return

        logger.info("closing TCP Server")

        self.exit_flag = True

        try:
            if self.client_socket:
                self.client_socket.close()
        except:
            pass
        self.client_socket = None

        try:
            if self.server_socket:
                self.server_socket.close()
        except:
            pass
        self.server_socket = None



# ------------------------
# main for test only
# ------------------------
import threading
import time

def main():
    print("Starte TCP-Server-Endlosschleife. STRG+C zum Beenden.")

    server = None  # global fuer Cleanup

    def tcp_loop():

        while True:
            server = TcpServer("0.0.0.0", 9000, verbose=True)

            # # Server in Thread starten
            # t = threading.Thread(target=server.start, daemon=True)
            # t.start()

            # # # Warten bis der Thread fertig ist, aber STRG+C abfangen
            # # while t.is_alive():
            # #     t.join(timeout=0.5)  # kurz timeout, damit KeyboardInterrupt reagiert
            # t.join()
            server.run()

            print("TCP-Session beendet. Warte 1 Sekunde, dann starte ich neu...\n")
            time.sleep(1)

    try:
        t = threading.Thread(target=tcp_loop, daemon=True)
        t.start()
        t.join()

    except KeyboardInterrupt:
        print("\nSTRG+C erkannt. Beende alles...")

        if server:
            server.stop()

        # Optional: warte kurz, damit Threads sauber schliessen
        time.sleep(0.5)

        print("Programm beendet.")
    
    finally:
        print("exit script")



if __name__ == "__main__":
    main()
