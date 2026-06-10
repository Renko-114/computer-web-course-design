"""Task2 UDP GBN 公共模块 — 线程安全日志 + 协议头打包"""

import struct
import threading
from datetime import datetime

_LOG_LOCK = threading.Lock()


def log_event(log_path: str, fmt: str, *args) -> None:
    """写入运行日志，带时间戳，线程安全"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {fmt.format(*args)}"
    with _LOG_LOCK:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def pack_header(flags: int, seq: int = 0, ack: int = 0, length: int = 0) -> bytes:
    """打包统一 13B 报文头 [1B Flags][4B Seq][4B Ack][4B Length]"""
    return struct.pack("!BIII", flags, seq, ack, length)
