====================================================================
  UDP Reliable Transfer — GBN 协议实验 运行说明
====================================================================

一、运行环境
  - Python 3.8+
  - pandas（用于 RTT 统计汇总）
    pip install pandas
  - 操作系统：Windows / Linux / macOS 均可

二、文件清单
  udpserver.py    UDP 服务端（三次握手 + 四次挥手 + 丢包模拟 + 累积确认）
  udpclient.py    UDP 客户端（三次握手 + GBN 滑动窗口 + 超时重传 + 快速重传 + 统计）
  readme.txt      本文件

三、启动方式

  1) 先在 guest OS（或另一终端）启动 Server：
     python udpserver.py --host 0.0.0.0 --port 12345

     参数说明：
       --host   服务器监听地址，默认 0.0.0.0
       --port   服务器监听端口，默认 12345

  2) 再在 host OS 启动 Client：
     python udpclient.py --server_ip 127.0.0.1 --server_port 12345

     参数说明：
       --server_ip   服务器 IP 地址，默认 127.0.0.1
       --server_port 服务器端口号，默认 12345

四、可调参数（udpclient.py 顶部常量）

  TOTAL_PACKETS = 30      总发包数
  PACKET_SIZE_MIN = 40   每包最小字节数
  PACKET_SIZE_MAX = 80   每包最大字节数（随机 40-80B）
  WINDOW_SIZE = 5        发送窗口（包数）
  INITIAL_TIMEOUT = 0.3  初始超时 300ms
  DROP_RATE = 0.25       Server 丢包率（udpserver.py 中调整）

五、协议报文格式（统一 13B 头 + Payload）

  头部格式: [1B Flags][4B Seq][4B Ack][4B Length]

  Flags: SYN=0x01, ACK=0x02, FIN=0x04

  三次握手阶段:
    SYN:    [Flags=0x01][Seq=0][Ack=0][Len=4][StudentID(2B)+TotalPackets(2B)]
    SYNACK: [Flags=0x03][Seq=0][Ack=1][Len=0]
    ACK:    [Flags=0x02][Seq=1][Ack=1][Len=0]

  数据传输阶段 (GBN):
    DATA:   [Flags=0x02][Seq=N][Ack=0][Len=M][M B Data]
    ACK:    [Flags=0x02][Seq=0][Ack=N+1][Len=0]  (累积确认)

  四次挥手阶段:
    FIN(client):  [Flags=0x04][Seq=30][Ack=0][Len=0]
    ACK(server):  [Flags=0x02][Seq=0][Ack=0][Len=0]
    FIN(server):  [Flags=0x04][Seq=0][Ack=0][Len=0]
    ACK(client):  [Flags=0x02][Seq=0][Ack=0][Len=0]

  StudentID 计算: 学号后4位(2913) XOR 0x5A3C = 0x515D

六、GBN 协议要点

  - 窗口大小：5 包
  - 累积确认：Server 只接受按序到达的包，乱序丢弃
  - 超时重传：RTO 自适应（初始 300ms，TCP EWMA 算法：estimated_rtt + 4×dev_rtt，上下界 50ms~3s）
  - 快速重传：收到 3 次重复 ACK 立即重传窗口（不等超时）
  - 丢包模拟：Server 25% 概率不响应 DATA 报文
  - 独立接收线程：客户端使用独立线程异步接收 ACK，主线程负责发送

七、输出文件

  run_log.txt    运行日志（含时间戳，与 Wireshark 相互印证）

八、Wireshark 验证建议

  1. 启动 Wireshark，选择 loopback 接口（本地测试）或 Ethernet（VM 测试）
  2. Display Filter: udp.port == <你的端口>
  3. 重点观察：
     - 三次握手的三条 UDP 报文（SYN, SYN-ACK, ACK）
     - DATA 报文的 SeqNum 滑动
     - ACK 报文的累积确认值
     - 丢包事件对应的超时重传 + 快速重传
     - 四次挥手 FIN 报文交换

九、验收准备

  - 能解释 StudentID 计算过程：2913 XOR 0x5A3C = 0x515D
  - 能解释 GBN 窗口滑动机制：发送窗口 5 包、累积确认、超时重传整个窗口
  - 能解释快速重传触发条件：收到 3 次相同 ACK（重复 ACK）
  - 能解释 RTO 自适应算法：TCP EWMA（estimated_rtt = 0.875×旧 + 0.125×样本，dev_rtt = 0.75×旧 + 0.25×|样本-估计|），RTO = max(50ms, min(3s, estimated_rtt + 4×dev_rtt))
  - 能解释丢包率计算：丢包率 = (总发送次数 - 30) / 总发送次数 × 100%
  - 能解释四次挥手过程：Client FIN → Server ACK → Server FIN → Client ACK → 连接关闭
