"""Task3 UDP SR 公共模块"""

import struct
import threading
from datetime import datetime

_LOG_LOCK = threading.Lock()


def log_event(log_path: str, fmt: str, *args) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {fmt.format(*args)}"
    with _LOG_LOCK:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def pack_header(flags: int, seq: int = 0, ack: int = 0, length: int = 0) -> bytes:
    return struct.pack("!BIII", flags, seq, ack, length)
