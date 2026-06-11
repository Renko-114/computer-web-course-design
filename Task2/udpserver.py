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

from common import log_event, pack_header, unpack_header

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")

def verify_student_id(received: int) -> bool:
    """验证学号：received XOR 0x5A3C 应在 [0, 9999] 范围内"""
    result = received ^ config.STUDENT_ID_MASK
    return 0 <= result <= 9999


class ReliableUDPClientHandler:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.client_addr = None
        self.total_pkts = 0
        self.connected = False

    def run(self):
        self._establish_connection()
        if not self.connected:
            return
        self._receive_data()
        self._terminate_connection()

    def _verify_student_id(self, received: int) -> bool:
        """验证学号：received XOR 0x5A3C 应在 [0, 9999] 范围内"""
        result = received ^ config.STUDENT_ID_MASK
        return 0 <= result <= 9999
    
    def _establish_connection(self):
        # ════════════════ Phase 1: 三次握手 ════════════════
        while True:
            data, self.client_addr = self.sock.recvfrom(4096)
            flags, seq, ack, length = unpack_header(data[:13])
            if flags is None:
                continue
            payload = data[13:]

            if not (flags & config.FLAG_SYN):
                continue

            # 从 SYN payload 提取 StudentID(2B) + TotalPackets(2B)
            if len(payload) < 4:
                continue
            student_id, self.total_pkts = struct.unpack("!HH", payload[:4])

            if self._verify_student_id(student_id):
                log_event(LOG_PATH,
                    "[{}] 收到 SYN, StudentID={:#x} 验证通过, 共{}包",
                    self.client_addr,
                    student_id,
                    self.total_pkts,
                )

                # 发送 SYN-ACK
                synack = pack_header(config.FLAG_SYN | config.FLAG_ACK, 0, 1, 0)
                self.sock.sendto(synack, self.client_addr)
                log_event(LOG_PATH, "[{}] 发送 SYN-ACK", self.client_addr)

                # 等待连接确认 ACK（5s 超时，忽略非 ACK 报文）
                self.sock.settimeout(5.0)
                while True:
                    try:
                        data2, _ = self.sock.recvfrom(4096)
                        ack_flags, _, ack_num, _ = unpack_header(data2[:13])
                        if ack_flags & config.FLAG_ACK and ack_num == 1:
                            log_event(LOG_PATH,
                                "[{}] 收到连接确认 ACK, 进入数据传输阶段", self.client_addr
                            )
                            self.connected = True
                            break
                    except socket.timeout:
                        log_event(LOG_PATH, "[{}] 等待握手 ACK 超时，关闭连接", self.client_addr)
                        self.sock.close()
                        return
                break
            else:
                log_event(LOG_PATH, 
                    "[{}] StudentID={:#x} 验证失败，拒绝连接", self.client_addr, student_id
                )
                
    def _receive_data(self):
        # ════════════════ Phase 2: 数据传输（GBN 接收端） ════════════════
        expected_seq = 0
        rng = random.Random()
        self.sock.settimeout(10.0)  # 10s 无数据则超时退出

        while expected_seq < self.total_pkts:
            try:
                data, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                log_event(LOG_PATH, "长时间未收到数据，Server 退出")
                break

            flags, seq, ack, data_len = unpack_header(data[:13])
            if flags is None:
                continue
            payload = data[13 : 13 + data_len]

            if not (flags & config.FLAG_ACK):
                continue

            # 模拟丢包
            if rng.random() < config.DROP_RATE:
                log_event(LOG_PATH, "丢弃 第{}个数据包 seq={}（模拟丢包）", seq + 1, seq)
                continue

            if seq == expected_seq:
                expected_seq += 1
                log_event(LOG_PATH, 
                    "接收 第{}个数据包 seq={} ({}B), 发送累积ACK={}",
                    seq + 1,
                    seq,
                    data_len,
                    expected_seq,
                )

                ack_pkt = pack_header(config.FLAG_ACK, 0, expected_seq, 0)
                self.sock.sendto(ack_pkt, self.client_addr)
            else:
                log_event(LOG_PATH, 
                    "丢弃乱序包 seq={} (期望 seq={}), 重发 ACK={}",
                    seq + 1,
                    expected_seq + 1,
                    expected_seq,
                )

                ack_pkt = pack_header(config.FLAG_ACK, 0, expected_seq, 0)
                self.sock.sendto(ack_pkt, self.client_addr)

        if expected_seq >= self.total_pkts:
            log_event(LOG_PATH, "全部 {} 个数据包接收完毕", self.total_pkts)
        else:
            log_event(LOG_PATH, 
                "数据传输中断，期望 seq={}，实际收到 {} 包", expected_seq, expected_seq
            )
            
    def _terminate_connection(self):
        # ════════════════ Phase 3: 四次挥手 ════════════════
        self.sock.settimeout(10.0)
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
                flags, _, _, _ = unpack_header(data[:13])
                if flags is None:
                    continue
                if flags & config.FLAG_FIN:
                    log_event(LOG_PATH, "收到 FIN，发送 ACK")
                    # Step 2: ACK
                    ack_pkt = pack_header(config.FLAG_ACK, 0, 0, 0)
                    self.sock.sendto(ack_pkt, self.client_addr)
                    # Step 3: FIN
                    fin_pkt = pack_header(config.FLAG_FIN, 0, 0, 0)
                    self.sock.sendto(fin_pkt, self.client_addr)
                    log_event(LOG_PATH, "发送 FIN 确认关闭")
                    # Step 4: Wait for final ACK
                    self.sock.settimeout(2.0)
                    try:
                        data2, _ = self.sock.recvfrom(4096)
                    except socket.timeout:
                        pass
                    break
            except socket.timeout:
                log_event(LOG_PATH, "等待 FIN 超时，Server 直接关闭")
                break
        self.sock.close()


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
    log_event(LOG_PATH, "Server 启动，监听 UDP {}:{}", args.host, args.port)
    
    handler = ReliableUDPClientHandler(sock)
    handler.run()


if __name__ == "__main__":
    main()
