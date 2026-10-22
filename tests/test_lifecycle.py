import time

from app.config import ServiceConfig
from app.lifecycle import TorrentView, decide_delete, filter_candidates, plan_adds
from app.scoring import Candidate

GB = 1024 ** 3


def service(**kwargs) -> ServiceConfig:
    base = dict(
        name="svc",
        site="mteam",
        disk_gb=50,
        discounts=["FREE"],
        size_mb=[1024, 102400],
        delete_threshold_mb=1024,
        exempt_minutes=60,
        min_keep_minutes=120,
        max_torrents=10,
        add_per_tick=2,
    )
    base.update(kwargs)
    return ServiceConfig(**base)


def cand(tid="1", size=5 * GB, discount="FREE", age_h=1, end_in=None, seeders=5, leechers=50):
    now = time.time()
    return Candidate(
        tid=tid,
        name="n%s" % tid,
        size=size,
        discount=discount,
        discount_end=int(now + end_in) if end_in is not None else None,
        created=int(now - age_h * 3600),
        seeders=seeders,
        leechers=leechers,
    )


# ---------- 筛选 ----------
def test_filter_rejects_wrong_discount_and_size():
    cfg = service()
    ok, rejected = filter_candidates(
        [cand("1"), cand("2", discount="NORMAL"), cand("3", size=10 * 1024 ** 2)],
        cfg, time.time(),
    )
    assert [c.tid for c in ok] == ["1"]
    assert len(rejected) == 2


def test_filter_respects_category_whitelist():
    cfg = service(categories=[402])
    items = [cand("1"), cand("2")]
    items[0].category = 402
    items[1].category = 401
    ok, _ = filter_candidates(items, cfg, time.time())
    assert [c.tid for c in ok] == ["1"]


def test_filter_drops_expiring_discount_window():
    cfg = service()
    # 5GB 在 20MB/s 下约需 256 秒，加 600 秒余量；只给 60 秒应被拒
    ok, rejected = filter_candidates([cand("1", end_in=60)], cfg, time.time())
    assert ok == []
    assert rejected[0][1] == "促销窗口不足"


def test_filter_allows_rule_based_discount_without_end():
    cfg = service()
    ok, _ = filter_candidates([cand("1", end_in=None)], cfg, time.time())
    assert len(ok) == 1


def test_filter_excludes_known_tids():
    cfg = service()
    ok, _ = filter_candidates([cand("9")], cfg, time.time(), exclude_tids={"9"})
    assert ok == []


# ---------- 选种 ----------
def test_plan_respects_disk_quota():
    cfg = service(disk_gb=10, add_per_tick=5)
    plan = plan_adds([cand("1", size=6 * GB), cand("2", size=6 * GB)], cfg,
                     time.time(), used_bytes=0, current_count=0, dl_left=10)
    assert len(plan.picks) == 1
    assert plan.picks[0].tid == "1"
    assert plan.used_bytes_after == 6 * GB


def test_plan_skips_too_large_and_keeps_smaller():
    cfg = service(disk_gb=10, add_per_tick=5)
    big = cand("big", size=9 * GB)
    small = cand("small", size=3 * GB)
    plan = plan_adds([big, small], cfg, time.time(), used_bytes=6 * GB, current_count=0, dl_left=5)
    assert [p.tid for p in plan.picks] == ["small"]


def test_plan_stops_at_add_per_tick_and_quota():
    cfg = service(add_per_tick=2)
    plan = plan_adds([cand(str(i)) for i in range(5)], cfg, time.time(),
                     used_bytes=0, current_count=0, dl_left=10)
    assert len(plan.picks) == 2
    assert plan.stop_reason == "达到单轮上限"

    plan2 = plan_adds([cand("1")], cfg, time.time(), used_bytes=0,
                      current_count=0, dl_left=0)
    assert plan2.picks == [] and plan2.stop_reason == "配额耗尽"


def test_plan_respects_max_torrents():
    cfg = service(max_torrents=2)
    plan = plan_adds([cand("1"), cand("2")], cfg, time.time(),
                     used_bytes=0, current_count=2, dl_left=10)
    assert plan.picks == []
    assert plan.stop_reason == "达到并发上限"


# ---------- 删除 ----------
def torrent(progress=1.0, age_min=200, dlspeed=0, uploaded=0, hash_="h"):
    return TorrentView(
        hash=hash_,
        name="t",
        size=5 * GB,
        progress=progress,
        dlspeed=dlspeed,
        uploaded=uploaded,
        added_on=int(time.time() - age_min * 60),
        category="svc",
    )


def test_delete_skips_during_exemption():
    cfg = service()
    ok, reason = decide_delete(torrent(age_min=10), cfg, time.time(), 0)
    assert not ok and reason == "豁免期内"


def test_delete_skips_min_keep():
    cfg = service()
    ok, reason = decide_delete(torrent(age_min=90), cfg, time.time(), 0)
    assert not ok and reason == "最短保留期内"


def test_delete_skips_actively_downloading():
    cfg = service()
    ok, reason = decide_delete(torrent(progress=0.4, age_min=180, dlspeed=1024), cfg,
                               time.time(), 0)
    assert not ok and reason == "下载中"


def test_delete_stalled_download_after_min_keep():
    cfg = service()
    ok, reason = decide_delete(torrent(progress=0.4, age_min=180, dlspeed=0), cfg,
                               time.time(), 0, previous_progress=0.4)
    assert ok and "停滞" in reason


def test_delete_low_upload_after_min_keep():
    cfg = service()
    ok, reason = decide_delete(torrent(age_min=200), cfg, time.time(), 100 * 1024 ** 2)
    assert ok and "上传量不足" in reason


def test_keep_when_upload_meets_threshold():
    cfg = service()
    ok, reason = decide_delete(torrent(age_min=200), cfg, time.time(), 2 * 1024 ** 3)
    assert not ok and reason == "达标"


def test_keep_when_samples_insufficient():
    cfg = service()
    ok, reason = decide_delete(torrent(age_min=200), cfg, time.time(), None)
    assert not ok and reason == "上传量样本不足"
