"""站点适配的通用底座。

新增站点时只需要：
1. 在本目录下建一个子包，例如 ``pttime/``
2. 实现 ``SiteAdapter`` 的两个方法：``search`` 与 ``genlink``
3. 用 ``@register("pttime")`` 注册

其余调度、选种、配额、删除逻辑与站点无关，全部复用。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from ..scoring import Candidate


class SiteError(RuntimeError):
    """站点请求或返回不符合预期。"""


class SiteAdapter(ABC):
    """站点需要提供的能力。"""

    name: str = ""

    @abstractmethod
    def search(self, page: int = 1, page_size: int = 100) -> List[Candidate]:
        """返回候选种子列表（已归一化为 Candidate）。"""

    @abstractmethod
    def genlink(self, tid: str) -> Optional[str]:
        """把种子 id 换成可直接交给下载器的链接。"""


class HTTPClient:
    """带超时、错误归一的轻量 HTTP 客户端。

    只依赖标准库，避免为每个站点引入额外依赖。
    """

    def __init__(self, base_url: str, timeout: int = 25, proxy: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.proxy = proxy

    def _open(self, request: Request):
        """有代理就带代理请求（DNS 由代理解析，可绕开本地投毒）。"""
        if self.proxy:
            opener = build_opener(ProxyHandler({"http": self.proxy, "https": self.proxy}))
            return opener.open(request, timeout=self.timeout)
        return urlopen(request, timeout=self.timeout)

    def request(self, path: str, method: str = "POST",
                headers: Optional[Dict[str, str]] = None,
                params: Optional[Dict[str, Any]] = None,
                body: Optional[Dict[str, Any]] = None,
                expect_json: bool = True) -> Any:
        url = self.base_url + path
        if params:
            url = url + "?" + urlencode(params)

        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")

        request = Request(url, data=payload, headers=headers or {}, method=method)
        try:
            with self._open(request) as resp:
                raw = resp.read()
        except HTTPError as exc:
            raise SiteError("%s 返回 HTTP %s" % (path, exc.code))
        except URLError as exc:
            raise SiteError("%s 连接失败: %s" % (path, exc.reason))

        if not expect_json:
            return raw
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SiteError("%s 返回非 JSON（可能被站点拦截）" % path)
