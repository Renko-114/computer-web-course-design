"""
Task1 TCP Reverse 公共模块 — 共享常量、精确接收、线程安全日志。
"""

import socket
import threading
from datetime import datetime

# 日志锁，保证多线程写日志安全
_LOG_LOCK = threading.Lock()


def log_event(log_path: str, fmt: str, *args) -> None:
    """写入运行日志，带时间戳，线程安全"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {fmt.format(*args)}"
    with _LOG_LOCK:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """精确接收 n 字节数据"""
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("连接意外关闭")
        data += chunk
    return data
