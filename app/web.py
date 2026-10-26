"""Web 面板：后端只提供 JSON 接口与静态文件，页面由 static/ 下的前端渲染。"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import ServiceConfig, SiteConfig
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


def create_app(runners: List[ServiceRunner], started_at: float, repo=None) -> FastAPI:
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

    @app.get("/api/services/{name}/config")
    def get_service_config(name: str) -> JSONResponse:
        runner = next((r for r in runners if r.cfg.name == name), None)
        if runner is None:
            return JSONResponse({"error": "未知服务: %s" % name}, status_code=404)
        return JSONResponse(runner.cfg.to_dict())

    @app.put("/api/services/{name}/config")
    def put_service_config(name: str, payload: dict = Body(...)) -> JSONResponse:
        runner = next((r for r in runners if r.cfg.name == name), None)
        if runner is None:
            return JSONResponse({"error": "未知服务: %s" % name}, status_code=404)
        data = dict(payload or {})
        data["name"] = name
        try:
            service = ServiceConfig.from_dict(data)
        except Exception as exc:  # noqa: BLE001  不做复杂校验，但格式错误要能提示
            return JSONResponse({"error": "配置格式错误: %s" % exc}, status_code=400)
        if repo is not None:
            repo.save_service(service)
        runner.apply_config(service)
        return JSONResponse({"ok": True, "config": service.to_dict()})

    @app.get("/api/sites/{name}/config")
    def get_site_config(name: str) -> JSONResponse:
        if repo is None:
            return JSONResponse({"error": "配置未启用"}, status_code=503)
        site = repo.get_site(name)
        if site is None:
            return JSONResponse({"error": "未知站点: %s" % name}, status_code=404)
        return JSONResponse(site.to_dict())

    @app.put("/api/sites/{name}/config")
    def put_site_config(name: str, payload: dict = Body(...)) -> JSONResponse:
        if repo is None:
            return JSONResponse({"error": "配置未启用"}, status_code=503)
        data = dict(payload or {})
        data["name"] = name
        try:
            site = SiteConfig.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": "站点配置格式错误: %s" % exc}, status_code=400)
        repo.save_site(site)
        # 立即生效：重建使用该站点的 runner 的适配器
        from .sites import create_site
        for runner in runners:
            if runner.cfg.site != name:
                continue
            try:
                runner.site = create_site(site)
                runner._site_signature = repr(sorted(site.to_dict().items(), key=lambda kv: kv[0]))
                runner._log("config", "站点配置已更新", name)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"ok": True, "warning": "已保存，但重建适配器失败: %s" % exc})
        return JSONResponse({"ok": True})

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
