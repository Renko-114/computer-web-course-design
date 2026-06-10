# TCP Reverse — Socket 编程实验

基于 TCP 的文本反转服务，客户端将 ASCII 文件随机分块发送，服务端逐块反转返回。

## 项目结构

- `config.py` — 网络配置、报文常量、分块默认值
- `common.py` — 公共模块（精确接收、线程安全日志）
- `reversetcpclient.py` — TCP 客户端（分块发送 + 反转接收）
- `reversetcpserver.py` — TCP 服务端（多线程并发）
- `sample.txt` — 测试用 ASCII 文本文件

## 运行环境

- Python 3.8+
- 仅使用标准库（socket, struct, threading, random, argparse），无需额外安装

## 启动方式

### 1. 启动服务端

```bash
python reversetcpserver.py --host 0.0.0.0 --port 12345
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `12345` | 监听端口 |

### 2. 启动客户端

```bash
python reversetcpclient.py --server_ip 127.0.0.1 --server_port 12345 \
    --file_path sample.txt --lmin 50 --lmax 100 --chunk_seed 42
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--server_ip` | `127.0.0.1` | 服务器 IP |
| `--server_port` | `12345` | 服务器端口 |
| `--file_path` | `sample.txt` | 待发送 ASCII 文件 |
| `--lmin` | `50` | 每块最小字节数 |
| `--lmax` | `100` | 每块最大字节数 |
| `--chunk_seed` | `42` | 随机分块种子（复现用） |

## 协议报文格式 (Big-Endian)

| 报文 | 格式 |
|------|------|
| Initialization | `[1B Type=1][4B N]` |
| agree | `[1B Type=2]` |
| reverseRequest | `[1B Type=3][4B Length][Data]` |
| reverseAnswer | `[1B Type=4][4B Length][Data]` |

## 输出文件

- `run_log.txt` — 运行日志（含毫秒时间戳，与 Wireshark 相互印证）
- `<原文件名>_reversed.txt` — 完整反转结果（如 `sample_reversed.txt`）

## 分块算法

`chunk_seed` 固定随机种子，循环生成 `[Lmin, Lmax]` 随机块长，剩余 ≤ Lmax 时作为最后一块。给定相同参数可复现相同分块。

## 并发

Server 为每个客户端连接创建独立线程，日志使用 `threading.Lock` 保证线程安全。
