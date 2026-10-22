"""qBittorrent Web API 客户端。

只做三件事：列出任务、按 URL 加种、删除任务。
全部走标准库 urllib，避免额外依赖。
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import uuid
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .config import DownloaderConfig
from .lifecycle import TorrentView


class QBError(RuntimeError):
    pass


def _multipart(fields: Dict[str, str]) -> "tuple[bytes, str]":
    boundary = "----ptseedflow" + uuid.uuid4().hex
    lines: List[bytes] = []
    for key, value in fields.items():
        lines.append(("--%s\r\n" % boundary).encode())
        lines.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode())
        lines.append(str(value).encode("utf-8"))
        lines.append(b"\r\n")
    lines.append(("--%s--\r\n" % boundary).encode())
    return b"".join(lines), "multipart/form-data; boundary=%s" % boundary


class QBittorrent:
    def __init__(self, cfg: DownloaderConfig):
        self.cfg = cfg
        self._jar = http.cookiejar.CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._jar))
        self._logged_in = not (cfg.username and cfg.password)

    # ---------- 基础 ----------
    def _open(self, path: str, data: Optional[bytes] = None,
              content_type: Optional[str] = None, method: Optional[str] = None):
        url = self.cfg.base_url + path
        headers = {"Referer": self.cfg.base_url}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(url, data=data, headers=headers, method=method)
        try:
            return self._opener.open(request, timeout=self.cfg.timeout)
        except HTTPError as exc:
            raise QBError("qB 请求失败 %s: HTTP %s" % (path, exc.code))
        except URLError as exc:
            raise QBError("qB 连接失败 %s: %s" % (path, exc.reason))

    def login(self) -> None:
        if self._logged_in:
            return
        body = urlencode({"username": self.cfg.username, "password": self.cfg.password}).encode()
        with self._open("/api/v2/auth/login", data=body,
                        content_type="application/x-www-form-urlencoded") as resp:
            text = resp.read().decode("utf-8", "replace")
        if text.strip() != "Ok.":
            raise QBError("qB 登录失败（检查 downloader.username/password）")
        self._logged_in = True

    def _post_form(self, path: str, fields: Dict[str, str]) -> str:
        self.login()
        body = urlencode(fields).encode()
        with self._open(path, data=body,
                        content_type="application/x-www-form-urlencoded") as resp:
            return resp.read().decode("utf-8", "replace")

    def _post_multipart(self, path: str, fields: Dict[str, str]) -> str:
        self.login()
        body, content_type = _multipart(fields)
        with self._open(path, data=body, content_type=content_type) as resp:
            return resp.read().decode("utf-8", "replace")

    def _get_json(self, path: str) -> Any:
        self.login()
        with self._open(path) as resp:
            raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            raise QBError("qB 返回非 JSON: %s" % path)

    # ---------- 业务 ----------
    def list_torrents(self, category: Optional[str] = None) -> List[TorrentView]:
        path = "/api/v2/torrents/info"
        if category:
            path += "?" + urlencode({"category": category})
        rows = self._get_json(path)
        result: List[TorrentView] = []
        for row in rows:
            result.append(TorrentView(
                hash=str(row.get("hash") or ""),
                name=str(row.get("name") or ""),
                size=int(row.get("size") or 0),
                progress=float(row.get("progress") or 0.0),
                state=str(row.get("state") or ""),
                dlspeed=int(row.get("dlspeed") or 0),
                upspeed=int(row.get("upspeed") or 0),
                uploaded=int(row.get("uploaded") or 0),
                added_on=int(row.get("added_on") or 0) or None,
                category=str(row.get("category") or ""),
            ))
        return result

    def add_url(self, url: str, category: str = "", save_path: str = "",
                paused: bool = False, auto_tmm: bool = False) -> bool:
        fields = {"urls": url, "paused": "true" if paused else "false"}
        if category:
            fields["category"] = category
        if save_path:
            fields["savepath"] = save_path
            # 配了显式路径就必须关掉自动管理，否则会被分类路径覆盖
            fields["autoTMM"] = "true" if auto_tmm else "false"
        text = self._post_multipart("/api/v2/torrents/add", fields).strip()
        return text.startswith("Ok")

    def delete(self, hashes: List[str], delete_files: bool = True) -> bool:
        if not hashes:
            return True
        text = self._post_form("/api/v2/torrents/delete", {
            "hashes": "|".join(hashes),
            "deleteFiles": "true" if delete_files else "false",
        })
        return "Fails" not in text


def ensure_dirs(path: str) -> None:
    if path and not os.path.isdir(path):
        # 这里不主动建目录：路径属于 qB 容器，建了也没用
        pass
