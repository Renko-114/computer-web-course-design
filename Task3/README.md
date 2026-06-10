# Task3 UDP SR (Selective Repeat) — 选择性重传

基于 UDP 实现 SR 协议，GBN 的升级版：乱序包缓存而非丢弃，超时只重传丢失的包。

## 与 Task2 GBN 的核心区别

| | GBN | SR |
|------|------|------|
| 接收端乱序处理 | 丢弃，重发当前 ACK | 缓存，逐个确认 |
| 超时重传粒度 | 整个窗口 | 只重传超时的单个包 |
| 窗口滑动方式 | 累积 ACK 一次滑多步 | 逐包确认，逐个滑动 |
| 定时器 | 一个全局 RTO | 每个未确认包独立超时检测 |
| 总发送次数（典型） | 70+ | 45-55 |

## 项目结构

- `config.py` — 网络配置、标志位常量、SR 参数
- `common.py` — 公共模块（线程安全日志、协议头打包）
- `sr_server.py` — SR 服务端（乱序缓存 + 逐个确认 + 滑窗）
- `sr_client.py` — SR 客户端（发送窗口 + 逐包超时检测 + 单独重传）

## 运行方式

```bash
# 启动 Server
python sr_server.py --port 12345

# 启动 Client
python sr_client.py --server_ip 127.0.0.1 --server_port 12345
```
