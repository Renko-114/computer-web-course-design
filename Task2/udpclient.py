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
import os
import time
import threading
import queue
import argparse

import pandas as pd

from common import log_event, pack_header, unpack_header

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")


def compute_student_id(last4: int) -> int:
    """计算 StudentID 字段：学号后4位 XOR 0x5A3C"""
    return last4 ^ config.STUDENT_ID_MASK


class ReliableUDPClient:
    def __init__(self, server_ip: str, server_port: int):
        self.server_addr = (server_ip, server_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # 滑动窗口
        self.base = 0
        self.next_seq = 0

        # 统计
        self.rtt_samples = []
        self.packets = []  # 数据包信息列表
        self.packet_sizes = []  # 每包大小
        self.byte_offsets = [0]  # 前缀和：byte_offsets[i] = 前 i 包的总字节数

        # 超时
        self.rto = config.INITIAL_TIMEOUT
        self.estimated_rtt = None  # EWMA 平滑 RTT，首次测量后初始化
        self.dev_rtt = None        # EWMA RTT 偏差，首次测量后初始化

        # 线程安全锁（保护 base/next_seq）
        self._lock = threading.RLock()

        # 接收线程
        self.ack_queue = queue.Queue()
        self.running = True
        self.recv_thread = threading.Thread(target=self._receiver, daemon=True)

    def _receiver(self):
        """独立接收线程，处理来自服务器的 ACK 报文"""
        self.sock.settimeout(0.05)
        last_seq = -1
        dup_cnt = 0

        while self.running:
            try:
                data, _ = self.sock.recvfrom(4096)
                result = unpack_header(data[:13])
                if result is None:
                    continue
                flags, _, ack, _ = result

                if not (flags & config.FLAG_ACK):
                    continue

                self.ack_queue.put(ack)

                if ack != last_seq:
                    last_seq = ack
                    dup_cnt = 0
                else:
                    dup_cnt += 1

                if dup_cnt >= config.FAST_RETX_THRESHOLD:
                    log_event(LOG_PATH,
                        "收到 {} 个重复 ACK={}，触发快速重传",
                        config.FAST_RETX_THRESHOLD, ack)
                    with self._lock:
                        dup_cnt = 0
                        self.next_seq = self.base
            except socket.timeout:
                continue
            except ConnectionError:
                return

    def _send_packet(
        self,
        flags: int,
        seq: int = 0,
        ack: int = 0,
        data: bytes = b"",
    ) -> float:
        """发送报文，返回发送时间"""
        pkt = pack_header(flags, seq, ack, len(data)) + data
        self.sock.sendto(pkt, self.server_addr)
        return time.time()

    def _establish_connection(self):
        """三次握手建立连接"""
        log_event(LOG_PATH, "=== 三次握手阶段 ===")

        student_id = compute_student_id(config.STUDENT_ID_LAST4)

        while True:
            # SYN payload: StudentID(2B) + TotalPackets(2B)
            syn_data = struct.pack("!HH", student_id, config.TOTAL_PACKETS)
            self._send_packet(config.FLAG_SYN, 0, 0, syn_data)
            log_event(LOG_PATH, 
                "发送 SYN: StudentID={:#x}, TotalPackets={}",
                student_id,
                config.TOTAL_PACKETS,
            )

            try:
                self.sock.settimeout(0.5)
                data, _ = self.sock.recvfrom(4096)
                flags, _, ack, _ = unpack_header(data[:13])
                if flags is None:
                    continue
                if (flags & config.FLAG_SYN) and (flags & config.FLAG_ACK) and ack == 1:
                    log_event(LOG_PATH, "收到 SYN-ACK，连接建立中...")
                    break
            except socket.timeout:
                log_event(LOG_PATH, "等待 SYN-ACK 超时，重发 SYN")
                continue

        # 发送连接确认 ACK
        self._send_packet(config.FLAG_ACK, 1, 1)
        log_event(LOG_PATH, "发送连接确认 ACK，进入数据传输阶段")

    def _terminate_connection(self):
        """四次挥手终止连接（最多重试 10 次）"""
        log_event(LOG_PATH, "=== 四次挥手阶段 ===")

        for _ in range(10):
            self._send_packet(config.FLAG_FIN, config.TOTAL_PACKETS, 0)
            log_event(LOG_PATH, "发送 FIN")

            # Step 2: Wait for ACK
            try:
                self.sock.settimeout(0.5)
                data, _ = self.sock.recvfrom(4096)
                flags, _, _, _ = unpack_header(data[:13])
                if flags is None:
                    continue
                if not (flags & config.FLAG_ACK):
                    continue
                log_event(LOG_PATH, "收到 FIN 的 ACK")
            except socket.timeout:
                continue
            except ConnectionError:
                break

            # Step 3: Wait for FIN
            try:
                self.sock.settimeout(0.5)
                data, _ = self.sock.recvfrom(4096)
                flags, _, _, _ = unpack_header(data[:13])
                if flags is None:
                    continue
                if flags & config.FLAG_FIN:
                    log_event(LOG_PATH, "收到服务器 FIN")
                    # Step 4: Send final ACK
                    self._send_packet(config.FLAG_ACK, 0, 0)
                    log_event(LOG_PATH, "发送最终 ACK，连接关闭")
                    break
            except socket.timeout:
                continue
            except ConnectionError:
                break

        self.running = False
        self.sock.close()
        
    def _generate_data(self):
        """数据生成"""
        log_event(LOG_PATH, "=== 数据生成阶段 ===")

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
            self.byte_offsets.append(self.byte_offsets[-1] + pkt_size)
            data = base_text.encode("ascii")[:pkt_size]
            if len(data) < pkt_size:
                data = (
                    base_text * (pkt_size // len(base_text.encode("ascii")) + 1)
                ).encode("ascii")[:pkt_size]

            self.packets.append(
                {
                    "seq": i,
                    "size": pkt_size,
                    "data": data,
                    "sent_time": 0,
                    "retrans_count": 0,
                    "acked": False,
                }
            )

    def _send_data(self):
        """GBN 数据传输"""
        log_event(LOG_PATH, "=== 数据传输阶段 ===")
        
        self.recv_thread.start()

        while self.base < config.TOTAL_PACKETS:
            # 发送窗口内的包
            while self.next_seq < min(
                self.base + config.WINDOW_SIZE, config.TOTAL_PACKETS
            ):
                pkt = self.packets[self.next_seq]
                tag = "重传" if pkt["retrans_count"] > 0 else ""
                pkt["sent_time"] = self._send_packet(
                    config.FLAG_ACK, self.next_seq, 0, pkt["data"]
                )
                pkt["retrans_count"] += 1
                byte_s = self.byte_offsets[self.next_seq]
                byte_e = byte_s + pkt["size"] - 1
                log_event(LOG_PATH,
                    "{}第{}个（偏移 {}~{}B）client 端已经发送",
                    tag,
                    self.next_seq + 1,
                    byte_s,
                    byte_e,
                )
                self.next_seq += 1

            # 等待 ACK
            try:
                ack_num = self.ack_queue.get(timeout=self.rto)
                with self._lock:
                    if ack_num > self.base:
                        old_base = self.base
                        self.base = ack_num
                        
                        # 计算 RTT（对刚被确认的最老包）
                        sent_t = self.packets[old_base]["sent_time"]
                        if sent_t > 0:
                            rtt_ms = (time.time() - sent_t) * 1000
                            self.rtt_samples.append(rtt_ms)

                        # 标记已确认的包
                        for s in range(old_base, self.base):
                            self.packets[s]["acked"] = True
                            byte_s = self.byte_offsets[s]
                            byte_e = byte_s + self.packet_sizes[s] - 1
                            pkt_rtt = (time.time() - self.packets[s]["sent_time"]) * 1000
                            log_event(LOG_PATH,
                                "第{}个（偏移 {}~{}B）server 端已经收到，RTT 是 {:.2f} ms",
                                s + 1,
                                byte_s,
                                byte_e,
                                pkt_rtt,
                            )
                            
                        # 自适应 RTO（TCP EWMA 算法）
                        if self.rtt_samples:
                            sample = self.rtt_samples[-1]  # 本次 RTT 样本
                            if self.estimated_rtt is None:
                                self.estimated_rtt = sample
                                self.dev_rtt = sample / 2
                            else:
                                self.estimated_rtt = 0.875 * self.estimated_rtt + 0.125 * sample
                                self.dev_rtt = 0.75 * self.dev_rtt + 0.25 * abs(sample - self.estimated_rtt)
                            self.rto = max(0.05, min(3.0, (self.estimated_rtt + 4 * self.dev_rtt) / 1000))

            except queue.Empty:
                # 超时重传：回退 next_seq，让主循环自然重发整个窗口
                log_event(LOG_PATH,
                    "超时 {:.0f}ms（RTO={:.0f}ms），重传窗口 seq={}..{}",
                    self.rto * 1000,
                    self.rto * 1000,
                    self.base,
                    self.next_seq - 1,
                )
                self.next_seq = self.base

        self.running = False

    def _print_stats(self):
        """打印传输统计"""
        log_event(LOG_PATH, "=== 汇总统计 ===")

        if not self.rtt_samples:
            log_event(LOG_PATH, "无 RTT 样本")
            return

        total_sends = sum(p["retrans_count"] for p in self.packets)
        retrans = sum(p["retrans_count"] - 1 for p in self.packets)
        
        loss_rate = (total_sends - config.TOTAL_PACKETS) / total_sends * 100
        s = pd.Series(self.rtt_samples)

        log_event(LOG_PATH, 
            "丢包率: {:.2f}%  (总发送{}次 / 成功{}包)",
            loss_rate,
            total_sends,
            config.TOTAL_PACKETS,
        )
        log_event(LOG_PATH, "最大 RTT: {:.2f} ms", s.max())
        log_event(LOG_PATH, "最小 RTT: {:.2f} ms", s.min())
        log_event(LOG_PATH, "平均 RTT: {:.2f} ms", s.mean())
        log_event(LOG_PATH, "RTT 标准差: {:.2f} ms", s.std())
        log_event(LOG_PATH, "重传次数: {}", retrans)

        print(f"\n{'=' * 50}")
        print(f"丢包率: {loss_rate:.2f}%")
        print(f"最大 RTT: {s.max():.2f} ms")
        print(f"最小 RTT: {s.min():.2f} ms")
        print(f"平均 RTT: {s.mean():.2f} ms")
        print(f"RTT 标准差: {s.std():.2f} ms")
        print(f"重传次数: {retrans}")
        print(f"{'=' * 50}")

    def run(self):
        """主流程"""
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write("")
            self._establish_connection()
            self._generate_data()
            self._send_data()
            self._print_stats()
        finally:
            self._terminate_connection()


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP GBN Client")
    parser.add_argument(
        "--server_ip", default=config.DEFAULT_SERVER_IP, help="服务器 IP 地址"
    )
    parser.add_argument(
        "--server_port", type=int, default=config.DEFAULT_PORT, help="服务器端口号"
    )
    args = parser.parse_args()

    client = ReliableUDPClient(args.server_ip, args.server_port)
    client.run()


if __name__ == "__main__":
    main()
