"""
TCP Reverse Server — 接收客户端分块文本，反转后返回。
支持多客户端并发，生成运行日志 run_log.txt。

协议报文 (Big-Endian):
  Initialization (client→server): [1B Type=1][4B N]
  agree        (server→client): [1B Type=2]
  reverseRequest (client→server): [1B Type=3][4B Length][Length B Data]
  reverseAnswer  (server→client): [1B Type=4][4B Length][Length B reverseData]
"""

import socket
import struct
import threading
import os
import argparse

import config
from common import log_event, recv_exact

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")


def handle_client(conn: socket.socket, addr: tuple) -> None:
    """处理单个客户端请求"""
    client_tag = f"{addr[0]}:{addr[1]}"
    log_event(LOG_PATH, "新客户端连接: {}", client_tag)

    try:
        # 1) 接收 Initialization 报文
        header = recv_exact(conn, config.HEADER_LEN)  # 1B Type + 4B N
        msg_type, n_blocks = struct.unpack("!BI", header)
        if msg_type != config.TYPE_INIT:
            log_event(LOG_PATH, "[{}] 期望 Initialization 报文，收到 Type={}", client_tag, msg_type)
            conn.close()
            return
        log_event(LOG_PATH, "[{}] 收到 Initialization: N={}", client_tag, n_blocks)

        # 2) 发送 agree 报文
        conn.sendall(struct.pack("!B", config.TYPE_AGREE))
        log_event(LOG_PATH, "[{}] 发送 agree", client_tag)

        # 3) 逐块处理 reverseRequest
        for i in range(n_blocks):
            header = recv_exact(conn, config.HEADER_LEN)  # 1B Type + 4B Length
            msg_type, data_len = struct.unpack("!BI", header)
            if msg_type != config.TYPE_REQUEST:
                log_event(LOG_PATH, "[{}] 期望 reverseRequest，收到 Type={}", client_tag, msg_type)
                conn.close()
                return

            data = recv_exact(conn, data_len)
            text = data.decode("utf-8")
            reversed_text = text[::-1]
            reversed_data = reversed_text.encode("utf-8")

            log_event(LOG_PATH, "[{}] 第{}块: 收到 {}B, 反转后发回", client_tag, i + 1, data_len)

            # 发送 reverseAnswer
            answer = struct.pack("!BI", config.TYPE_ANSWER, len(reversed_data)) + reversed_data
            conn.sendall(answer)

        log_event(LOG_PATH, "[{}] 全部 {} 块处理完成，连接关闭", client_tag, n_blocks)

    except ConnectionError as e:
        log_event(LOG_PATH, "[{}] 连接异常: {}", client_tag, e)
    except Exception as e:
        log_event(LOG_PATH, "[{}] 错误: {}", client_tag, e)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP Reverse Server")
    parser.add_argument("--host", default=config.DEFAULT_HOST, help="服务器监听地址")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT, help="服务器监听端口")
    args = parser.parse_args()

    # 清空旧日志
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((args.host, args.port))
    server_socket.listen(5)

    log_event(LOG_PATH, "Server 启动，监听 {}:{}", args.host, args.port)

    try:
        while True:
            conn, addr = server_socket.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        log_event(LOG_PATH, "Server 关闭")
    finally:
        server_socket.close()


if __name__ == "__main__":
    main()
