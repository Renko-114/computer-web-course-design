"""
UDP Server — 模拟 TCP 可靠传输（GBN 协议）。

流程:
  1. 三次握手：验证 Client 发来的 StudentID（学号后4位 XOR 0x5A3C）
  2. 数据传输：随机丢弃报文模拟丢包，累积确认（GBN 接收端）
  3. 四次挥手：收到 FIN 后回复 FIN，优雅关闭连接

自定义协议报文 (统一 13B 头):
  [1B Flags][4B Seq][4B Ack][4B Length] + Payload

  Flags: SYN=0x01, ACK=0x02, FIN=0x04
  三次握手: SYN → SYN|ACK → ACK
  数据传输: ACK|Payload → ACK（累积确认）
  四次挥手: FIN → FIN
"""

import config
import socket
import struct
import random
import os
import argparse
import threading
from datetime import datetime

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")
LOG_LOCK = threading.Lock()


def log_event(fmt: str, *args) -> None:
    """线程安全写日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {fmt.format(*args)}"
    with LOG_LOCK:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def verify_student_id(received: int) -> bool:
    """验证学号：received XOR 0x5A3C 应在 [0, 9999] 范围内"""
    result = received ^ config.STUDENT_ID_MASK
    return 0 <= result <= 9999


def pack_header(flags: int, seq: int = 0, ack: int = 0, length: int = 0) -> bytes:
    """打包统一 13B 报文头"""
    return struct.pack("!BIII", flags, seq, ack, length)


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP GBN Server")
    parser.add_argument("--host", default=config.DEFAULT_HOST, help="服务器监听地址")
    parser.add_argument(
        "--port", type=int, default=config.DEFAULT_PORT, help="服务器监听端口"
    )
    args = parser.parse_args()

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    log_event("Server 启动，监听 UDP {}:{}", args.host, args.port)

    # ════════════════ Phase 1: 三次握手 ════════════════
    total_pkts = 0
    while True:
        data, client_addr = sock.recvfrom(4096)
        if len(data) < 13:
            continue

        flags, seq, ack, length = struct.unpack("!BIII", data[:13])
        payload = data[13:]

        if not (flags & config.FLAG_SYN):
            continue

        # 从 SYN payload 提取 StudentID(2B) + TotalPackets(2B)
        if len(payload) < 4:
            continue
        student_id, total_pkts = struct.unpack("!HH", payload[:4])

        if verify_student_id(student_id):
            log_event(
                "[{}] 收到 SYN, StudentID={:#x} 验证通过, 共{}包",
                client_addr,
                student_id,
                total_pkts,
            )

            # 发送 SYN-ACK
            synack = pack_header(config.FLAG_SYN | config.FLAG_ACK, 0, 1, 0)
            sock.sendto(synack, client_addr)
            log_event("[{}] 发送 SYN-ACK", client_addr)

            # 等待连接确认 ACK（5s 超时，忽略非 ACK 报文）
            sock.settimeout(5.0)
            while True:
                try:
                    data2, _ = sock.recvfrom(4096)
                    ack_flags, _, ack_num, _ = struct.unpack("!BIII", data2[:13])
                    if ack_flags & config.FLAG_ACK and ack_num == 1:
                        log_event(
                            "[{}] 收到连接确认 ACK, 进入数据传输阶段", client_addr
                        )
                        break
                except socket.timeout:
                    log_event("[{}] 等待握手 ACK 超时，关闭连接", client_addr)
                    sock.close()
                    return
            break
        else:
            log_event(
                "[{}] StudentID={:#x} 验证失败，拒绝连接", client_addr, student_id
            )

    # ════════════════ Phase 2: 数据传输（GBN 接收端） ════════════════
    expected_seq = 0
    rng = random.Random()
    sock.settimeout(10.0)  # 10s 无数据则超时退出

    while expected_seq < total_pkts:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            log_event("长时间未收到数据，Server 退出")
            break

        if len(data) < 13:
            continue

        flags, seq, ack, data_len = struct.unpack("!BIII", data[:13])
        payload = data[13 : 13 + data_len]

        if not (flags & config.FLAG_ACK):
            continue

        # 模拟丢包
        if rng.random() < config.DROP_RATE:
            log_event("丢弃 第{}个数据包 seq={}（模拟丢包）", seq + 1, seq)
            continue

        if seq == expected_seq:
            expected_seq += 1
            log_event(
                "接收 第{}个数据包 seq={} ({}B), 发送累积ACK={}",
                seq + 1,
                seq,
                data_len,
                expected_seq,
            )

            ack_pkt = pack_header(config.FLAG_ACK, 0, expected_seq, 0)
            sock.sendto(ack_pkt, client_addr)
        else:
            log_event(
                "丢弃乱序包 seq={} (期望 seq={}), 重发 ACK={}",
                seq + 1,
                expected_seq + 1,
                expected_seq,
            )

            ack_pkt = pack_header(config.FLAG_ACK, 0, expected_seq, 0)
            sock.sendto(ack_pkt, client_addr)

    if expected_seq >= total_pkts:
        log_event("全部 {} 个数据包接收完毕", total_pkts)
    else:
        log_event(
            "数据传输中断，期望 seq={}，实际收到 {} 包", expected_seq, expected_seq
        )

    # ════════════════ Phase 3: 四次挥手 ════════════════
    sock.settimeout(10.0)
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) < 13:
                continue
            flags, _, _, _ = struct.unpack("!BIII", data[:13])
            if flags & config.FLAG_FIN:
                log_event("收到 FIN，发送 FIN 确认关闭")
                fin_pkt = pack_header(config.FLAG_FIN, 0, 0, 0)
                sock.sendto(fin_pkt, client_addr)
                break
        except socket.timeout:
            log_event("等待 FIN 超时，Server 直接关闭")
            break

    sock.close()


if __name__ == "__main__":
    main()
