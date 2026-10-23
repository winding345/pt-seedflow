"""换链接重试逻辑的测试（不依赖网络与数据库）。"""
import time

import pytest

from app.scheduler import ServiceRunner
from app.sites.base import SiteError


class FlakySite:
    def __init__(self, fail_times=2, error="genDlToken 连接失败: [Errno 104] Connection reset by peer"):
        self.fail_times = fail_times
        self.error = error
        self.calls = 0

    def genlink(self, tid):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise SiteError(self.error)
        return "https://example.invalid/dl/%s" % tid


def make_runner(site):
    runner = object.__new__(ServiceRunner)
    runner.site = site
    return runner


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def test_retries_transient_connection_error():
    site = FlakySite(fail_times=2)
    assert make_runner(site)._genlink("42") == "https://example.invalid/dl/42"
    assert site.calls == 3


def test_gives_up_after_retries():
    site = FlakySite(fail_times=99)
    with pytest.raises(SiteError):
        make_runner(site)._genlink("42")
    assert site.calls == 3          # 首次 + 2 次重试


def test_business_error_is_not_retried():
    site = FlakySite(fail_times=99, error="m-team 业务错误 /api/torrent/genDlToken: 請求參數錯誤")
    with pytest.raises(SiteError):
        make_runner(site)._genlink("42")
    assert site.calls == 1


class FakeStore:
    def __init__(self):
        self.saved = {}
        self.kv = {}

    def load_candidates(self, service):
        return self.saved.get(service, (None, 0))

    def save_candidates(self, service, payload, saved_at=None):
        self.saved[service] = (payload, saved_at or 0)


def test_restore_candidates_from_cache():
    import json
    from app.scoring import Candidate

    store = FakeStore()
    store.save_candidates("svc", json.dumps([{
        "tid": "1", "name": "缓存的种子", "size": 1024, "discount": "FREE",
        "discount_end": None, "seeders": 1, "leechers": 2, "created": 100, "category": 402,
    }]), 123)

    runner = object.__new__(ServiceRunner)
    runner.store = store
    runner.cfg = type("Cfg", (), {"name": "svc"})()
    runner.candidates = []
    runner._cache_restored = False
    runner.last_search_at = 0

    runner._restore_candidates()
    assert len(runner.candidates) == 1
    assert runner.candidates[0].name == "缓存的种子"
    assert runner.last_search_at == 123


def test_restore_is_noop_when_cache_empty():
    runner = object.__new__(ServiceRunner)
    runner.store = FakeStore()
    runner.cfg = type("Cfg", (), {"name": "svc"})()
    runner.candidates = []
    runner._cache_restored = False
    runner.last_search_at = 0
    runner._restore_candidates()
    assert runner.candidates == []
