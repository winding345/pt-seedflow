import time

from app.scoring import Candidate, heat_score, rank, ratio_score, recency_score, score


def make(tid="1", size=10 * 1024 ** 3, seeders=5, leechers=50, age_hours=1.0, discount="FREE"):
    return Candidate(
        tid=tid,
        name="t%s" % tid,
        size=size,
        discount=discount,
        created=int(time.time() - age_hours * 3600),
        seeders=seeders,
        leechers=leechers,
    )


def test_recency_decays_with_age():
    now = time.time()
    fresh = recency_score(int(now - 60), now)
    old = recency_score(int(now - 12 * 3600), now)
    assert 0 < old < fresh <= 1
    # 半衰期 6 小时：12 小时后应约为 1/4
    assert abs(old - 0.25) < 0.02


def test_ratio_prefers_more_leechers_fewer_seeders():
    assert ratio_score(1, 100) > ratio_score(100, 1)
    assert ratio_score(0, 0) == 0


def test_heat_is_bounded():
    assert heat_score(0) == 0
    assert 0 < heat_score(10) < 1
    assert heat_score(10 ** 9) <= 1


def test_dead_torrent_scores_zero():
    dead = Candidate(tid="x", name="x", size=1, seeders=0, leechers=0, created=int(time.time()))
    assert score(dead) == 0


def test_rank_prefers_fresh_hot_torrent():
    now = time.time()
    fresh_hot = make("a", seeders=2, leechers=200, age_hours=0.2)
    stale = make("b", seeders=2, leechers=200, age_hours=40)
    congested = make("c", seeders=500, leechers=3, age_hours=0.2)
    ordered = rank([stale, congested, fresh_hot], now)
    assert ordered[0].tid == "a"
    assert ordered[-1].tid == "b"


def test_rank_keeps_original_list_untouched():
    items = [make("a"), make("b")]
    snapshot = list(items)
    rank(items)
    assert items == snapshot
