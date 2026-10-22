"""候选种子打分：新鲜度 + 下载/做种比 + 热度。

纯函数，不依赖网络与数据库，便于单测。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import List, Optional

# 权重可按实际效果调整
W_RECENCY = 0.50
W_RATIO = 0.35
W_HEAT = 0.15

# 新鲜度半衰期：6 小时后权重降到一半
RECENCY_HALF_LIFE_HOURS = 6.0


@dataclass
class Candidate:
    """站点返回的候选种子（已归一化）。"""

    tid: str
    name: str
    size: int
    discount: str = "NORMAL"
    discount_end: Optional[int] = None
    seeders: int = 0
    leechers: int = 0
    created: Optional[int] = None
    category: Optional[int] = None
    page_url: str = ""

    def as_dict(self) -> dict:
        return {
            "tid": self.tid,
            "name": self.name,
            "size": self.size,
            "discount": self.discount,
            "discount_end": self.discount_end,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "created": self.created,
            "category": self.category,
        }


def recency_score(created: Optional[int], now: Optional[float] = None) -> float:
    """越新越高，返回 (0, 1]。"""
    if not created:
        return 0.0
    now = time.time() if now is None else now
    age_hours = max(0.0, (now - created) / 3600.0)
    return math.pow(0.5, age_hours / RECENCY_HALF_LIFE_HOURS)


def ratio_score(seeders: int, leechers: int) -> float:
    """下载人数相对做种人数越多越好，饱和映射到 (0, 1)。"""
    seeders = max(0, int(seeders))
    leechers = max(0, int(leechers))
    if seeders == 0 and leechers == 0:
        return 0.0
    ratio = leechers / float(seeders + 1)
    return ratio / (ratio + 1.0)


def heat_score(leechers: int) -> float:
    """绝对下载人数，防止"比例好看但没人下"。"""
    leechers = max(0, int(leechers))
    return min(1.0, math.log10(1 + leechers) / 3.0)


def score(cand: Candidate, now: Optional[float] = None) -> float:
    """综合得分；死种（无人做种也无人下载）直接 0。"""
    if cand.seeders <= 0 and cand.leechers <= 0:
        return 0.0
    return (
        W_RECENCY * recency_score(cand.created, now)
        + W_RATIO * ratio_score(cand.seeders, cand.leechers)
        + W_HEAT * heat_score(cand.leechers)
    )


def rank(candidates: List[Candidate], now: Optional[float] = None) -> List[Candidate]:
    """按得分从高到低排序。"""
    now = time.time() if now is None else now
    return sorted(candidates, key=lambda c: score(c, now), reverse=True)


@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: float = field(default=0.0)

    @property
    def tid(self) -> str:
        return self.candidate.tid
