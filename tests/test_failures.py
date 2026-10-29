"""服务初始化失败的处理：重试、面板展示、自动恢复。"""
import pytest

from app.web import build_status


class _FakeRunner:
    def __init__(self, name):
        class C:
            pass
        self.cfg = C()
        self.cfg.name = name

    def snapshot(self):
        return {"name": self.cfg.name, "site": "x", "last_error": "",
                "config": {}, "stats": {}, "actions": []}


def test_status_includes_failure_card():
    failures = {"auto-mteam-1": {"name": "auto-mteam-1", "site": "mteam",
                                 "error": "未知站点类型", "config": {"enabled": True}}}
    data = build_status([_FakeRunner("auto-carpt-1")], 0, failures)
    names = [s["name"] for s in data["services"]]
    assert names == ["auto-carpt-1", "auto-mteam-1"]
    broken = data["services"][1]
    assert broken["broken"] is True
    assert "未知站点类型" in broken["last_error"]
    assert broken["actions"][0]["kind"] == "error"


def test_status_hides_failure_when_service_alive():
    failures = {"auto-mteam-1": {"name": "auto-mteam-1", "site": "mteam", "error": "x"}}
    data = build_status([_FakeRunner("auto-mteam-1")], 0, failures)
    assert [s["name"] for s in data["services"]] == ["auto-mteam-1"]
    assert not data["services"][0].get("broken")


def test_build_runners_records_failure(monkeypatch):
    import app.main as main_mod
    from app.config import AppConfig, DownloaderConfig, ServiceConfig, SiteConfig

    def boom(_cfg):
        raise RuntimeError("站点挂了")

    monkeypatch.setattr(main_mod, "create_site", boom)
    cfg = AppConfig(
        host="127.0.0.1", port=8600, tick_seconds=60, refresh_every_ticks=60,
        services=[ServiceConfig(name="s1", site="mteam")],
        sites={"mteam": SiteConfig(name="mteam", base_url="https://x", user_agent="ua")},
        downloader=DownloaderConfig(),
    )
    failures = {}
    runners = main_mod.build_runners(cfg, store=None, failures=failures)
    assert runners == []
    assert "s1" in failures
    assert "站点挂了" in failures["s1"]["error"]
