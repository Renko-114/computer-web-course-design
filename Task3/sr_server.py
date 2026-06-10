"""
UDP SR Server — 模拟 TCP 可靠传输（SR 协议）。

流程:
  1. 三次握手：验证 Client 发来的 StudentID
  2. 数据传输：SR 接收端 — 乱序缓存、逐个确认、按序交付
  3. 四次挥手：收到 FIN 后回复 FIN

自定义协议报文 (统一 13B 头):
  [1B Flags][4B Seq][4B Ack][4B Length] + Payload
"""

import config
import socket
import struct
import random
import os
import argparse

from common import log_event, pack_header, unpack_header

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")


def verify_student_id(received: int) -> bool:
    result = received ^ config.STUDENT_ID_MASK
    return 0 <= result <= 9999


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP SR Server")
    parser.add_argument("--host", default=config.DEFAULT_HOST, help="服务器监听地址")
    parser.add_argument(
        "--port", type=int, default=config.DEFAULT_PORT, help="服务器监听端口"
    )
    args = parser.parse_args()

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    log_event(LOG_PATH, "SR Server 启动，监听 UDP {}:{}", args.host, args.port)

    # === Phase 1: 三次握手 ===
    total_pkts = 0
    while True:
        data, client_addr = sock.recvfrom(4096)
        if len(data) < 13:
            continue
        flags, seq, ack, length = unpack_header(data[:13])
        payload = data[13:]
        if not (flags & config.FLAG_SYN):
            continue
        if len(payload) < 4:
            continue
        student_id, total_pkts = struct.unpack("!HH", payload[:4])
        if verify_student_id(student_id):
            log_event(LOG_PATH,
                "[{}] 收到 SYN, StudentID={:#x} 验证通过, 共{}包",
                client_addr, student_id, total_pkts)
            synack = pack_header(config.FLAG_SYN | config.FLAG_ACK, 0, 1, 0)
            sock.sendto(synack, client_addr)
            log_event(LOG_PATH, "[{}] 发送 SYN-ACK", client_addr)
            sock.settimeout(5.0)
            while True:
                try:
                    d2, _ = sock.recvfrom(4096)
                    af, _, an, _ = unpack_header(d2[:13])
                    if af & config.FLAG_ACK and an == 1:
                        log_event(LOG_PATH, "[{}] 收到连接确认 ACK, 进入数据传输阶段", client_addr)
                        break
                except socket.timeout:
                    log_event(LOG_PATH, "[{}] 等待握手 ACK 超时", client_addr)
                    sock.close()
                    return
            break
        else:
            log_event(LOG_PATH, "[{}] StudentID={:#x} 验证失败", client_addr, student_id)

    # === Phase 2: SR 数据传输 ===
    # SR 接收窗口：缓存 [base, base+WINDOW_SIZE) 内的包
    base = 0
    recv_buffer = {}   # seq → payload bytes，乱序缓存
    rng = random.Random()
    sock.settimeout(10.0)

    while base < total_pkts:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            log_event(LOG_PATH, "长时间未收到数据，Server 退出")
            break
        if len(data) < 13:
            continue
        flags, seq, ack, data_len = unpack_header(data[:13])
        payload = data[13 : 13 + data_len]
        if not (flags & config.FLAG_ACK):
            continue

        # 模拟丢包
        if rng.random() < config.DROP_RATE:
            log_event(LOG_PATH, "丢弃 第{}个数据包 seq={}（模拟丢包）", seq + 1, seq)
            continue

        # SR：窗口内就缓存，窗口外忽略
        if seq < base:
            # 旧包重传，仍回 ACK
            ack_pkt = pack_header(config.FLAG_ACK, 0, seq, 0)
            sock.sendto(ack_pkt, client_addr)
            continue

        upper = base + config.WINDOW_SIZE
        if seq >= upper:
            continue  # 超出接收窗口

        recv_buffer[seq] = payload
        log_event(LOG_PATH, "接收 第{}个数据包 seq={} ({}B), 发送 ACK={}",
                  seq + 1, seq, data_len, seq)
        ack_pkt = pack_header(config.FLAG_ACK, 0, seq, 0)
        sock.sendto(ack_pkt, client_addr)

        # 滑动窗口：base 推进到第一个连续未收到的 seq
        while base in recv_buffer:
            base += 1

    if base >= total_pkts:
        log_event(LOG_PATH, "全部 {} 个数据包接收完毕", total_pkts)
    else:
        log_event(LOG_PATH, "数据传输中断, 收到 {} 个连续包", base)

    # === Phase 3: 四次挥手 ===
    sock.settimeout(10.0)
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) < 13:
                continue
            flags, _, _, _ = unpack_header(data[:13])
            if flags & config.FLAG_FIN:
                log_event(LOG_PATH, "收到 FIN，发送 FIN 确认关闭")
                sock.sendto(pack_header(config.FLAG_FIN, 0, 0, 0), client_addr)
                break
        except socket.timeout:
            log_event(LOG_PATH, "等待 FIN 超时，直接关闭")
            break
    sock.close()


if __name__ == "__main__":
    main()
