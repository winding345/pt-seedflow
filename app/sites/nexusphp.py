"""NexusPHP 站点适配器（RSS 方式）。

为什么用 RSS：
- NexusPHP 的 RSS 条目里 `<enclosure url>` 就是带 passkey 的下载链接，
  不需要登录 cookie，也不需要调用任何接口，更没有配额限制
- 只需要一个能公网访问的 RSS 地址，配置里写好即可

局限：
- 拿到的是站点最新的 N 条（通常 50–100），不是按条件搜索
- 体积、促销标签依赖 RSS 标题里的文字，各站格式不同，用 discount_tags 映射
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from ..config import SiteConfig
from ..scoring import Candidate
from . import register
from .base import SiteAdapter, SiteError

_SIZE_UNITS = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
_TAG_RE = re.compile(r"[\[【(（]([^\]】)）]{1,12})[\]】)）]")


def parse_size_text(text: str) -> int:
    """把 "1.5 GB" / "800 MiB" 这类文本转成字节数。"""
    if not text:
        return 0
    match = re.search(r"([\d.]+)\s*([KMGT]i?B)", text, re.I)
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2).upper().replace("I", "")
    return int(value * _SIZE_UNITS.get(unit, 1))


def detect_discount(text: str, tags: Dict[str, str]) -> str:
    """按配置的关键词映射识别促销；没命中就是 NORMAL。"""
    if not text:
        return "NORMAL"
    for keyword, value in tags.items():
        if keyword and keyword in text:
            return value
    return "NORMAL"


def parse_rss(xml_text: str, tags: Optional[Dict[str, str]] = None,
              fallback_size_from_title: bool = True) -> List[Candidate]:
    """解析 NexusPHP RSS 为候选列表。"""
    tags = tags or {}
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise SiteError("RSS 解析失败: %s" % exc)

    items = root.findall(".//item")
    candidates: List[Candidate] = []
    for item in items:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = item.findtext("description") or ""

        enclosure = item.find("enclosure")
        download_url = (enclosure.get("url") if enclosure is not None else "") or ""
        size = int(enclosure.get("length") or 0) if enclosure is not None else 0
        if not size and fallback_size_from_title:
            size = parse_size_text(description) or parse_size_text(title)

        # 促销标识通常在标题的方括号里，也在描述里
        discount = detect_discount(title, tags) 
        if discount == "NORMAL":
            discount = detect_discount(description, tags)

        pub = item.findtext("pubDate")
        tid = (item.findtext("guid") or link or download_url or title).strip()

        candidates.append(Candidate(
            tid=tid,
            name=title,
            size=size,
            discount=discount,
            created=None if not pub else _parse_pubdate(pub),
            page_url=link,
            url=download_url,
        ))
    return candidates


def _parse_pubdate(text: str) -> Optional[int]:
    from email.utils import parsedate_to_datetime

    try:
        return int(parsedate_to_datetime(text).timestamp())
    except (TypeError, ValueError):
        return None


@register("nexusphp")
class NexusPHPSite(SiteAdapter):
    """NexusPHP 通用适配器：拉 RSS，直接用条目里的下载链接。"""

    def __init__(self, cfg: SiteConfig):
        if not cfg.rss_url:
            raise SiteError("站点 %s 缺少 rss_url" % cfg.name)
        self.cfg = cfg
        self._links: Dict[str, str] = {}

    def _fetch(self) -> str:
        request = Request(self.cfg.rss_url, headers={"User-Agent": self.cfg.user_agent})
        try:
            with urlopen(request, timeout=self.cfg.timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except HTTPError as exc:
            raise SiteError("RSS 返回 HTTP %s" % exc.code)
        except URLError as exc:
            raise SiteError("RSS 连接失败: %s" % exc.reason)

    def search(self, page: int = 1, page_size: int = 100) -> List[Candidate]:
        # RSS 只有一页；page/page_size 仅用于兼容接口
        candidates = parse_rss(self._fetch(), self.cfg.discount_tags)
        for candidate in candidates:
            if candidate.url:
                self._links[candidate.tid] = candidate.url
        return candidates[:page_size] if page_size else candidates

    def genlink(self, tid: str) -> Optional[str]:
        if tid in self._links:
            return self._links[tid]
        # 重启后缓存丢失：重新拉一次 RSS 再找
        for candidate in parse_rss(self._fetch(), self.cfg.discount_tags):
            if candidate.url:
                self._links[candidate.tid] = candidate.url
        return self._links.get(tid)
