"""NexusPHP HTML 列表页适配器测试（离线）。"""
from pathlib import Path

import pytest

from app.config import SiteConfig
from app.sites.nexusphp_html import NexusPHPHtmlSite, parse_list_html

FIXTURE = Path(__file__).parent / "fixtures" / "pttime_list.html"


def test_parse_rows_with_promo():
    candidates = parse_list_html(FIXTURE.read_text(encoding="utf-8"), {}, {},
                                 "https://www.pttime.org")
    assert len(candidates) == 3
    first = candidates[0]
    assert first.name.startswith("Make Zhonghe")
    assert first.size == int(21.05 * 1024 ** 3)
    assert first.discount == "FREE"
    assert "passkey=xyz" in first.url
    assert first.tid == "127120"
    assert first.created is not None
    assert candidates[1].discount == "NORMAL"
    assert candidates[2].discount == "_2X_FREE"


def test_site_search_and_genlink(monkeypatch):
    cfg = SiteConfig(name="demo", base_url="https://example.org",
                     user_agent="UA/1.0", type="nexusphp_html",
                     list_url="torrents.php?spstate=2", cookie="c=1")
    site = NexusPHPHtmlSite(cfg)
    monkeypatch.setattr(site, "_fetch", lambda: FIXTURE.read_text(encoding="utf-8"))
    rows = site.search()
    assert len(rows) == 3
    assert site.genlink(rows[0].tid) == rows[0].url


def test_missing_cookie_rejected():
    from app.sites.base import SiteError
    cfg = SiteConfig(name="demo", base_url="https://x", user_agent="ua",
                     type="nexusphp_html", list_url="torrents.php")
    with pytest.raises(SiteError):
        NexusPHPHtmlSite(cfg)
