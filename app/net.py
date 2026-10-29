"""网络小工具。

背景：部分环境（如某些家宽 NAS）的 DNS 会返回 IPv6 地址，但 IPv6 路由不可用，
表现为：
- 连接超时（curl: (28)）
- 或落到运营商 NAT64 网关，TLS 被替换成自签证书
  （[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate）

对策：解析时**优先 IPv4**，只有拿不到 A 记录时才回落到系统默认顺序。
"""
from __future__ import annotations

import socket
from typing import Any, Iterable

_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def _prefer_ipv4(host: str, port: Any, family: int = 0, type: int = 0,
                 proto: int = 0, flags: int = 0) -> Iterable:
    if family in (0, socket.AF_UNSPEC):
        try:
            infos = _ORIGINAL_GETADDRINFO(host, port, socket.AF_INET, type, proto, flags)
            if infos:
                return infos
        except OSError:
            pass
    return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)


def install_ipv4_preference() -> None:
    """让 getaddrinfo 优先返回 IPv4 结果（幂等，可重复调用）。"""
    if socket.getaddrinfo is _prefer_ipv4:
        return
    socket.getaddrinfo = _prefer_ipv4
