"""NexusPHP（pttime）RSS 适配器测试，全部离线。"""
from pathlib import Path

import pytest

from app.config import SiteConfig
from app.sites.nexusphp import NexusPHPSite, detect_discount, parse_rss, parse_size_text

FIXTURE = Path(__file__).parent / "fixtures" / "pttime_rss.xml"


def test_parse_size_text():
    assert parse_size_text("4.5 GB") == int(4.5 * 1024 ** 3)
    assert parse_size_text("800 MiB") == 800 * 1024 ** 2
    assert parse_size_text("") == 0


def test_detect_discount_by_keyword():
    tags = {"2X免费": "_2X_FREE", "免费": "FREE"}
    assert detect_discount("[2X免费] 标题", tags) == "_2X_FREE"   # 长的优先
    assert detect_discount("[免费] 标题", tags) == "FREE"
    assert detect_discount("普通标题", tags) == "NORMAL"


def test_parse_rss_items():
    candidates = parse_rss(FIXTURE.read_text(encoding="utf-8"),
                           {"2X免费": "_2X_FREE", "免费": "FREE"})
    assert len(candidates) == 3
    first = candidates[0]
    assert first.size == 4831838208
    assert first.discount == "FREE"
    assert "passkey=abc123" in first.url          # 直链可用，不必换 token
    assert candidates[1].discount == "_2X_FREE"
    assert candidates[2].discount == "NORMAL"


def test_site_search_and_genlink(monkeypatch):
    cfg = SiteConfig(name="pttime", base_url="https://pttime.org",
                     user_agent="UA/1.0", type="nexusphp",
                     rss_url="https://pttime.org/getrss.php?passkey=abc123",
                     discount_tags={"2X免费": "_2X_FREE", "免费": "FREE"})
    site = NexusPHPSite(cfg)
    monkeypatch.setattr(site, "_fetch", lambda: FIXTURE.read_text(encoding="utf-8"))

    candidates = site.search(page_size=100)
    assert len(candidates) == 3
    assert site.genlink(candidates[0].tid) == candidates[0].url


def test_missing_rss_url_rejected():
    from app.sites.base import SiteError
    cfg = SiteConfig(name="pttime", base_url="https://pttime.org", user_agent="UA/1.0")
    with pytest.raises(SiteError):
        NexusPHPSite(cfg)
