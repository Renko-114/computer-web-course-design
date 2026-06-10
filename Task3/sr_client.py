"""
UDP SR Client — 模拟 TCP 可靠传输（SR 协议）。

流程:
  1. 三次握手：发送 StudentID（学号后4位 XOR 0x5A3C）
  2. SR 选择性重传（窗口 5 包，每包独立定时器）
  3. 超时只重传丢失的包，不回退整个窗口
  4. 四次挥手 + pandas 统计

用法:
  python sr_client.py --server_ip 127.0.0.1 --server_port 12345
"""

import config
import socket
import struct
import random
import os
import time
import argparse

import pandas as pd

from common import log_event, pack_header

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")


def compute_student_id(last4: int) -> int:
    return last4 ^ config.STUDENT_ID_MASK


class SRClient:
    def __init__(self, server_ip: str, server_port: int):
        self.server_addr = (server_ip, server_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.base = 0
        self.next_seq = 0
        self.total_sends = 0
        self.retransmit_count = 0
        self.rtt_samples = []
        self.packets = []       # [{seq, size, data, sent_time, retrans_count, acked}]
        self.packet_sizes = []
        self.byte_offsets = [0]

        self.rto = config.INITIAL_TIMEOUT
        self.estimated_rtt = None
        self.dev_rtt = None

    def _send_packet(self, flags: int, seq: int = 0, ack: int = 0,
                     data: bytes = b"") -> float:
        pkt = pack_header(flags, seq, ack, len(data)) + data
        self.sock.sendto(pkt, self.server_addr)
        return time.time()

    def _establish_connection(self):
        log_event(LOG_PATH, "=== 三次握手阶段 ===")
        student_id = compute_student_id(config.STUDENT_ID_LAST4)
        while True:
            syn_data = struct.pack("!HH", student_id, config.TOTAL_PACKETS)
            self._send_packet(config.FLAG_SYN, 0, 0, syn_data)
            log_event(LOG_PATH, "发送 SYN: StudentID={:#x}, TotalPackets={}",
                      student_id, config.TOTAL_PACKETS)
            try:
                data, _ = self.sock.recvfrom(4096)
                if len(data) >= 13:
                    flags, seq, ack, _ = struct.unpack("!BIII", data[:13])
                    if (flags & config.FLAG_SYN) and (flags & config.FLAG_ACK) and ack == 1:
                        log_event(LOG_PATH, "收到 SYN-ACK，连接建立中...")
                        break
            except socket.timeout:
                log_event(LOG_PATH, "等待 SYN-ACK 超时，重发 SYN")

        self._send_packet(config.FLAG_ACK, 1, 1)
        log_event(LOG_PATH, "发送连接确认 ACK，进入数据传输阶段")

    def _terminate_connection(self):
        log_event(LOG_PATH, "=== 四次挥手阶段 ===")
        for _ in range(10):
            self._send_packet(config.FLAG_FIN, config.TOTAL_PACKETS, 0)
            log_event(LOG_PATH, "发送 FIN")
            try:
                data, _ = self.sock.recvfrom(4096)
                if len(data) >= 13:
                    flags, _, _, _ = struct.unpack("!BIII", data[:13])
                    if flags & config.FLAG_FIN:
                        log_event(LOG_PATH, "收到服务器 FIN，连接关闭")
                        break
            except socket.timeout:
                continue
            except OSError:
                break
        self.sock.close()

    def _send_data(self):
        log_event(LOG_PATH, "=== 数据传输阶段 ===")
        base_text = (
            "The quick brown fox jumps over the lazy dog. "
            "UDP is a connectionless transport protocol. "
            "GBN uses cumulative acknowledgements. "
        )
        rng = random.Random()
        for i in range(config.TOTAL_PACKETS):
            pkt_size = rng.randint(config.PACKET_SIZE_MIN, config.PACKET_SIZE_MAX)
            self.packet_sizes.append(pkt_size)
            self.byte_offsets.append(self.byte_offsets[-1] + pkt_size)
            data = base_text.encode("ascii")[:pkt_size]
            if len(data) < pkt_size:
                data = (base_text * (pkt_size // len(base_text.encode("ascii")) + 1)
                        ).encode("ascii")[:pkt_size]
            self.packets.append({
                "seq": i, "size": pkt_size, "data": data,
                "sent_time": 0.0, "retrans_count": 0, "acked": False,
            })

        self.sock.settimeout(0.05)

        while self.base < config.TOTAL_PACKETS:
            # 发送窗口内未发出的新包
            while self.next_seq < min(self.base + config.WINDOW_SIZE,
                                       config.TOTAL_PACKETS):
                pkt = self.packets[self.next_seq]
                if pkt["sent_time"] == 0.0:
                    pkt["sent_time"] = self._send_packet(
                        config.FLAG_ACK, self.next_seq, 0, pkt["data"])
                    pkt["retrans_count"] += 1
                    self.total_sends += 1
                    byte_s = self.byte_offsets[self.next_seq]
                    byte_e = byte_s + pkt["size"] - 1
                    log_event(LOG_PATH, "第{}个（偏移 {}~{}B）client 端已经发送",
                              self.next_seq + 1, byte_s, byte_e)
                self.next_seq += 1

            # 收 ACK
            try:
                data, _ = self.sock.recvfrom(4096)
                if len(data) >= 13:
                    flags, _, ack_seq, _ = struct.unpack("!BIII", data[:13])
                    if flags & config.FLAG_ACK:
                        seq = ack_seq  # SR: ACK 字段携带被确认的 seq
                        if (self.base <= seq < min(self.base + config.WINDOW_SIZE,
                                                    config.TOTAL_PACKETS)
                                and not self.packets[seq]["acked"]):
                            self.packets[seq]["acked"] = True
                            # 收集 RTT
                            sent_t = self.packets[seq]["sent_time"]
                            if sent_t > 0:
                                rtt_ms = (time.time() - sent_t) * 1000
                                self.rtt_samples.append(rtt_ms)
                                byte_s = self.byte_offsets[seq]
                                byte_e = byte_s + self.packets[seq]["size"] - 1
                                log_event(LOG_PATH,
                                    "第{}个（偏移 {}~{}B）server 端已经收到，RTT 是 {:.2f} ms",
                                    seq + 1, byte_s, byte_e, rtt_ms)

                                # 自适应 RTO（TCP EWMA）
                                sample = self.rtt_samples[-1]
                                if self.estimated_rtt is None:
                                    self.estimated_rtt = sample
                                    self.dev_rtt = sample / 2
                                else:
                                    self.estimated_rtt = (
                                        0.875 * self.estimated_rtt + 0.125 * sample)
                                    self.dev_rtt = (
                                        0.75 * self.dev_rtt
                                        + 0.25 * abs(sample - self.estimated_rtt))
                                self.rto = max(0.05, min(3.0,
                                    (self.estimated_rtt + 4 * self.dev_rtt) / 1000))

                            # 推进 base 到第一个未 ack 的包
                            while (self.base < config.TOTAL_PACKETS
                                   and self.packets[self.base]["acked"]):
                                self.base += 1
            except socket.timeout:
                pass

            # SR: 只重传超时的包（不是整个窗口）
            now = time.time()
            for i in range(self.base, min(self.base + config.WINDOW_SIZE,
                                           config.TOTAL_PACKETS)):
                pkt = self.packets[i]
                if (not pkt["acked"] and pkt["sent_time"] > 0
                        and now - pkt["sent_time"] > self.rto):
                    log_event(LOG_PATH, "超时 第{}个 (seq={})，单独重传", i + 1, i)
                    pkt["sent_time"] = self._send_packet(
                        config.FLAG_ACK, i, 0, pkt["data"])
                    pkt["retrans_count"] += 1
                    self.total_sends += 1
                    self.retransmit_count += 1

        log_event(LOG_PATH, "=== 汇总统计 ===")
        s = pd.Series(self.rtt_samples)
        loss_rate = (self.total_sends - config.TOTAL_PACKETS) / self.total_sends * 100
        log_event(LOG_PATH, "丢包率: {:.2f}%  (总发送{}次 / 成功{}包)",
                  loss_rate, self.total_sends, config.TOTAL_PACKETS)
        if len(s) > 0:
            log_event(LOG_PATH, "最大 RTT: {:.2f} ms", s.max())
            log_event(LOG_PATH, "最小 RTT: {:.2f} ms", s.min())
            log_event(LOG_PATH, "平均 RTT: {:.2f} ms", s.mean())
            log_event(LOG_PATH, "RTT 标准差: {:.2f} ms", s.std())
        log_event(LOG_PATH, "重传次数: {}", self.retransmit_count)
        if len(s) > 0:
            print(f"\n{'='*50}")
            print(f"丢包率: {loss_rate:.2f}%")
            print(f"最大 RTT: {s.max():.2f} ms")
            print(f"最小 RTT: {s.min():.2f} ms")
            print(f"平均 RTT: {s.mean():.2f} ms")
            print(f"RTT 标准差: {s.std():.2f} ms")
            print(f"重传次数: {self.retransmit_count}")
            print(f"{'='*50}")

    def run(self):
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write("")
            self._establish_connection()
            self._send_data()
        finally:
            self._terminate_connection()


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP SR Client")
    parser.add_argument("--server_ip", default=config.DEFAULT_SERVER_IP, help="服务器 IP")
    parser.add_argument("--server_port", type=int, default=config.DEFAULT_PORT, help="服务器端口")
    args = parser.parse_args()
    SRClient(args.server_ip, args.server_port).run()


if __name__ == "__main__":
    main()
