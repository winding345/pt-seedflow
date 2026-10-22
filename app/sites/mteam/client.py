"""m-team API 客户端。

实测要点（改代码前先读）：
- 必须用 ``api.<domain>``，不是 ``kp.<domain>``
- 必须同时带 ``User-Agent`` 与 ``Accept: application/json, text/plain, */*``，
  否则会被 Cloudflare 302 到 google 或 403
- ``/torrent/search`` 用 JSON body；``/torrent/genDlToken`` 用查询串
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ...config import SiteConfig
from ...scoring import Candidate
from ..base import HTTPClient, SiteError
from .parser import parse_candidates


class MTeamClient(HTTPClient):
    def __init__(self, cfg: SiteConfig):
        super().__init__(cfg.base_url, cfg.timeout)
        self.cfg = cfg
        self._key = cfg.resolve_key()

    def _headers(self, with_body: bool = True) -> Dict[str, str]:
        headers = {
            "User-Agent": self.cfg.user_agent,
            "Accept": "application/json, text/plain, */*",
            "x-api-key": self._key,
        }
        # 只用查询串的接口不要声明 JSON body，否则部分接口会判定参数错误
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _call(self, path: str, params: Optional[Dict] = None,
              body: Optional[Dict] = None) -> Dict:
        data = self.request(
            path,
            method="POST",
            headers=self._headers(body is not None),
            params=params,
            body=body,
        )
        if str(data.get("code")) != "0":
            detail = "m-team 业务错误 %s" % path
            if params:
                detail += " 参数=%s" % params
            detail += ": %s" % data.get("message")
            raise SiteError(detail)
        return data

    def search(self, page: int = 1, page_size: int = 100) -> List[Candidate]:
        data = self._call(
            "/api/torrent/search",
            body={"pageNumber": page, "pageSize": page_size},
        )
        return parse_candidates(data)

    def genlink(self, tid: str) -> Optional[str]:
        data = self._call("/api/torrent/genDlToken", params={"id": tid})
        url = data.get("data")
        return url if isinstance(url, str) else None
