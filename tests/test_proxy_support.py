"""站点代理支持测试（离线，替换底层 opener）。"""
import pytest

from app.config import SiteConfig
from app.sites.base import HTTPClient


def test_proxy_roundtrip_in_config():
    cfg = SiteConfig(name="mteam", base_url="https://api.m-team.cc", user_agent="ua",
                     proxy="http://127.0.0.1:7890")
    assert SiteConfig.from_dict(cfg.to_dict()).proxy == "http://127.0.0.1:7890"


def test_http_client_uses_proxy(monkeypatch):
    used = {}

    class FakeResp:
        def read(self):
            return b'{"ok":1}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class FakeOpener:
        def open(self, request, timeout=None):
            used["proxy"] = True
            return FakeResp()

    def fake_build_opener(handler):
        used["handler"] = handler
        return FakeOpener()

    import app.sites.base as base
    monkeypatch.setattr(base, "build_opener", fake_build_opener)

    client = HTTPClient("https://api.m-team.cc", proxy="http://127.0.0.1:7890")
    assert client.request("/x", method="GET") == {"ok": 1}
    assert used.get("proxy") is True


def test_http_client_without_proxy_uses_direct(monkeypatch):
    called = {}

    class FakeResp:
        def read(self):
            return b'{"ok":2}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    import app.sites.base as base
    monkeypatch.setattr(base, "urlopen", lambda req, timeout=None: FakeResp())
    monkeypatch.setattr(base, "build_opener", lambda h: pytest.fail("不应走代理"))

    client = HTTPClient("https://api.m-team.cc")
    assert client.request("/x", method="GET") == {"ok": 2}
