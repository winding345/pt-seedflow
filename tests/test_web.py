"""Web 接口与静态资源的测试（用假 runner，不连数据库与网络）。"""
from fastapi.testclient import TestClient

from app.web import create_app


class FakeRunner:
    def __init__(self, name="auto-test"):
        self.cfg = type("Cfg", (), {"name": name})()

    def snapshot(self):
        return {
            "name": self.cfg.name,
            "site": "mteam",
            "last_error": "",
            "config": {
                "enabled": True, "dry_run": False, "disk_gb": 50.0,
                "discounts": ["FREE"], "categories": [], "size_mb": [1024, 102400],
                "save_path": "", "category": self.cfg.name,
                "delete_threshold_mb": 1024.0, "window_minutes": 60,
                "exempt_minutes": 60, "min_keep_minutes": 120,
                "max_torrents": 30, "add_per_tick": 2,
                "limits": {"search_per_day": 1000, "dl_per_day": 100, "safety": 0.9},
            },
            "stats": {
                "used_gb": 12.5, "torrents": 3, "candidates": 100,
                "current_downloaded": 1, "current_uploaded": 2,
                "since_start_downloaded": 3, "since_start_uploaded": 4,
                "added_total": 5, "removed_total": 1,
                "search_used": 1, "search_cap": 900,
                "dl_used": 6, "dl_cap": 90,
                "next_search_seconds": 600, "quota_exhausted": False,
            },
            "actions": [],
        }

    def torrent_rows(self):
        return {
            "active": [{
                "name": "示例种子", "size": 1024, "downloaded": 512, "uploaded": 2048,
                "last_hour_upload": 100, "ratio": 4.0, "progress": 50.0,
                "seeds": 10, "leechs": 20, "dlspeed": 1024, "upspeed": 2048,
                "added_on": 1700000000, "last_activity": 1700000100, "state": "downloading",
            }],
            "removed": [],
        }

    def manual(self, action):
        return {"service": self.cfg.name, "action": action, "done": ["add"],
                "snapshot": self.snapshot()}


def client():
    return TestClient(create_app([FakeRunner()], started_at=0))


def test_index_serves_html_shell():
    resp = client().get("/")
    assert resp.status_code == 200
    body = resp.text
    assert 'app.js' in body and 'pt-seedflow' in body
    assert resp.headers["cache-control"].startswith("no-store")


def test_static_assets_served():
    api = client()
    assert api.get("/app.js").status_code == 200
    assert api.get("/style.css").status_code == 200


def test_status_payload_shape():
    data = client().get("/api/status").json()
    assert "services" in data and len(data["services"]) == 1
    svc = data["services"][0]
    assert svc["config"]["dry_run"] is False
    assert svc["stats"]["dl_cap"] == 90


def test_torrents_endpoint():
    data = client().get("/api/services/auto-test/torrents").json()
    assert data["active"][0]["ratio"] == 4.0
    assert data["removed"] == []


def test_torrents_unknown_service_404():
    assert client().get("/api/services/nope/torrents").status_code == 404


def test_trigger_endpoint():
    resp = client().post("/api/trigger", json={"service": "auto-test", "action": "add"})
    assert resp.status_code == 200
    assert resp.json()["done"] == ["add"]


def test_site_registry_rejects_unknown():
    import pytest

    from app.config import SiteConfig
    from app.sites import available, create_site
    from app.sites.base import SiteError

    assert "mteam" in available()
    with pytest.raises(SiteError):
        create_site(SiteConfig(name="nothere", base_url="https://x", user_agent="ua",
                               api_key="k"))
