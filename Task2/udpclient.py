"""
UDP Client — 模拟 TCP 可靠传输（GBN 协议）。

流程:
  1. 三次握手：发送 StudentID（学号后4位 XOR 0x5A3C）
  2. GBN 滑动窗口发送（窗口 5 包，每包 40-80B 随机）
  3. 超时重传 + 快速重传（3 dup ACK）+ 自适应 RTO
  4. 四次挥手：发送 FIN，等待服务器 FIN 回复
  5. 汇总：丢包率、RTT 统计（pandas）

用法:
  python udpclient.py --server_ip 127.0.0.1 --server_port 12345

自定义协议报文 (统一 13B 头):
  [1B Flags][4B Seq][4B Ack][4B Length] + Payload
"""

import config
import socket
import struct
import random
import sys
import os
import time
import threading
import queue
import argparse
from datetime import datetime

import pandas as pd

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")


def log_event(fmt: str, *args) -> None:
    """写日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {fmt.format(*args)}"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def pack_header(flags: int, seq: int = 0, ack: int = 0, length: int = 0) -> bytes:
    """打包统一 13B 报文头"""
    return struct.pack("!BIII", flags, seq, ack, length)


def compute_student_id(last4: int) -> int:
    """计算 StudentID 字段：学号后4位 XOR 0x5A3C"""
    return last4 ^ config.STUDENT_ID_MASK


class ReliableUDPClient:
    def __init__(self, server_ip: str, server_port: int):
        self.server_addr = (server_ip, server_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.1)

        # 滑动窗口
        self.base = 0
        self.next_seq = 0

        # 统计
        self.total_sends = 0
        self.retransmit_count = 0
        self.rtt_samples = []
        self.send_times = {}       # seq → 发送时间
        self.packets = []          # 数据包信息列表
        self.packet_sizes = []     # 每包大小

        # 超时
        self.rto = config.INITIAL_TIMEOUT

        # 快速重传
        self.last_ack = -1
        self.dup_ack_count = 0

        # 接收线程
        self.ack_queue = queue.Queue()
        self.running = True
        self.recv_thread = threading.Thread(target=self._receiver, daemon=True)

    def _receiver(self):
        """独立接收线程，处理来自服务器的 ACK 报文"""
        while self.running:
            try:
                data, _ = self.sock.recvfrom(4096)
                if len(data) < 13:
                    continue
                flags, seq, ack, _ = struct.unpack("!BIII", data[:13])

                if flags & config.FLAG_ACK:
                    self.ack_queue.put(ack)
                    # 快速重传检测
                    if ack == self.last_ack:
                        self.dup_ack_count += 1
                        if self.dup_ack_count == config.FAST_RETX_THRESHOLD:
                            log_event("收到 {} 个重复 ACK={}，触发快速重传",
                                      config.FAST_RETX_THRESHOLD, ack)
                            self._fast_retransmit()
                    else:
                        self.last_ack = ack
                        self.dup_ack_count = 0
            except socket.timeout:
                continue
            except OSError:
                return

    def _send_packet(self, flags: int, seq: int = 0, ack: int = 0,
                     data: bytes = b"", record_time: bool = True) -> float:
        """发送报文，返回发送时间"""
        pkt = pack_header(flags, seq, ack, len(data)) + data
        self.sock.sendto(pkt, self.server_addr)
        return time.time() if record_time else 0

    def _fast_retransmit(self):
        """快速重传：收到 3 个重复 ACK 时重传窗口内所有未确认包"""
        if self.base >= config.TOTAL_PACKETS:
            return

        for i in range(self.base, min(self.base + config.WINDOW_SIZE, config.TOTAL_PACKETS)):
            if not self.packets[i]['acked']:
                pkt_info = self.packets[i]
                pkt_info['sent_time'] = self._send_packet(
                    config.FLAG_ACK, i, 0, pkt_info['data'])
                pkt_info['retrans_count'] += 1
                self.total_sends += 1
                self.retransmit_count += 1
                log_event("快速重传 第{}个 (seq={}, 大小:{}B)",
                          i + 1, i, pkt_info['size'])

        self.dup_ack_count = 0
        self.next_seq = self.base

    def _establish_connection(self):
        """三次握手建立连接"""
        log_event("=== 三次握手阶段 ===")

        student_id = compute_student_id(config.STUDENT_ID_LAST4)

        while True:
            # SYN payload: StudentID(2B) + TotalPackets(2B)
            syn_data = struct.pack("!HH", student_id, config.TOTAL_PACKETS)
            self._send_packet(config.FLAG_SYN, 0, 0, syn_data)
            log_event("发送 SYN: StudentID={:#x}, TotalPackets={}", student_id, config.TOTAL_PACKETS)

            try:
                data, _ = self.sock.recvfrom(4096)
                if len(data) < 13:
                    continue
                flags, seq, ack, _ = struct.unpack("!BIII", data[:13])
                if (flags & config.FLAG_SYN) and (flags & config.FLAG_ACK) and ack == 1:
                    log_event("收到 SYN-ACK，连接建立中...")
                    break
            except socket.timeout:
                continue

        # 发送连接确认 ACK
        self._send_packet(config.FLAG_ACK, 1, 1)
        log_event("发送连接确认 ACK，进入数据传输阶段")

    def _terminate_connection(self):
        """四次挥手终止连接"""
        log_event("=== 四次挥手阶段 ===")

        while True:
            self._send_packet(config.FLAG_FIN, config.TOTAL_PACKETS, 0)
            log_event("发送 FIN")

            try:
                data, _ = self.sock.recvfrom(4096)
                if len(data) < 13:
                    continue
                flags, _, _, _ = struct.unpack("!BIII", data[:13])
                if flags & config.FLAG_FIN:
                    log_event("收到服务器 FIN，连接关闭")
                    break
            except socket.timeout:
                continue
            except OSError:
                break

        self.running = False
        self.sock.close()

    def _send_data(self):
        """GBN 数据传输"""
        log_event("=== 数据传输阶段 ===")

        # 生成测试数据
        base_text = (
            "The quick brown fox jumps over the lazy dog. "
            "UDP is a connectionless transport protocol. "
            "GBN uses cumulative acknowledgements. "
        )

        rng = random.Random()
        for i in range(config.TOTAL_PACKETS):
            pkt_size = rng.randint(config.PACKET_SIZE_MIN, config.PACKET_SIZE_MAX)
            self.packet_sizes.append(pkt_size)
            data = base_text.encode("ascii")[:pkt_size]
            if len(data) < pkt_size:
                data = (base_text * (pkt_size // len(base_text.encode("ascii")) + 1)).encode("ascii")[:pkt_size]

            self.packets.append({
                'seq': i,
                'size': pkt_size,
                'data': data,
                'sent_time': 0,
                'retrans_count': 0,
                'acked': False,
            })

        self.recv_thread.start()

        while self.base < config.TOTAL_PACKETS:
            # 发送窗口内未发送的包
            while self.next_seq < min(self.base + config.WINDOW_SIZE, config.TOTAL_PACKETS):
                pkt = self.packets[self.next_seq]
                if pkt['sent_time'] == 0:
                    pkt['sent_time'] = self._send_packet(
                        config.FLAG_ACK, self.next_seq, 0, pkt['data'])
                    self.send_times[self.next_seq] = pkt['sent_time']
                    pkt['retrans_count'] += 1
                    self.total_sends += 1

                    byte_s = sum(self.packet_sizes[:self.next_seq])
                    byte_e = byte_s + pkt['size'] - 1
                    tag = "重传" if pkt['retrans_count'] > 1 else ""
                    log_event("{}第{}个（第{}~{}字节）client 端已经发送",
                              tag, self.next_seq + 1, byte_s, byte_e)

                self.next_seq += 1

            # 等待 ACK
            try:
                ack_num = self.ack_queue.get(timeout=self.rto)
                if ack_num > self.base:
                    old_base = self.base
                    self.base = ack_num

                    # 计算 RTT（对刚被确认的最老包）
                    if old_base < config.TOTAL_PACKETS and old_base in self.send_times:
                        rtt_ms = (time.time() - self.send_times[old_base]) * 1000
                        self.rtt_samples.append(rtt_ms)

                    # 标记已确认的包
                    for s in range(old_base, self.base):
                        if s < config.TOTAL_PACKETS:
                            self.packets[s]['acked'] = True
                            byte_s = sum(self.packet_sizes[:s])
                            byte_e = byte_s + self.packet_sizes[s] - 1
                            log_event("第{}个（第{}~{}字节）server 端已经收到，RTT 是 {:.2f} ms",
                                      s + 1, byte_s, byte_e,
                                      (time.time() - self.send_times.get(old_base, time.time())) * 1000)

                    # 清理已确认包的发送时间
                    self.send_times = {k: v for k, v in self.send_times.items() if k >= self.base}

                    # 自适应 RTO
                    if self.rtt_samples:
                        avg_rtt = sum(self.rtt_samples[-5:]) / min(len(self.rtt_samples), 5)
                        self.rto = max(0.05, min(3.0, avg_rtt * 5 / 1000))

            except queue.Empty:
                # 超时重传整个窗口
                if self.base < config.TOTAL_PACKETS:
                    log_event("超时 {:.0f}ms（RTO={:.0f}ms），重传窗口 seq={}..{}",
                              self.rto * 1000, self.rto * 1000,
                              self.base, self.next_seq - 1)
                    self.retransmit_count += 1
                    for s in range(self.base, self.next_seq):
                        if s < config.TOTAL_PACKETS:
                            self.packets[s]['sent_time'] = 0
                    self.next_seq = self.base

        self.running = False

    def _print_stats(self):
        """打印传输统计"""
        log_event("=== 汇总统计 ===")

        if not self.rtt_samples:
            log_event("无 RTT 样本")
            return

        loss_rate = (self.total_sends - config.TOTAL_PACKETS) / self.total_sends * 100
        s = pd.Series(self.rtt_samples)

        log_event("丢包率: {:.2f}%  (总发送{}次 / 成功{}包)",
                  loss_rate, self.total_sends, config.TOTAL_PACKETS)
        log_event("最大 RTT: {:.2f} ms", s.max())
        log_event("最小 RTT: {:.2f} ms", s.min())
        log_event("平均 RTT: {:.2f} ms", s.mean())
        log_event("RTT 标准差: {:.2f} ms", s.std())
        log_event("重传次数: {}", self.retransmit_count)

        print(f"\n{'='*50}")
        print(f"丢包率: {loss_rate:.2f}%")
        print(f"最大 RTT: {s.max():.2f} ms")
        print(f"最小 RTT: {s.min():.2f} ms")
        print(f"平均 RTT: {s.mean():.2f} ms")
        print(f"RTT 标准差: {s.std():.2f} ms")
        print(f"重传次数: {self.retransmit_count}")
        print(f"{'='*50}")

    def run(self):
        """主流程"""
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write("")
            self._establish_connection()
            self._send_data()
            self._print_stats()
        finally:
            self._terminate_connection()


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP GBN Client")
    parser.add_argument("--server_ip", default=config.DEFAULT_SERVER_IP, help="服务器 IP 地址")
    parser.add_argument("--server_port", type=int, default=config.DEFAULT_PORT, help="服务器端口号")
    args = parser.parse_args()

    client = ReliableUDPClient(args.server_ip, args.server_port)
    client.run()


if __name__ == "__main__":
    main()
