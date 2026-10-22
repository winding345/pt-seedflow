"""m-team 站点适配器。"""
from __future__ import annotations

from typing import List, Optional

from ...config import SiteConfig
from ...scoring import Candidate
from .. import register
from ..base import SiteAdapter
from .client import MTeamClient
from .parser import parse_candidates

__all__ = ["MTeamSite", "parse_torrents"]


@register("mteam")
class MTeamSite(SiteAdapter):
    """m-team：API 搜索 + 令牌换下载链接。"""

    def __init__(self, cfg: SiteConfig):
        self.cfg = cfg
        self.client = MTeamClient(cfg)

    def search(self, page: int = 1, page_size: int = 100) -> List[Candidate]:
        return self.client.search(page=page, page_size=page_size)

    def genlink(self, tid: str) -> Optional[str]:
        return self.client.genlink(tid)


def parse_torrents(payload: dict) -> List[Candidate]:
    """离线解析（单测直接喂真实响应样本）。"""
    return parse_candidates(payload)
