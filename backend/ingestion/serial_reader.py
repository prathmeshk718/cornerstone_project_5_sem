import json
import os
import threading
import time
from typing import Callable, List, Optional

import serial


class DirectSerialReader:
    """
    Optional direct USB Serial reader (/dev/ttyACM0, /dev/ttyUSB0) for standalone verification.
    In full Ubuntu 22.04 deployment, ROS 2 Humble `serial_bridge` handles USB serial -> ROS 2 topics.
    """

    def __init__(
        self,
        on_frame: Callable[[dict], None],
        on_disconnect: Callable[[], None],
        preferred_port: str = "/dev/ttyACM0",
        baud_rate: int = 115200,
    ) -> None:
        self._on_frame = on_frame
        self._on_disconnect = on_disconnect
        self.preferred_port = preferred_port
        self.baud_rate = baud_rate
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _ports(self) -> List[str]:
        candidates = [self.preferred_port, "/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyUSB0", "/dev/ttyUSB1"]
        seen = []
        for p in candidates:
            if p and p not in seen:
                seen.append(p)
        return seen

    def _run(self) -> None:
        ser: Optional[serial.Serial] = None
        while not self._stop_event.is_set():
            if ser is None or not ser.is_open:
                for p in self._ports():
                    if os.path.exists(p):
                        try:
                            ser = serial.Serial(p, self.baud_rate, timeout=0.5)
                            break
                        except (serial.SerialException, OSError):
                            ser = None
                if ser is None:
                    time.sleep(1.5)
                    continue

            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if line.startswith("{"):
                    data = json.loads(line)
                    data.setdefault("port", ser.port)
                    self._on_frame(data)
            except json.JSONDecodeError:
                continue
            except (serial.SerialException, OSError):
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                self._on_disconnect()
                time.sleep(1.0)
