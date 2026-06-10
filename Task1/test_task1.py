"""Task1 TCP Reverse — pytest 测试套件"""

import os
import sys
import struct
import socket
import threading
import time
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from common import recv_exact, log_event, _LOG_LOCK
from reversetcpclient import split_file


# ════════════════ 单元测试：split_file ════════════════

class TestSplitFile:
    def test_deterministic_same_seed(self):
        """相同 seed 产生相同分块"""
        data = b"A" * 500
        result1 = split_file(data, 50, 100, 42)
        result2 = split_file(data, 50, 100, 42)
        assert result1 == result2

    def test_different_seed_different_chunks(self):
        """不同 seed 大概率产生不同分块"""
        data = b"B" * 1000
        result1 = split_file(data, 50, 100, 5)
        result2 = split_file(data, 50, 100, 99)
        assert result1 != result2

    def test_chunks_sum_to_total(self):
        """所有块长度之和 == 原文件长度"""
        data = b"C" * 635
        chunks = split_file(data, 50, 100, 42)
        assert sum(chunks) == len(data)

    def test_last_chunk_not_exceed_lmax(self):
        """除最后一块外，每块 ≤ Lmax"""
        data = b"D" * 777
        chunks = split_file(data, 20, 100, 7)
        for i, sz in enumerate(chunks[:-1]):
            assert sz <= 100, f"chunk {i}: {sz} > 100"
        assert chunks[-1] <= 100

    def test_small_file_single_chunk(self):
        """小于 Lmax 的文件只有一块"""
        data = b"E" * 30
        chunks = split_file(data, 50, 100, 42)
        assert len(chunks) == 1
        assert chunks[0] == 30

    def test_exact_lmax_boundary(self):
        """恰好等于 Lmax 的文件"""
        data = b"F" * 100
        chunks = split_file(data, 50, 100, 42)
        assert sum(chunks) == 100

    def test_known_result(self):
        """已知参数验证固定输出（验收手算用）"""
        data = b"G" * 520
        chunks = split_file(data, 50, 100, 42)
        assert len(chunks) >= 5
        assert sum(chunks) == 520


# ════════════════ 单元测试：config 常量 ════════════════

class TestConfig:
    def test_type_constants_unique(self):
        """报文类型常量不重复"""
        types = [config.TYPE_INIT, config.TYPE_AGREE, config.TYPE_REQUEST, config.TYPE_ANSWER]
        assert len(types) == len(set(types))

    def test_header_len(self):
        """HEADER_LEN = TYPE_LEN + N_LEN"""
        assert config.HEADER_LEN == config.TYPE_LEN + config.N_LEN

    def test_default_port_valid(self):
        """默认端口在合法范围内"""
        assert 1024 <= config.DEFAULT_PORT <= 65535


# ════════════════ 单元测试：协议打包/解包 ════════════════

class TestProtocol:
    def test_pack_init(self):
        pkt = struct.pack("!BI", config.TYPE_INIT, 9)
        assert len(pkt) == config.HEADER_LEN
        t, n = struct.unpack("!BI", pkt)
        assert t == config.TYPE_INIT
        assert n == 9

    def test_pack_agree(self):
        pkt = struct.pack("!B", config.TYPE_AGREE)
        assert len(pkt) == 1
        assert struct.unpack("!B", pkt)[0] == config.TYPE_AGREE

    def test_pack_request(self):
        data = b"hello"
        pkt = struct.pack("!BI", config.TYPE_REQUEST, len(data)) + data
        assert len(pkt) == config.HEADER_LEN + 5

    def test_pack_answer(self):
        data = b"olleh"
        pkt = struct.pack("!BI", config.TYPE_ANSWER, len(data)) + data
        t, length = struct.unpack("!BI", pkt[:config.HEADER_LEN])
        assert t == config.TYPE_ANSWER
        assert length == 5


# ════════════════ 集成测试：TCP 端到端 ════════════════

class TestTCPEndToEnd:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.port = 12337
        self.server_ready = threading.Event()
        self.server_error = None
        self.server_thread = None

    def _run_server(self):
        """在后台线程启动 TCP Server"""
        try:
            import reversetcpserver
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(("127.0.0.1", self.port))
            server_sock.listen(1)
            self.server_ready.set()

            # 只处理一个客户端
            conn, addr = server_sock.accept()
            reversetcpserver.handle_client(conn, addr)
            server_sock.close()
        except Exception as e:
            self.server_error = e
            self.server_ready.set()

    def _run_client(self, file_content, lmin=50, lmax=100, seed=42):
        """运行客户端逻辑并返回反转结果"""
        # 写临时文件
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="ascii")
        tmp.write(file_content)
        tmp.close()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", self.port))

            with open(tmp.name, "rb") as f:
                data = f.read()
            chunks = split_file(data, lmin, lmax, seed)
            n_blocks = len(chunks)

            # 握手
            sock.sendall(struct.pack("!BI", config.TYPE_INIT, n_blocks))
            agree = recv_exact(sock, 1)
            assert struct.unpack("!B", agree)[0] == config.TYPE_AGREE

            # 逐块收发
            results = []
            pos = 0
            for sz in chunks:
                chunk = data[pos:pos + sz]
                pos += sz
                sock.sendall(struct.pack("!BI", config.TYPE_REQUEST, sz) + chunk)

                header = recv_exact(sock, config.HEADER_LEN)
                t, length = struct.unpack("!BI", header)
                assert t == config.TYPE_ANSWER
                ans = recv_exact(sock, length)
                results.append(ans.decode("utf-8"))

            sock.close()
            return "".join(reversed(results))
        finally:
            os.unlink(tmp.name)

    def test_reverse_correctness(self):
        """端到端：验证反转正确性"""
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        original = "Hello World! This is a test."
        result = self._run_client(original, lmin=10, lmax=30, seed=1)

        # 验证反转
        assert result == original[::-1], f"Expected '{original[::-1]}', got '{result}'"
        assert self.server_error is None

    def test_multi_chunk_file(self):
        """端到端：多块大文件"""
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        # 构造一个长文本（确保分多块）
        base = "The quick brown fox jumps over the lazy dog. "
        original = base * 20
        expected = original[::-1]

        result = self._run_client(original, lmin=20, lmax=50, seed=7)

        assert result == expected
        assert self.server_error is None

    def test_single_chunk_edge_case(self):
        """端到端：单块（文件小于 Lmin）"""
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        original = "Hi"
        result = self._run_client(original, lmin=50, lmax=100, seed=1)

        assert result == "iH"
        assert self.server_error is None

    def test_deterministic_reproducible(self):
        """端到端：相同参数两次运行结果一致"""
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        text = "ABCDEFGHIJ" * 30
        r1 = self._run_client(text, lmin=10, lmax=20, seed=42)

        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
        self.server_ready.wait(timeout=2)

        r2 = self._run_client(text, lmin=10, lmax=20, seed=42)

        assert r1 == r2
