"""
TCP Reverse Client — 将文件分块发送给 Server 反转，打印结果，输出完整反转文件。

用法:
  python reversetcpclient.py --server_ip 127.0.0.1 --server_port 12345 \
      --file_path sample.txt --lmin 50 --lmax 100 --chunk_seed 42

分块算法 (split_file 函数):
  用 chunk_seed 初始化随机数生成器，循环生成 [Lmin, Lmax] 范围内的随机块长，
  直到覆盖整个文件。当剩余字节 ≤ Lmax 时作为最后一块（不受 Lmin 约束）。
"""

import socket
import struct
import random
import sys
import os
import argparse

import config
from common import log_event, recv_exact

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")


def split_file(data: bytes, lmin: int, lmax: int, seed: int) -> list:
    """
    分块算法 — 用固定种子随机确定每块长度。

    返回:
      list[int] — 每块的字节长度，共 N 块。
      前 N-1 块长度在 [Lmin, Lmax] 内，最后一块可能小于 Lmin。
    """
    rng = random.Random(seed)
    total = len(data)
    chunks = []
    pos = 0

    while pos < total:
        remaining = total - pos
        if remaining <= lmax:
            chunks.append(remaining)
            break
        size = rng.randint(lmin, lmax)
        chunks.append(size)
        pos += size

    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP Reverse Client")
    parser.add_argument(
        "--server_ip", default=config.DEFAULT_SERVER_IP, help="服务器 IP 地址"
    )
    parser.add_argument(
        "--server_port", type=int, default=config.DEFAULT_PORT, help="服务器端口号"
    )
    parser.add_argument(
        "--file_path", default=config.DEFAULT_FILE, help="待发送的 ASCII 文本文件"
    )
    parser.add_argument(
        "--lmin", type=int, default=config.DEFAULT_LMIN, help="每块最小字节数"
    )
    parser.add_argument(
        "--lmax", type=int, default=config.DEFAULT_LMAX, help="每块最大字节数"
    )
    parser.add_argument(
        "--chunk_seed",
        type=int,
        default=config.DEFAULT_CHUNK_SEED,
        help="随机分块种子（用于复现）",
    )
    args = parser.parse_args()

    server_ip = args.server_ip
    server_port = args.server_port
    file_path = args.file_path
    lmin = args.lmin
    lmax = args.lmax
    chunk_seed = args.chunk_seed

    # 读取文件
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    # 分块
    chunk_sizes = split_file(file_bytes, lmin, lmax, chunk_seed)
    n_blocks = len(chunk_sizes)

    # === 连接 server ===
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((server_ip, server_port))

    log_event(LOG_PATH, "连接 server {}:{}", server_ip, server_port)
    log_event(
        LOG_PATH,
        "文件 {} ({}B), Lmin={}, Lmax={}, seed={}, 共 {} 块",
        file_path,
        len(file_bytes),
        lmin,
        lmax,
        chunk_seed,
        n_blocks,
    )

    # 打印分块明细
    pos = 0
    for i, sz in enumerate(chunk_sizes):
        log_event(LOG_PATH, "  块{}: offset={}, 长度={}B", i + 1, pos, sz)
        pos += sz

    # 1) 发送 Initialization
    init_pkt = struct.pack("!BI", config.TYPE_INIT, n_blocks)
    sock.sendall(init_pkt)
    log_event(LOG_PATH, "发送 Initialization: N={}", n_blocks)

    # 2) 接收 agree
    agree = recv_exact(sock, 1)
    agree_type = struct.unpack("!B", agree)[0]
    if agree_type != config.TYPE_AGREE:
        log_event(LOG_PATH, "期望 agree(2)，收到 Type={}", agree_type)
        sock.close()
        sys.exit(1)
    log_event(LOG_PATH, "收到 agree")

    # 3) 逐块发送 reverseRequest，接收 reverseAnswer
    reversed_chunks = []
    pos = 0
    for i, sz in enumerate(chunk_sizes):
        chunk_data = file_bytes[pos : pos + sz]
        pos += sz

        # 发送 reverseRequest: Type + Length + Data
        req = struct.pack("!BI", config.TYPE_REQUEST, sz) + chunk_data
        sock.sendall(req)
        log_event(LOG_PATH, "发送 reverseRequest 第{}块: {}B", i + 1, sz)

        # 接收 reverseAnswer: Type + Length + Data
        ans_header = recv_exact(sock, 5)
        ans_type, ans_len = struct.unpack("!BI", ans_header)
        if ans_type != config.TYPE_ANSWER:
            log_event(LOG_PATH, "期望 reverseAnswer(4)，收到 Type={}", ans_type)
            sock.close()
            sys.exit(1)

        ans_data = recv_exact(sock, ans_len)
        reversed_text = ans_data.decode("utf-8")
        reversed_chunks.append(reversed_text)

        log_event(
            LOG_PATH,
            '收到 reverseAnswer 第{}块: {}B → "{}"',
            i + 1,
            ans_len,
            reversed_text,
        )
        print(f"第{i + 1}块: {reversed_text}")

    sock.close()
    log_event(LOG_PATH, "全部 {} 块收发完成", n_blocks)

    # 4) 写出完整反转文件 — 将各块反转结果按逆序拼接
    full_reversed = "".join(reversed(reversed_chunks))
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    output_path = f"{base_name}_reversed.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_reversed)
    log_event(LOG_PATH, "完整反转文件已写出: {}", output_path)


if __name__ == "__main__":
    main()
