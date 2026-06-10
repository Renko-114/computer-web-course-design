# UDP GBN Reliable Transfer — 模拟 TCP 可靠传输

基于 UDP 实现 GBN（Go-Back-N）协议，模拟 TCP 的三次握手、滑动窗口、超时重传、快速重传、四次挥手。

## 项目结构

- `config.py` — 网络配置、标志位常量、GBN 可调参数
- `common.py` — 公共模块（线程安全日志、协议头打包）
- `udpserver.py` — UDP 服务端（三次握手 + 累积确认 + 丢包模拟 + 四次挥手）
- `udpclient.py` — UDP 客户端（GBN 滑动窗口 + 超时重传 + 快速重传 + 统计）
- `requirements.txt` — Python 依赖

## 运行环境

- Python 3.8+
- 依赖安装：`pip install -r requirements.txt`

## 启动方式

### 1. 启动服务端

```bash
python udpserver.py --host 0.0.0.0 --port 12345
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `12345` | 监听端口 |

### 2. 启动客户端

```bash
python udpclient.py --server_ip 127.0.0.1 --server_port 12345
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--server_ip` | `127.0.0.1` | 服务器 IP |
| `--server_port` | `12345` | 服务器端口 |

## 协议报文格式（统一 13B 头）

```
[1B Flags][4B Seq][4B Ack][4B Length] + Payload
```

### Flags 定义

| Flag | 值 | 含义 |
|------|----|------|
| `SYN` | `0x01` | 握手 |
| `ACK` | `0x02` | 确认 |
| `FIN` | `0x04` | 挥手 |

### 通信流程

**三次握手：**
1. Client → SYN（Payload 含 StudentID=2913 XOR 0x5A3C）
2. Server → SYN|ACK
3. Client → ACK

**数据传输 (GBN)：**
- DATA: `[Flags=ACK][Seq=N][Ack=0][Len=M][M B Data]`
- ACK: `[Flags=ACK][Seq=0][Ack=N+1][Len=0]`（累积确认）

**四次挥手：**
1. Client → FIN
2. Server → FIN

## GBN 协议要点

| 机制 | 说明 |
|------|------|
| 滑动窗口 | 5 包窗口，base/next_seq 双指针 |
| 累积确认 | Server 只接受按序包，乱序丢弃 |
| 超时重传 | RTO 自适应（初始 300ms，基于最近 5 次 RTT × 5） |
| 快速重传 | 3 次重复 ACK 立即重传窗口 |
| 丢包模拟 | Server 25% 概率不响应（`config.DROP_RATE`） |
| 独立接收线程 | 异步接收 ACK 入队列，主线程轮询发送 |

## 输出文件

- `run_log.txt` — 运行日志（含毫秒时间戳，与 Wireshark 相互印证）

## Wireshark 验证

```bash
# Display Filter
udp.port == 12345
```

重点观察：三次握手 → DATA/ACK 交互 → 丢包重传 → 四次挥手。

## 验收要点

- **StudentID 计算**：`2913 XOR 0x5A3C = 0x515D`
- **RTO 算法**：`max(50ms, min(3s, avgRTT × 5))`
- **丢包率**：`(总发送次数 - 30) / 总发送次数 × 100%`
- **GBN 窗口滑动**：窗口 5 包，累积 ACK，超时回退整个窗口
