"""m-team 站点适配。

要点（实测得来，改代码前先读）：
- 必须使用 api.<domain>，不是 kp.<domain>。
- 必须同时带 User-Agent 与 Accept: application/json, text/plain, */*
  否则会被 Cloudflare 直接 302 到 google 或 403。
- /torrent/search 用 JSON body；/torrent/detail、/torrent/genDlToken 用查询串。
- search 不返回下载链接，必须用 genDlToken 换。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import SiteConfig
from ..scoring import Candidate
from . import register

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@register("mteam")
class MTeamSite:
    name = "mteam"

    def __init__(self, cfg: SiteConfig):
        self.cfg = cfg
        self._key = cfg.resolve_key()

    # ---------- 底层请求 ----------
    def _headers(self, with_body: bool = True) -> Dict[str, str]:
        headers = {
            "User-Agent": self.cfg.user_agent,
            "Accept": "application/json, text/plain, */*",
            "x-api-key": self._key,
        }
        # 只带查询串的接口不要声明 JSON body，否则部分接口会判定参数错误
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _call(self, path: str, params: Optional[Dict[str, Any]] = None,
              body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self.cfg.base_url + path
        if params:
            url = url + "?" + urlencode(params)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=payload, headers=self._headers(body is not None), method="POST")
        try:
            with urlopen(request, timeout=self.cfg.timeout) as resp:
                raw = resp.read()
        except HTTPError as exc:
            raise RuntimeError("m-team 请求失败 %s: HTTP %s" % (path, exc.code))
        except URLError as exc:
            raise RuntimeError("m-team 连接失败 %s: %s" % (path, exc.reason))

        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError("m-team 返回非 JSON（可能被拦截）: %s" % path)

        code = data.get("code")
        if str(code) != "0":
            detail = "m-team 业务错误 %s" % path
            if params:
                detail += " 参数=%s" % urlencode(params)
            detail += ": %s" % data.get("message")
            raise RuntimeError(detail)
        return data

    # ---------- 对外能力 ----------
    def search(self, page: int = 1, page_size: int = 100) -> List[Candidate]:
        data = self._call(
            "/api/torrent/search",
            body={"pageNumber": page, "pageSize": page_size},
        )
        items = ((data.get("data") or {}).get("data")) or []
        return [self._to_candidate(item) for item in items]

    def genlink(self, tid: str) -> Optional[str]:
        data = self._call("/api/torrent/genDlToken", params={"id": tid})
        url = data.get("data")
        return url if isinstance(url, str) else None

    # ---------- 解析 ----------
    @staticmethod
    def _ts(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        try:
            return int(datetime.strptime(value, DATE_FORMAT).timestamp())
        except ValueError:
            return None

    def _to_candidate(self, item: Dict[str, Any]) -> Candidate:
        status = item.get("status") or {}
        discount = status.get("discount") or "NORMAL"
        discount_end = self._ts(status.get("discountEndTime"))

        # 规则型促销没有 discountEndTime，但会在 promotionRule 中标注
        rule = status.get("promotionRule") or {}
        if rule.get("discount"):
            discount = rule["discount"]
            discount_end = None

        try:
            category = int(item.get("category"))
        except (TypeError, ValueError):
            category = None

        return Candidate(
            tid=str(item.get("id")),
            name=str(item.get("name") or ""),
            size=int(item.get("size") or 0),
            discount=str(discount),
            discount_end=discount_end,
            seeders=int(status.get("seeders") or 0),
            leechers=int(status.get("leechers") or 0),
            created=self._ts(item.get("createdDate")),
            category=category,
            page_url="https://kp.m-team.cc/detail/%s" % item.get("id"),
        )


def parse_torrents(payload: Dict[str, Any]) -> List[Candidate]:
    """离线解析用（单测直接喂真实响应样本）。"""
    site = MTeamSite.__new__(MTeamSite)
    items = ((payload.get("data") or {}).get("data")) or []
    return [site._to_candidate(item) for item in items]
