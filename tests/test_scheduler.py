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


def _runner_for_refresh(**overrides):
    """构造一个只用于测试 _need_refresh 的 runner。"""
    from app.config import ServiceConfig

    cfg = ServiceConfig(name="demo", site="mteam", **overrides)
    runner = object.__new__(ServiceRunner)
    runner.cfg = cfg
    runner.ticks_since_search = 0
    runner.refresh_every_ticks = 12
    runner.last_search_at = 0.0
    runner._pool_exhausted = False
    return runner


def test_refresh_waits_for_period_by_default():
    import time
    runner = _runner_for_refresh(refill_when_empty=False)
    runner.last_search_at = time.time() - 3600
    assert runner._need_refresh() is False
    runner.ticks_since_search = 12
    assert runner._need_refresh() is True


def test_refill_triggers_when_pool_empty_after_cooldown(monkeypatch):
    import time
    runner = _runner_for_refresh(refill_min_seconds=600)
    runner._pool_exhausted = True
    runner.last_search_at = time.time() - 700     # 超过补池冷却
    assert runner._need_refresh() is True


def test_refill_respects_cooldown(monkeypatch):
    import time
    runner = _runner_for_refresh(refill_min_seconds=600)
    runner._pool_exhausted = True
    runner.last_search_at = time.time() - 60      # 还没到补池冷却
    assert runner._need_refresh() is False


def test_no_refill_when_pool_not_empty():
    import time
    runner = _runner_for_refresh(refill_min_seconds=600)
    runner._pool_exhausted = False
    runner.last_search_at = time.time() - 700
    assert runner._need_refresh() is False
