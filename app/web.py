"""Web 面板：后端只提供 JSON 接口与静态文件，页面由 static/ 下的前端渲染。"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .scheduler import ServiceRunner

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def build_status(runners: List[ServiceRunner], started_at: float) -> dict:
    uptime = int(time.time() - started_at)
    hours, rest = divmod(uptime, 3600)
    minutes, seconds = divmod(rest, 60)
    return {
        "uptime_seconds": uptime,
        "uptime_human": "%d 小时 %d 分" % (hours, minutes) if hours else "%d 分 %d 秒" % (minutes, seconds),
        "services": [r.snapshot() for r in runners],
    }


def _asset_version() -> int:
    """用 app.js 的修改时间做版本号，部署后浏览器会自动取新文件。"""
    try:
        return int((STATIC_DIR / "app.js").stat().st_mtime)
    except OSError:
        return 0


def create_app(runners: List[ServiceRunner], started_at: float) -> FastAPI:
    app = FastAPI(title="pt-seedflow", docs_url=None, redoc_url=None)

    # ---------- API ----------
    @app.get("/api/status")
    def status() -> JSONResponse:
        return JSONResponse(build_status(runners, started_at))

    @app.post("/api/trigger")
    def trigger(payload: dict = Body(...)) -> JSONResponse:
        name = payload.get("service")
        action = payload.get("action", "both")
        runner = next((r for r in runners if r.cfg.name == name), None)
        if runner is None:
            return JSONResponse({"error": "未知服务: %s" % name}, status_code=404)
        try:
            return JSONResponse(runner.manual(action))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/services")
    def services_json() -> JSONResponse:
        return JSONResponse({"services": [r.snapshot() for r in runners]})

    @app.post("/api/services/{name}/search")
    def service_search(name: str) -> JSONResponse:
        runner = next((r for r in runners if r.cfg.name == name), None)
        if runner is None:
            return JSONResponse({"error": "未知服务: %s" % name}, status_code=404)
        return JSONResponse(runner.manual_search())

    @app.get("/api/services/{name}/torrents")
    def service_torrents(name: str) -> JSONResponse:
        runner = next((r for r in runners if r.cfg.name == name), None)
        if runner is None:
            return JSONResponse({"error": "未知服务: %s" % name}, status_code=404)
        return JSONResponse(runner.torrent_rows())

    @app.get("/api/services/{name}/traffic")
    def service_traffic(name: str, days: int = 1, bucket: str = "hour") -> JSONResponse:
        runner = next((r for r in runners if r.cfg.name == name), None)
        if runner is None:
            return JSONResponse({"error": "未知服务: %s" % name}, status_code=404)
        if bucket not in ("hour", "day"):
            return JSONResponse({"error": "bucket 只能是 hour 或 day"}, status_code=400)

        days = max(1, min(30, int(days)))
        since = int(time.time() - days * 86400)
        rows = runner.store.traffic_rows(name, since)

        buckets = {}
        for ts, downloaded, uploaded in rows:
            moment = datetime.fromtimestamp(ts)
            if bucket == "day":
                moment = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                moment = moment.replace(minute=0, second=0, microsecond=0)
            key = int(moment.timestamp())
            item = buckets.setdefault(key, [0, 0])
            item[0] += downloaded
            item[1] += uploaded

        points = [{"ts": ts, "downloaded": buckets[ts][0], "uploaded": buckets[ts][1]}
                  for ts in sorted(buckets)]
        return JSONResponse({"bucket": bucket, "days": days, "points": points})

    # ---------- 静态资源 ----------
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        version = _asset_version()
        html = html.replace('href="style.css"', 'href="style.css?v=%d"' % version)
        html = html.replace('src="app.js"', 'src="app.js?v=%d"' % version)
        return HTMLResponse(html, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        })

    @app.get("/app.js")
    def app_js() -> FileResponse:
        return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/style.css")
    def style_css() -> FileResponse:
        return FileResponse(STATIC_DIR / "style.css", media_type="text/css",
                            headers={"Cache-Control": "no-cache"})

    return app
