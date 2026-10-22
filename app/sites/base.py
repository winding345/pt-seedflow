"""站点适配器接口。"""
from __future__ import annotations

from typing import List, Optional, Protocol

from ..scoring import Candidate


class SiteAdapter(Protocol):
    """站点需要提供的能力：搜索候选、按种子 id 换下载链接。"""

    name: str

    def search(self, page: int = 1, page_size: int = 100) -> List[Candidate]:
        ...

    def genlink(self, tid: str) -> Optional[str]:
        ...
