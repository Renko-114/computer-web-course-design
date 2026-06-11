"""Task3 UDP SR — pytest 测试套件"""

import os
import sys
import struct
import socket
import threading
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from common import pack_header
from sr_client import compute_student_id, SRClient
from sr_server import verify_student_id


class TestStudentID:
    def test_compute(self):
        assert compute_student_id(2913) == 0x515D

    def test_verify_ok(self):
        assert verify_student_id(compute_student_id(2913))

    def test_verify_bad(self):
        assert not verify_student_id(0xFFFF)


class TestClientInit:
    def test_init_state(self):
        c = SRClient("127.0.0.1", 12345)
        assert c.base == 0
        assert c.next_seq == 0
        assert c.rtt_samples == []
        c.sock.close()


class TestSREndToEnd:
    _port = 12330

    @pytest.fixture(autouse=True)
    def setup(self):
        TestSREndToEnd._port += 1
        self.port = TestSREndToEnd._port
        self.ready = threading.Event()
        self.err = None

    def _run_server(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", self.port))
            self.ready.set()
            data, addr = sock.recvfrom(4096)
            synack = pack_header(config.FLAG_SYN | config.FLAG_ACK, 0, 1, 0)
            sock.sendto(synack, addr)
            sock.settimeout(3.0)
            data2, _ = sock.recvfrom(4096)
            sock.settimeout(5.0)
            import random as rng_module
            rng = rng_module.Random()
            base = 0
            buf = {}
            total = config.TOTAL_PACKETS
            while base < total:
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    break
                if len(data) < 13:
                    continue
                flags, seq, ack, dlen = unpack_header(data[:13])
                if not (flags & config.FLAG_ACK):
                    continue
                if rng.random() < config.DROP_RATE / 2:
                    continue
                if base <= seq < base + config.WINDOW_SIZE:
                    buf[seq] = True
                    ack_pkt = pack_header(config.FLAG_ACK, 0, seq, 0)
                    sock.sendto(ack_pkt, addr)
                    while base in buf:
                        base += 1
                elif seq < base:
                    ack_pkt = pack_header(config.FLAG_ACK, 0, seq, 0)
                    sock.sendto(ack_pkt, addr)
            sock.settimeout(3.0)
            try:
                data, _ = sock.recvfrom(4096)
                flags, _, _, _ = unpack_header(data[:13])
                if flags & config.FLAG_FIN:
                    # Step 2: ACK
                    ack_pkt = pack_header(config.FLAG_ACK, 0, 0, 0)
                    sock.sendto(ack_pkt, addr)
                    # Step 3: FIN
                    sock.sendto(pack_header(config.FLAG_FIN, 0, 0, 0), addr)
                    # Step 4: Wait for client's final ACK
                    sock.settimeout(1.0)
                    try:
                        data2, _ = sock.recvfrom(4096)
                    except socket.timeout:
                        pass
            except socket.timeout:
                pass
            sock.close()
        except Exception as e:
            self.err = e
            self.ready.set()

    def test_sr_completes(self):
        t = threading.Thread(target=self._run_server, daemon=True)
        t.start()
        self.ready.wait(timeout=2)
        c = SRClient("127.0.0.1", self.port)
        try:
            c._establish_connection()
            c._send_data()
            acked = sum(1 for p in c.packets if p["acked"])
            assert acked == config.TOTAL_PACKETS
            assert c.total_sends >= config.TOTAL_PACKETS
        finally:
            c._terminate_connection()
        assert self.err is None
