"""CARPT（标准 NexusPHP）真实页面脱敏样本测试。"""
from collections import Counter
from pathlib import Path

from app.config import SiteConfig
from app.sites.nexusphp_html import SITE_PROFILES, NexusPHPHtmlSite, parse_list_html

FIXTURE = Path(__file__).parent / "fixtures" / "carpt_real.html"
BASE = "https://carpt.net"


def test_carpt_profile_parses():
    profile = SITE_PROFILES["carpt.net"]
    rows = parse_list_html(FIXTURE.read_text(encoding="utf-8"),
                           profile["selectors"], {},
                           BASE, passkey="TESTKEY")
    assert len(rows) == 3
    counts = Counter(r.discount for r in rows)
    assert counts["FREE"] >= 1
    assert counts["_2X_FREE"] >= 1        # pro_free2up 是 2X免费，不能算普通免费

    first = rows[0]
    assert first.size > 0
    assert first.created is not None
    assert first.tid.isdigit()
    # 页面里的下载链接不带 passkey，适配器要补上，否则 qB 拿不到种子
    assert "passkey=TESTKEY" in first.url


def test_carpt_login_url_built(monkeypatch):
    cfg = SiteConfig(name="carpt", base_url="https://carpt.net",
                     user_agent="ua", type="nexusphp_html",
                     cookie="c=1", passkey="TESTKEY")
    site = NexusPHPHtmlSite(cfg)
    assert cfg.list_url == "torrents.php?spstate=2"
    monkeypatch.setattr(site, "_fetch", lambda: FIXTURE.read_text(encoding="utf-8"))
    rows = site.search()
    assert len(rows) == 3
    assert site.genlink(rows[0].tid) == rows[0].url
