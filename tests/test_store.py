import time

from app.store import Store


def make_store(tmp_path):
    return Store(tmp_path / "state.db")


def test_quota_accumulates_per_day(tmp_path):
    store = make_store(tmp_path)
    assert store.quota_used("s1", "dl", "2026-10-22") == 0
    store.quota_add("s1", "dl", "2026-10-22")
    store.quota_add("s1", "dl", "2026-10-22")
    assert store.quota_used("s1", "dl", "2026-10-22") == 2
    # 换一天从零开始
    assert store.quota_used("s1", "dl", "2026-10-23") == 0
    # 不同服务互不影响
    assert store.quota_used("s2", "dl", "2026-10-22") == 0


def test_quota_isolated_by_kind(tmp_path):
    store = make_store(tmp_path)
    store.quota_add("s1", "search", "2026-10-22", 5)
    assert store.quota_used("s1", "search", "2026-10-22") == 5
    assert store.quota_used("s1", "dl", "2026-10-22") == 0


def test_upload_window_uses_baseline_before_window(tmp_path):
    store = make_store(tmp_path)
    now = int(time.time())
    store.add_sample("s1", "abc", 1000, now - 3600)
    store.add_sample("s1", "abc", 2000, now - 1800)
    store.add_sample("s1", "abc", 5000, now)
    assert store.upload_window("s1", "abc", now - 3600) == 4000
    # 窗口起点落在两次采样之间时，用起点之前最近的一条做基准
    assert store.upload_window("s1", "abc", now - 900) == 3000


def test_upload_window_needs_enough_samples(tmp_path):
    store = make_store(tmp_path)
    now = int(time.time())
    store.add_sample("s1", "abc", 1000, now)
    assert store.upload_window("s1", "abc", now - 3600) is None
    assert store.sample_count("s1", "abc", now - 3600) == 1


def test_upload_window_zero_delta(tmp_path):
    store = make_store(tmp_path)
    now = int(time.time())
    store.add_sample("s1", "abc", 1000, now - 600)
    store.add_sample("s1", "abc", 1000, now)
    assert store.upload_window("s1", "abc", now - 3600) == 0


def test_prune_samples(tmp_path):
    store = make_store(tmp_path)
    now = int(time.time())
    store.add_sample("s1", "abc", 1, now - 7200)
    store.add_sample("s1", "abc", 2, now)
    store.prune_samples(now - 600)
    assert store.sample_count("s1", "abc", 0) == 1


def test_removal_history_window(tmp_path):
    store = make_store(tmp_path)
    now = int(time.time())
    store.mark_removed("s1", "111", "old", now - 90000)
    store.mark_removed("s1", "222", "new", now)
    recent = store.removed_tids_since("s1", now - 86400)
    assert recent == ["222"]


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "state.db"
    first = Store(path)
    first.quota_add("s1", "dl", "2026-10-22", 3)
    first.close()

    second = Store(path)
    assert second.quota_used("s1", "dl", "2026-10-22") == 3
    second.close()
