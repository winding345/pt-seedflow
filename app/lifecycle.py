"""加种与删除的决策逻辑。

全部为纯函数：输入是已采集好的数据，输出是"该做什么"，不直接触网。
便于在本地用固定数据做单测。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .config import ServiceConfig
from .scoring import Candidate, rank, score

# 估算下载速度，用于判断促销窗口是否够用（20 MB/s）
ASSUMED_DOWNLOAD_BPS = 20 * 1024 * 1024
# 促销到期预留的安全余量
DISCOUNT_SAFETY_SECONDS = 600


@dataclass
class Pick:
    candidate: Candidate
    score: float

    @property
    def tid(self) -> str:
        return self.candidate.tid


@dataclass
class AddPlan:
    picks: List[Pick]
    used_bytes_after: int
    stop_reason: str = ""


@dataclass
class TorrentView:
    """来自 qBittorrent 的任务快照。"""

    hash: str
    name: str
    size: int
    progress: float
    state: str = ""
    dlspeed: int = 0
    upspeed: int = 0
    uploaded: int = 0
    downloaded: int = 0
    num_seeds: int = 0
    num_leechs: int = 0
    added_on: Optional[int] = None
    last_activity: Optional[int] = None
    category: str = ""

    @property
    def completed(self) -> bool:
        return self.progress >= 0.999

    @property
    def age_minutes(self) -> float:
        if not self.added_on:
            return 0.0
        return max(0.0, (time.time() - self.added_on) / 60.0)


def _discount_window_ok(cand: Candidate, now: float, size: int) -> bool:
    """促销是否还有足够时间下载完。"""
    if not cand.discount_end:
        # 规则型促销没有结束时间，视为长期有效
        return True
    remaining = cand.discount_end - now
    if remaining <= 0:
        return False
    needed = size / float(ASSUMED_DOWNLOAD_BPS) + DISCOUNT_SAFETY_SECONDS
    return remaining > needed


def filter_candidates(
    candidates: Iterable[Candidate],
    cfg: ServiceConfig,
    now: float,
    exclude_tids: Optional[Set[str]] = None,
) -> Tuple[List[Candidate], List[Tuple[str, str]]]:
    """按配置筛掉不合格的候选；返回(通过列表, 被拒原因)。"""
    exclude_tids = exclude_tids or set()
    low, high = cfg.size_bytes_range
    passed: List[Candidate] = []
    rejected: List[Tuple[str, str]] = []

    for cand in candidates:
        if cand.tid in exclude_tids:
            rejected.append((cand.tid, "已在队列或近期删除"))
            continue
        if not cfg.allows_discount(cand.discount):
            rejected.append((cand.tid, "促销类型不符: %s" % (cand.discount or "NORMAL")))
            continue
        if not (low <= cand.size <= high):
            rejected.append((cand.tid, "体积超出范围"))
            continue
        if cfg.categories and cand.category not in cfg.categories:
            rejected.append((cand.tid, "分类不在范围"))
            continue
        if not _discount_window_ok(cand, now, cand.size):
            rejected.append((cand.tid, "促销窗口不足"))
            continue
        passed.append(cand)

    return passed, rejected


def plan_adds(
    candidates: Sequence[Candidate],
    cfg: ServiceConfig,
    now: float,
    used_bytes: int,
    current_count: int,
    dl_left: int,
    exclude_tids: Optional[Set[str]] = None,
    max_adds: Optional[int] = None,
) -> AddPlan:
    """按剩余空间与配额挑选要加的种子，返回不可变的计划。"""
    if dl_left <= 0:
        return AddPlan([], used_bytes, "配额耗尽")

    pool, _ = filter_candidates(candidates, cfg, now, exclude_tids)
    ordered = rank(pool, now)

    picks: List[Pick] = []
    used = used_bytes
    count = current_count
    left = dl_left
    stop_reason = "没有符合条件的新候选"
    no_space = 0
    broke = False

    for cand in ordered:
        limit = cfg.add_per_tick if max_adds is None else max_adds
        if len(picks) >= limit:
            stop_reason = "达到单轮上限"
            broke = True
            break
        if left <= 0:
            stop_reason = "配额耗尽"
            broke = True
            break
        if count >= cfg.max_torrents:
            stop_reason = "达到并发上限"
            broke = True
            break
        if used + cand.size > cfg.disk_bytes:
            # 体积不合适就跳过，继续看更小的
            no_space += 1
            continue
        picks.append(Pick(cand, score(cand, now)))
        used += cand.size
        count += 1
        left -= 1

    if not broke:
        if picks:
            stop_reason = "候选已遍历完，本批添加 %d 个" % len(picks)
        elif no_space:
            stop_reason = "剩余空间不足（%d 个候选装不下）" % no_space

    return AddPlan(picks, used, stop_reason)


def verify_deletable(
    torrents: Sequence[TorrentView],
    targets: Sequence[Tuple[str, str]],
    category: str,
) -> List[Tuple[str, str]]:
    """删除前的最后一道校验。

    只有同时满足以下条件的任务才允许删除：
    - hash 出现在当前 qB 列表里
    - 该任务的分类与服务分类完全一致
    - 任务名与记录的名字一致（防止列表漂移或 hash 碰撞）

    返回确认可删的 (hash, name) 列表。
    """
    index = {t.hash: t for t in torrents if (t.category or "") == category}
    verified: List[Tuple[str, str]] = []
    for torrent_hash, name in targets:
        current = index.get(torrent_hash)
        if current is None:
            continue
        if current.name != name:
            continue
        verified.append((torrent_hash, current.name))
    return verified


def decide_delete(
    torrent: TorrentView,
    cfg: ServiceConfig,
    now: float,
    upload_window_bytes: Optional[int],
    previous_progress: Optional[float] = None,
    discount_end: Optional[int] = None,
) -> Tuple[bool, str]:
    """判断某个任务是否该删；返回(是否删除, 原因)。"""
    age = (now - torrent.added_on) / 60.0 if torrent.added_on else 0.0

    if age < cfg.exempt_minutes:
        return False, "豁免期内"

    # 未下完但促销已结束：继续下载会消耗真实下载量，直接淘汰
    if (cfg.delete_unfinished_after_discount and not torrent.completed
            and discount_end and now > discount_end):
        return True, "促销已结束且未下完"

    if not torrent.completed:
        progress_moving = (
            previous_progress is not None and torrent.progress > previous_progress + 1e-6
        )
        if torrent.dlspeed > 0 or progress_moving:
            return False, "下载中"
        if age < cfg.min_keep_minutes:
            return False, "最短保留期内"
        return True, "停滞且超过最短保留期"

    if age < cfg.min_keep_minutes:
        return False, "最短保留期内"

    if upload_window_bytes is None:
        return False, "上传量样本不足"

    if upload_window_bytes < cfg.threshold_bytes:
        return True, "上传量不足(%d MB < %d MB)" % (
            upload_window_bytes // (1024 ** 2),
            cfg.delete_threshold_mb,
        )

    return False, "达标"
