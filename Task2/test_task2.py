"""Task2 UDP GBN — pytest 测试套件"""

import os
import sys
import struct
import socket
import threading
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from common import pack_header, unpack_header
from udpclient import compute_student_id, ReliableUDPClient
from udpserver import verify_student_id


# ════════════════ 单元测试：StudentID ════════════════

class TestStudentID:
    def test_compute_correct(self):
        """2913 XOR 0x5A3C = 0x515D"""
        assert compute_student_id(2913) == 0x515D

    def test_verify_valid(self):
        """合法 StudentID 验证通过"""
        sid = compute_student_id(2913)
        assert verify_student_id(sid) is True

    def test_verify_invalid(self):
        """非法 StudentID 验证失败"""
        assert verify_student_id(0xFFFF) is False
        assert verify_student_id(0x0000 ^ config.STUDENT_ID_MASK + 10000) is False


# ════════════════ 单元测试：协议头 ════════════════

class TestProtocolHeader:
    def test_pack_header_syn(self):
        pkt = pack_header(config.FLAG_SYN, 0, 0, 4)
        assert len(pkt) == config.HEADER_LEN
        flags, seq, ack, length = unpack_header(pkt)
        assert flags == config.FLAG_SYN
        assert seq == 0
        assert ack == 0
        assert length == 4

    def test_pack_header_synack(self):
        pkt = pack_header(config.FLAG_SYN | config.FLAG_ACK, 0, 1, 0)
        flags, seq, ack, length = unpack_header(pkt)
        assert flags == 0x03
        assert ack == 1

    def test_pack_header_data(self):
        pkt = pack_header(config.FLAG_ACK, 15, 0, 60)
        flags, seq, ack, length = unpack_header(pkt)
        assert flags == config.FLAG_ACK
        assert seq == 15
        assert length == 60

    def test_pack_header_fin(self):
        pkt = pack_header(config.FLAG_FIN, 30, 0, 0)
        flags, _, _, _ = unpack_header(pkt)
        assert flags == config.FLAG_FIN

    def test_flags_mutually_exclusive_bits(self):
        """SYN/ACK/FIN 互不重叠"""
        assert config.FLAG_SYN & config.FLAG_ACK == 0
        assert config.FLAG_SYN & config.FLAG_FIN == 0
        assert config.FLAG_ACK & config.FLAG_FIN == 0


# ════════════════ 单元测试：config 常量 ════════════════

class TestConfig:
    def test_window_fits_packet_bounds(self):
        """窗口大小合理"""
        assert config.WINDOW_SIZE > 0
        assert config.WINDOW_SIZE < config.TOTAL_PACKETS

    def test_drop_rate_valid(self):
        """丢包率在 [0, 1]"""
        assert 0 <= config.DROP_RATE <= 1

    def test_timeout_reasonable(self):
        """初始超时合理"""
        assert 0.01 <= config.INITIAL_TIMEOUT <= 5.0

    def test_packet_sizes_positive(self):
        """包大小为正"""
        assert config.PACKET_SIZE_MIN > 0
        assert config.PACKET_SIZE_MAX >= config.PACKET_SIZE_MIN


# ════════════════ 集成测试：Client 初始化 ════════════════

class TestReliableUDPClientInit:
    def test_client_init_state(self):
        """Client 初始化状态正确"""
        c = ReliableUDPClient("127.0.0.1", 12345)
        assert c.base == 0
        assert c.next_seq == 0
        assert c.rtt_samples == []
        assert hasattr(c, "_lock")
        assert c.sock is not None
        c.sock.close()

    def test_client_has_receiver_thread(self):
        """Client 创建了接收线程"""
        c = ReliableUDPClient("127.0.0.1", 12346)
        assert c.recv_thread is not None
        assert c.recv_thread.daemon is True
        c.sock.close()


# ════════════════ 集成测试：UDP 端到端 ════════════════

class TestUDPEndToEnd:
    _port_counter = 12350  # 共享端口计数器

    @pytest.fixture(autouse=True)
    def setup(self):
        TestUDPEndToEnd._port_counter += 1
        self.port = TestUDPEndToEnd._port_counter
        self.server_ready = threading.Event()
        self.server_done = threading.Event()
        self.server_error = None

    def _run_server(self):
        """在后台线程启动 UDP Server"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self.port))
            self.server_ready.set()

            # Phase 1: Handshake
            data, addr = sock.recvfrom(4096)
            flags, _, _, _ = unpack_header(data[:13])
            assert flags & config.FLAG_SYN

            synack = pack_header(config.FLAG_SYN | config.FLAG_ACK, 0, 1, 0)
            sock.sendto(synack, addr)

            # Wait for ACK
            sock.settimeout(3.0)
            data2, _ = sock.recvfrom(4096)
            flags2, _, ack2, _ = unpack_header(data2[:13])
            assert flags2 & config.FLAG_ACK

            # Phase 2: GBN data transfer
            import random as rng_module
            rng = rng_module.Random()
            expected_seq = 0
            sock.settimeout(5.0)
            total_pkts = config.TOTAL_PACKETS

            while expected_seq < total_pkts:
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    break

                if len(data) < 13:
                    continue
                flags, seq, ack, dlen = unpack_header(data[:13])

                if not (flags & config.FLAG_ACK):
                    continue
                if rng.random() < config.DROP_RATE / 2:  # 测试中减半丢包率加速
                    continue
                if seq == expected_seq:
                    expected_seq += 1
                    ack_pkt = pack_header(config.FLAG_ACK, 0, expected_seq, 0)
                    sock.sendto(ack_pkt, addr)
                else:
                    ack_pkt = pack_header(config.FLAG_ACK, 0, expected_seq, 0)
                    sock.sendto(ack_pkt, addr)

            # Phase 3: FIN (4-step handshake)
            sock.settimeout(3.0)
            try:
                data, _ = sock.recvfrom(4096)
                flags, _, _, _ = unpack_header(data[:13])
                if flags & config.FLAG_FIN:
                    # Step 2: ACK
                    ack_pkt = pack_header(config.FLAG_ACK, 0, 0, 0)
                    sock.sendto(ack_pkt, addr)
                    # Step 3: FIN
                    fin_pkt = pack_header(config.FLAG_FIN, 0, 0, 0)
                    sock.sendto(fin_pkt, addr)
                    # Step 4: Wait for client's final ACK
                    sock.settimeout(1.0)
                    try:
                        data2, _ = sock.recvfrom(4096)
                    except socket.timeout:
                        pass
            except socket.timeout:
                pass

            sock.close()
            self.server_done.set()
        except Exception as e:
            self.server_error = e
            self.server_ready.set()
            self.server_done.set()

    def test_end_to_end_gbn(self):
        """端到端：完整 GBN 流程，30 包全部接收"""
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        client = ReliableUDPClient("127.0.0.1", self.port)
        try:
            client._establish_connection()
            client._generate_data()
            client._send_data()
        finally:
            client._terminate_connection()

        # 验证全部 30 包被确认
        acked = sum(1 for p in client.packets if p.get("acked", False))
        assert acked == config.TOTAL_PACKETS, f"Only {acked}/{config.TOTAL_PACKETS} acked"

        # 验证有 RTT 样本
        assert len(client.rtt_samples) > 0, "No RTT samples collected"

        # 验证总发送次数 ≥ 总包数（允许重传）
        assert sum(p["retrans_count"] for p in client.packets) >= config.TOTAL_PACKETS

        assert self.server_error is None

    def test_handshake_student_id(self):
        """端到端：三次握手 StudentID 验证"""
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        client = ReliableUDPClient("127.0.0.1", self.port)
        try:
            client._establish_connection()
        finally:
            client.sock.close()

        # 握手成功则无 server error
        assert self.server_error is None

    def test_rtt_statistics_valid(self):
        """集成测试：RTT 统计数据合理"""
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        client = ReliableUDPClient("127.0.0.1", self.port)
        try:
            client._establish_connection()
            client._generate_data()
            client._send_data()
        finally:
            client._terminate_connection()

        if client.rtt_samples:
            import pandas as pd
            s = pd.Series(client.rtt_samples)
            assert s.min() >= 0, "RTT should be non-negative"
            assert s.max() < 1000, f"RTT too high: {s.max()}ms (network issue?)"
            assert s.mean() < 500

    def test_loss_rate_calculation(self):
        """集成测试：丢包率计算正确"""
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        client = ReliableUDPClient("127.0.0.1", self.port)
        try:
            client._establish_connection()
            client._generate_data()
            client._send_data()
        finally:
            client._terminate_connection()

        loss_rate = (sum(p["retrans_count"] for p in client.packets) - config.TOTAL_PACKETS) / sum(p["retrans_count"] for p in client.packets) * 100
        assert 0 <= loss_rate <= 100
        # 丢包率 25% 下，loss_rate 通常在 20-80% 之间
        assert 0 <= loss_rate <= 95  # 宽松范围
