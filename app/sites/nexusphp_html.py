"""NexusPHP HTML 列表页适配器（可配置选择器）。

为什么需要它：
- RSS 里没有促销信息，而刷流必须知道哪些种子免费
- 列表页的每行带着促销标记（`img.pro_free` / 「免费」标签），这是唯一可靠来源

设计参考 MoviePilot 的「站点定义」：把 row/title/size/download/discount 等
选择器写进配置，新增站点只改配置不改代码。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from lxml import etree
from cssselect import SelectorError

from ..config import SiteConfig
from ..scoring import Candidate
from . import register
from .base import SiteAdapter, SiteError
from .nexusphp import parse_size_text

# NexusPHP 常见模板的默认选择器，站点可在配置里覆盖
DEFAULT_SELECTORS: Dict[str, str] = {
    "row": "table.torrents tr, table#torrenttable tr, table.torrents_table tr",
    "title": "td:nth-child(2) a[href*='details.php']",
    "size": "td:nth-child(5)",
    "download": "td:nth-child(2) a[href*='download.php']@href",
    "date": "td:nth-child(4)",
    "seeders": "td:nth-child(6) b, td:nth-child(6) a",
    "leechers": "td:nth-child(7) b, td:nth-child(7) a",
}

# 促销标记：命中哪个选择器就是哪种促销
DEFAULT_DISCOUNT_CASES: Dict[str, str] = {
    # free2up = 免费 + 2X 上传，语义上是 2X免费，别和单纯免费混了
    "img.pro_free2up, img.pro_2xfree, img.pro_2x_free": "_2X_FREE",
    "img.pro_free": "FREE",
    "img.pro_2x, img.pro_2up": "_2X",
    "img.pro_50pct, img.pro_50": "PERCENT_50",
    "img.pro_30pct, img.pro_30": "PERCENT_30",
}

# 站点模板：按域名自动匹配，配置里的 selectors 可覆盖
SITE_PROFILES: Dict[str, Dict] = {
    "pttime.org": {
        "list_url": "torrents.php?spstate=2",
        "selectors": {
            "row": "table#torrenttable > tr, table#torrenttable > tbody > tr",
            "title": "a.torrentname_title",
            "size": "td:nth-child(6)",
            "download": "a[href*='download.php']@href",
            "date": "td:nth-child(5) span[title]@title",
            "seeders": "td:nth-child(7)",
            "leechers": "td:nth-child(8)",
            "discount_case": {
                "font.promotion.twoupfree": "_2X_FREE",
                "font.promotion.twouphalfdown": "_2X_PERCENT_50",
                "font.promotion.free": "FREE",
                "font.promotion.zeroupzerodown": "FREE",
                "font.promotion.twoup": "_2X",
                "font.promotion.halfdown": "PERCENT_50",
                "font.promotion.thirtypercent": "PERCENT_30",
            },
        },
    },
}

SITE_PROFILES["carpt.net"] = {
    "list_url": "torrents.php?spstate=2",
    "selectors": {
        "row": "table.torrents > tr, table.torrents > tbody > tr",
        "title": "td:nth-child(2) a[href*='details.php']",
        "size": "td:nth-child(5)",
        "download": "td:nth-child(2) a[href*='download.php']@href",
        "date": "td:nth-child(4) span[title]@title",
        "seeders": "td:nth-child(6)",
        "leechers": "td:nth-child(7)",
    },
}

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?")


def _resolve(node, spec):
    """按选择器取值。

    spec 支持三种写法：
      "td:nth-child(6)"            取文本
      "span[title]@title"          取属性（@ 后缀）
      {"selector": "...", "attribute": "title"}
    """
    attribute = None
    if isinstance(spec, dict):
        attribute = spec.get("attribute")
        selector = spec.get("selector") or ""
    else:
        selector = str(spec or "")
        if "@" in selector:
            selector, attribute = selector.rsplit("@", 1)
            attribute = attribute.strip()
    if not selector:
        return ""
    nodes = _select(node, selector)
    if not nodes:
        return ""
    target = nodes[0]
    if attribute:
        return (target.get(attribute) or "").strip()
    return " ".join(t.strip() for t in target.itertext() if t.strip()).strip()


def _select(node, selector: str):
    """CSS 选择器优先，也兼容直接写 XPath。"""
    if not selector:
        return []
    selector = selector.strip()
    if selector.startswith("/"):
        return node.xpath(selector)
    try:
        return node.cssselect(selector)
    except SelectorError:
        return []


def parse_list_html(html_text: str, selectors: Dict[str, str],
                    discount_cases: Dict[str, str], base_url: str,
                    discount_text_map: Optional[Dict[str, str]] = None,
                    passkey: str = "") -> List[Candidate]:
    """把列表页 HTML 解析成候选。选择器缺省时用 NexusPHP 默认值。"""
    merged = dict(DEFAULT_SELECTORS)
    merged.update({k: v for k, v in (selectors or {}).items() if v})
    cases = dict(DEFAULT_DISCOUNT_CASES)
    cases.update(discount_cases or {})
    discount_text_map = discount_text_map or {}

    try:
        root = etree.HTML(html_text)
    except Exception as exc:  # noqa: BLE001
        raise SiteError("HTML 解析失败: %s" % exc)
    if root is None:
        raise SiteError("HTML 为空")

    rows = _select(root, merged["row"])
    candidates: List[Candidate] = []
    for row in rows:
        title_nodes = _select(row, merged["title"])
        if not title_nodes:
            continue
        link_node = title_nodes[0]
        title = " ".join(t.strip() for t in link_node.itertext() if t.strip())
        title = title or (link_node.get("title") or "").strip()
        if not title:
            continue

        page_url = urljoin(base_url, link_node.get("href") or "") if link_node.tag == "a" else ""

        download_url = _resolve(row, merged["download"])
        if download_url:
            download_url = urljoin(base_url, download_url)
            # 有的站点下载链接只认 session，qB 没有 cookie，必须补 passkey
            if passkey and "passkey=" not in download_url:
                sep = "&" if "?" in download_url else "?"
                download_url = "%s%spasskey=%s" % (download_url, sep, passkey)

        size = parse_size_text(_resolve(row, merged["size"]))

        created = None
        date_text = _resolve(row, merged["date"])
        match = DATE_RE.search(date_text)
        if match:
            from datetime import datetime
            parts = [int(x) if x else 0 for x in match.groups()]
            created = int(datetime(*parts).timestamp())

        seeders = 0
        leechers = 0
        if merged.get("seeders"):
            try:
                seeders = int(re.sub(r"[^0-9]", "", _resolve(row, merged["seeders"])) or 0)
            except ValueError:
                seeders = 0
        if merged.get("leechers"):
            try:
                leechers = int(re.sub(r"[^0-9]", "", _resolve(row, merged["leechers"])) or 0)
            except ValueError:
                leechers = 0

        discount = "NORMAL"
        for selector, value in cases.items():
            if _select(row, selector):
                discount = value
                break
        if discount == "NORMAL" and discount_text_map:
            row_text = " ".join(row.itertext())
            for keyword, value in discount_text_map.items():
                if keyword and keyword in row_text:
                    discount = value
                    break

        tid_match = re.search(r"[?&]id=(\d+)", page_url) or re.search(r"[?&]id=(\d+)", download_url)
        tid = tid_match.group(1) if tid_match else page_url
        candidates.append(Candidate(
            tid=tid,
            name=title,
            size=size,
            discount=discount,
            seeders=seeders,
            leechers=leechers,
            created=created,
            page_url=page_url,
            url=download_url,
        ))
    return candidates


@register("nexusphp_html")
class NexusPHPHtmlSite(SiteAdapter):
    """用 cookie 抓列表页，解析出带促销信息的候选。"""

    def __init__(self, cfg: SiteConfig):
        profile = {}
        host = (cfg.base_url or "").split("//")[-1].split("/")[0].lower()
        for domain, conf in SITE_PROFILES.items():
            if host.endswith(domain):
                profile = conf
                break

        selectors = dict(profile.get("selectors") or {})
        for key, value in (cfg.selectors or {}).items():
            if key == "discount_case" and isinstance(value, dict):
                selectors.setdefault("discount_case", {})
                selectors["discount_case"] = {**selectors["discount_case"], **value}
            elif value:
                selectors[key] = value
        cfg.selectors = selectors
        if not cfg.list_url:
            cfg.list_url = profile.get("list_url") or ""
        if not cfg.list_url:
            raise SiteError("站点 %s 缺少 list_url（该站点没有内置模板）" % cfg.name)
        self.cfg = cfg
        self._links: Dict[str, str] = {}

    def _fetch(self) -> str:
        url = urljoin(self.cfg.base_url + "/", self.cfg.list_url)
        headers = {
            "User-Agent": self.cfg.user_agent,
            "Cookie": self.cfg.cookie,
            "Accept": "text/html,application/xhtml+xml",
        }
        if not self.cfg.cookie:
            raise SiteError("站点 %s 未配置 cookie，请在面板「编辑配置 → 站点配置」里填写" % self.cfg.name)
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.cfg.timeout) as resp:
                raw = resp.read()
        except HTTPError as exc:
            raise SiteError("列表页返回 HTTP %s（cookie 可能失效）" % exc.code)
        except URLError as exc:
            raise SiteError("列表页连接失败: %s" % exc.reason)
        return raw.decode("utf-8", "replace")

    def search(self, page: int = 1, page_size: int = 100) -> List[Candidate]:
        cases = (self.cfg.selectors or {}).get("discount_case") or {}
        candidates = parse_list_html(
            self._fetch(),
            self.cfg.selectors or {},
            cases,
            self.cfg.base_url,
            self.cfg.discount_tags,
            passkey=self.cfg.passkey,
        )
        for candidate in candidates:
            if candidate.url:
                self._links[candidate.tid] = candidate.url
        return candidates[:page_size] if page_size else candidates

    def genlink(self, tid: str) -> Optional[str]:
        if tid in self._links:
            return self._links[tid]
        self.search()
        return self._links.get(tid)
