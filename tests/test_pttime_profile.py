"""用 pttime 真实页面（脱敏）验证内置模板。"""
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from app.config import SiteConfig
from app.sites.base import SiteError
from app.sites.nexusphp_html import NexusPHPHtmlSite, parse_list_html

FIXTURE = Path(__file__).parent / "fixtures" / "pttime_real.html"
BASE = "https://www.pttime.org"


def test_profile_parses_real_page():
    from app.sites.nexusphp_html import SITE_PROFILES
    profile = SITE_PROFILES["pttime.org"]
    rows = parse_list_html(FIXTURE.read_text(encoding="utf-8"),
                           profile["selectors"],
                           profile["selectors"]["discount_case"], BASE)
    assert len(rows) == 3

    first = rows[0]
    assert "Make Zhonghe Great Again" in first.name
    assert first.discount == "FREE"
    assert first.size > 20 * 1024 ** 3
    assert first.url.startswith("https://www.pttime.org/download.php?id=")
    assert first.tid.isdigit()
    assert first.seeders >= 0 and first.leechers >= 0
    # 发布时间取「存活」列的绝对时间
    assert first.created is not None
    assert datetime.fromtimestamp(first.created).year >= 2020

    counts = Counter(r.discount for r in rows)
    assert counts["FREE"] >= 1
    assert counts["NORMAL"] >= 1          # 页面里确实有未标促销的种子


def test_profile_applied_automatically(monkeypatch):
    cfg = SiteConfig(name="pttime", base_url="https://www.pttime.org",
                     user_agent="UA/1.0", type="nexusphp_html",
                     cookie="c=1", list_url="")
    site = NexusPHPHtmlSite(cfg)
    assert cfg.list_url == "torrents.php?spstate=2"          # 来自内置模板
    assert "torrenttable" in cfg.selectors["row"]
    monkeypatch.setattr(site, "_fetch", lambda: FIXTURE.read_text(encoding="utf-8"))
    rows = site.search()
    assert len(rows) == 3
    assert site.genlink(rows[0].tid) == rows[0].url


def test_config_override_wins():
    cfg = SiteConfig(name="pttime", base_url="https://www.pttime.org",
                     user_agent="ua", type="nexusphp_html", cookie="c=1",
                     selectors={"size": "td:nth-child(9)"})
    NexusPHPHtmlSite(cfg)
    assert cfg.selectors["size"] == "td:nth-child(9)"
    assert "row" in cfg.selectors            # 未覆盖的沿用模板
