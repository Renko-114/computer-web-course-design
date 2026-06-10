"""Task2 UDP GBN — 配置常量"""

# 网络配置
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 12345
DEFAULT_SERVER_IP = "127.0.0.1"

# 报文类型 (bit flags)
FLAG_SYN = 0x01
FLAG_ACK = 0x02
FLAG_FIN = 0x04

# 学号校验: 后4位(2913) XOR 0x5A3C = 0x515D
STUDENT_ID_LAST4 = 2913
STUDENT_ID_MASK = 0x5A3C

# 协议头长度 (字节)
# [1B Flags][4B Seq][4B Ack][4B Length]
HEADER_LEN = 13

# GBN 可调参数
TOTAL_PACKETS = 30  # 总发包数
PACKET_SIZE_MIN = 40  # 每包最小字节数
PACKET_SIZE_MAX = 80  # 每包最大字节数
WINDOW_SIZE = 5  # 发送窗口（包数）
INITIAL_TIMEOUT = 0.3  # 初始超时 (秒)

# 快速重传阈值（重复 ACK 次数）
FAST_RETX_THRESHOLD = 3

# Server 丢包率
DROP_RATE = 0.25
