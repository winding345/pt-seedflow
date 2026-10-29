"""IPv4 优先的解析兜底测试（离线，替换底层解析）。"""
import socket

import pytest

from app import net


@pytest.fixture()
def fake_resolver(monkeypatch):
    calls = []

    def fake(host, port, family=0, type=0, proto=0, flags=0):
        calls.append(family)
        if family == socket.AF_INET:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))]
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    monkeypatch.setattr(net, "_ORIGINAL_GETADDRINFO", fake)
    return calls


def test_prefers_ipv4(fake_resolver):
    net.install_ipv4_preference()
    infos = socket.getaddrinfo("example.com", 443, socket.AF_UNSPEC)
    assert infos[0][0] == socket.AF_INET
    assert socket.AF_INET in fake_resolver


def test_falls_back_when_no_a_record(fake_resolver, monkeypatch):
    def only_v6(host, port, family=0, type=0, proto=0, flags=0):
        raise socket.gaierror("no v4")
    monkeypatch.setattr(net, "_ORIGINAL_GETADDRINFO",
                        lambda h, p, f=0, t=0, pr=0, fl=0:
                        [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))])
    net.install_ipv4_preference()
    infos = socket.getaddrinfo("v6only.example", 443, socket.AF_UNSPEC)
    assert infos[0][0] == socket.AF_INET6
