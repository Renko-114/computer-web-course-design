====================================================================
  TCP Reverse — Socket 编程实验 运行说明
====================================================================

一、运行环境
  - Python 3.8+
  - 操作系统：Windows / Linux / macOS 均可
  - 无需额外第三方库（仅使用标准库 socket, struct, threading, random）

二、文件清单
  reversetcpserver.py    TCP 服务端（支持多客户端并发）
  reversetcpclient.py    TCP 客户端（分块发送 + 反转接收）
  common.py              公共模块（报文常量、精确接收、线程安全日志）
  sample.txt             测试用 ASCII 文本文件
  readme.txt             本文件

三、启动方式

  1) 先在 guest OS（或另一终端）启动 Server：
     python reversetcpserver.py --host 0.0.0.0 --port 12345

     参数说明：
       --host   服务器监听地址，默认 0.0.0.0
       --port   服务器监听端口，默认 12345

  2) 再在 host OS 启动 Client：
     python reversetcpclient.py --server_ip 127.0.0.1 --server_port 12345 \
         --file_path sample.txt --lmin 50 --lmax 100 --chunk_seed 42

     参数说明：
       --server_ip   服务器 IP 地址，默认 127.0.0.1
       --server_port 服务器端口号，默认 12345
       --file_path   待发送的全英文 ASCII 文本文件路径，默认 sample.txt
       --lmin        每块最小字节数，默认 50
       --lmax        每块最大字节数，默认 100
       --chunk_seed  随机分块种子（用于复现分块结果，便于验收验证），默认 42

四、输出文件
  run_log.txt                运行日志（含时间戳，与 Wireshark 抓包可相互印证）
  <原文件名>_reversed.txt    原始文件的完整反转结果（如 sample_reversed.txt）

五、协议报文格式 (Big-Endian 字节序)
  Initialization:  | Type(1B)=1 | N(4B)            |
  agree:           | Type(1B)=2                     |
  reverseRequest:  | Type(1B)=3 | Length(4B) | Data |
  reverseAnswer:   | Type(1B)=4 | Length(4B) | Data |

六、分块算法说明
  见 reversetcpclient.py 中 split_file() 函数。
  用 chunk_seed 初始化随机数生成器，循环取 [Lmin, Lmax] 随机值，
  剩余 ≤ Lmax 时作为最后一块。算法确定性保证给定相同参数可复现相同分块。

七、并发说明
  Server 为每个客户端连接创建独立线程处理，支持 ≥2 个客户端同时请求。
  日志写入使用 threading.Lock 保证线程安全。
