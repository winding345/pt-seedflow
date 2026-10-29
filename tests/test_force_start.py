"""加种后强制继续：参数与调用测试（离线）。"""
import pytest

from app.qb import QBittorrent


class _Cfg:
    base_url = "http://127.0.0.1:8085"
    username = ""
    password = ""
    timeout = 5
    category = "auto-x"
    save_path = ""


@pytest.fixture()
def qb(monkeypatch):
    client = QBittorrent(_Cfg())
    calls = []

    def fake_post_form(path, fields):
        calls.append((path, fields))
        return "Ok."

    monkeypatch.setattr(client, "_post_form", fake_post_form)
    return client, calls


def test_add_url_not_stopped(qb, monkeypatch):
    client, calls = qb
    monkeypatch.setattr(client, "_post_multipart", lambda p, f: "Ok.")
    client._last_multipart = None
    posted = {}

    def fake_mp(path, fields):
        posted.update(fields)
        return "Ok."

    monkeypatch.setattr(client, "_post_multipart", fake_mp)
    assert client.add_url("magnet:?xt=urn:btih:x")
    assert posted["paused"] == "false"
    assert posted["stopped"] == "false"


def test_set_force_start_fields(qb):
    client, calls = qb
    assert client.set_force_start(["a", "b"]) is True
    path, fields = calls[-1]
    assert path == "/api/v2/torrents/setForceStart"
    assert fields["hashes"] == "a|b"
    assert fields["value"] == "true"


def test_set_force_start_empty_noop(qb):
    client, calls = qb
    assert client.set_force_start([]) is True
    assert calls == []
