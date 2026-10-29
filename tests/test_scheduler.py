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


class _StubStore:
    """_step_search 需要的最小存储接口。"""

    def __init__(self, added=(), removed=()):
        self.added = list(added)
        self.removed = list(removed)
        self.saved = []
        self.kv = {}
        self.quota = {}

    def added_tids_since(self, service, since):
        return self.added

    def removed_tids_since(self, service, since):
        return self.removed

    def quota_used(self, service, kind, day):
        return self.quota.get(kind, 0)

    def quota_add(self, service, kind, day, amount=1):
        self.quota[kind] = self.quota.get(kind, 0) + amount
        return self.quota[kind]

    def kv_set(self, service, key, value):
        self.kv[key] = value

    def kv_get(self, service, key, default=0):
        return int(self.kv.get(key, default))

    def save_candidates(self, service, payload, saved_at=None):
        self.saved.append(payload)


class _PagedSite:
    """每页返回固定候选，用于验证翻页与去重。"""

    def __init__(self, page_map):
        self.page_map = page_map
        self.pages_requested = []

    def search(self, page=1, page_size=100):
        self.pages_requested.append(page)
        return self.page_map.get(page, [])


def _make_search_runner(page_map, added=(), removed=(), **cfg_kw):
    from app.config import ServiceConfig
    from app.scoring import Candidate

    runner = object.__new__(ServiceRunner)
    runner.cfg = ServiceConfig(name="demo", site="mteam", **cfg_kw)
    runner.store = _StubStore(added=added, removed=removed)
    runner.site = _PagedSite({
        page: [Candidate(tid=t, name="t%s" % t, size=6 * 1024 ** 3, discount="FREE") for t in tids]
        for page, tids in page_map.items()
    })
    runner.ticks_since_search = 5
    runner.last_search_at = 0.0
    runner.last_error = ""
    runner._pool_exhausted = False
    runner._log = lambda *a, **k: None
    return runner


def test_search_paginates_when_first_page_all_excluded():
    # 第 1 页 3 条都加过 → 必须翻第 2 页
    runner = _make_search_runner({1: ["1", "2", "3"], 2: ["4", "5", "6"]},
                                 added=["1", "2", "3"], search_min_pool=2)
    assert runner._step_search(force=True) is True
    assert runner.site.pages_requested == [1, 2]
    assert [c.tid for c in runner.candidates] == ["4", "5", "6"]
    assert runner.last_search_at > 0            # 状态必须更新
    assert runner.ticks_since_search == 0
    assert runner.store.kv.get("last_search_ts")


def test_search_stops_when_pool_big_enough():
    runner = _make_search_runner({1: ["1", "2", "3", "4"], 2: ["5"]},
                                 search_min_pool=3)
    runner._step_search(force=True)
    assert runner.site.pages_requested == [1]     # 已达目标，不翻页


def test_search_respects_max_pages():
    runner = _make_search_runner({1: [], 2: [], 3: []}, added=[], search_max_pages=2)
    runner._step_search(force=True)
    assert runner.site.pages_requested == [1]     # 第 1 页空 → 直接停


def test_search_quota_cancels():
    runner = _make_search_runner({1: ["1"]})
    runner.store.quota["search"] = 1000
    assert runner._step_search(force=True) is False
    assert runner.site.pages_requested == []


def test_search_pages_while_usable_below_min_pool():
    """第 1 页原始条数很多、但可用为 0 时必须继续翻页。"""
    from app.scoring import Candidate

    runner = _make_search_runner({1: [], 2: []}, search_min_pool=1, search_max_pages=3)
    runner.cfg.size_mb = (1.0, 1000000.0)            # 体积全放
    runner.cfg.discounts = ["FREE"]
    runner.site.page_map = {
        1: [Candidate(tid="a%d" % i, name="n", size=6 * 1024 ** 3, discount="_4K_FREE") for i in range(50)],
        2: [Candidate(tid="b1", name="n", size=6 * 1024 ** 3, discount="FREE")],
    }
    runner._step_search(force=True)
    assert runner.site.pages_requested == [1, 2]     # 必须翻到第 2 页
    assert len(runner.candidates) == 51              # 两页都收进池子
