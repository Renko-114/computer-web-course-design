"""Task1 TCP Reverse — 配置常量"""

# 网络配置
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 12345
DEFAULT_SERVER_IP = "127.0.0.1"

# 分块默认值
DEFAULT_LMIN = 50
DEFAULT_LMAX = 100
DEFAULT_CHUNK_SEED = 42
DEFAULT_FILE = "sample.txt"

# 报文类型常量
TYPE_INIT = 1
TYPE_AGREE = 2
TYPE_REQUEST = 3
TYPE_ANSWER = 4

# 协议字段长度 (字节, Big-Endian)
TYPE_LEN = 1  # Type: unsigned char (!B)
N_LEN = 4  # N/Length: unsigned int (!I)
HEADER_LEN = TYPE_LEN + N_LEN  # 5
