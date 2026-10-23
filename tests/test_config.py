import json
from pathlib import Path

import pytest

from app.config import (ConfigError, DownloaderConfig, Limits, ServiceConfig,
                        SiteConfig, load_config)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def build(tmp_path: Path, services: str, defaults_extra: str = "", key_path: str = "") -> Path:
    key = key_path or str(tmp_path / "key.txt")
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n"
        "  port: 8600\n"
        "  tick_seconds: 300\n"
        "defaults:\n"
        "  disk_gb: 20\n"
        "  limits:\n"
        "    search_per_day: 1000\n"
        "    dl_per_day: 1000\n"
        "    safety: 0.9\n"
        + defaults_extra
        + "sites:\n"
        "  mteam:\n"
        "    base_url: https://api.m-team.cc\n"
        "    api_key_file: %s\n" % key
        + "services:\n"
        + services
        + "downloader:\n"
        "  base_url: http://127.0.0.1:8085\n"
    )
    return config


def default_key(tmp_path: Path) -> None:
    (tmp_path / "key.txt").write_text("019a3309-1251-77d4-bf0e-5da0287ee4a8\n")


ONE_SERVICE = "  - name: s1\n    site: mteam\n    disk_gb: 50\n"


def test_load_minimal_config(tmp_path):
    default_key(tmp_path)
    cfg = load_config(build(tmp_path, ONE_SERVICE))
    assert cfg.port == 8600
    assert cfg.tick_seconds == 300
    assert len(cfg.services) == 1
    svc = cfg.services[0]
    assert svc.name == "s1"
    assert svc.disk_gb == 50
    assert svc.category == "s1"          # 未配置时用服务名
    assert svc.dry_run is True           # 默认演练模式
    assert svc.limits.dl_cap == 900      # 1000 * 0.9


def test_defaults_are_inherited_and_overridable(tmp_path):
    default_key(tmp_path)
    cfg = load_config(build(
        tmp_path,
        ONE_SERVICE,
        defaults_extra="  delete_threshold_mb: 512\n  min_keep_minutes: 30\n",
    ))
    svc = cfg.services[0]
    assert svc.delete_threshold_mb == 512   # 来自 defaults
    assert svc.min_keep_minutes == 30       # 来自 defaults
    assert svc.disk_gb == 50                # 服务内覆盖了 defaults 的 20


def test_service_overrides_default_threshold(tmp_path):
    default_key(tmp_path)
    cfg = load_config(build(
        tmp_path,
        "  - name: s1\n    site: mteam\n    delete_threshold_mb: 2048\n",
        defaults_extra="  delete_threshold_mb: 512\n",
    ))
    assert cfg.services[0].delete_threshold_mb == 2048


def test_missing_key_file_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="api_key_file"):
        load_config(build(tmp_path, ONE_SERVICE))


def test_unknown_site_is_rejected(tmp_path):
    default_key(tmp_path)
    with pytest.raises(ConfigError, match="未定义的 site"):
        load_config(build(tmp_path, "  - name: s1\n    site: nothere\n"))


def test_duplicate_service_names_rejected(tmp_path):
    default_key(tmp_path)
    duplicated = "  - name: s1\n    site: mteam\n  - name: s1\n    site: mteam\n"
    with pytest.raises(ConfigError, match="名称重复"):
        load_config(build(tmp_path, duplicated))


def test_size_range_order_validated(tmp_path):
    default_key(tmp_path)
    services = "  - name: s1\n    site: mteam\n    size_mb: [5000, 100]\n"
    with pytest.raises(ConfigError, match="下限不能大于上限"):
        load_config(build(tmp_path, services))


def test_size_range_requires_two_values(tmp_path):
    default_key(tmp_path)
    services = "  - name: s1\n    site: mteam\n    size_mb: [100]\n"
    with pytest.raises(ConfigError, match="两个元素"):
        load_config(build(tmp_path, services))


def test_bad_yaml_rejected(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("services: [\n")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(config)


def test_all_services_disabled_rejected(tmp_path):
    default_key(tmp_path)
    services = "  - name: s1\n    site: mteam\n    enabled: false\n"
    with pytest.raises(ConfigError, match="enabled"):
        load_config(build(tmp_path, services))


def test_env_expansion(tmp_path, monkeypatch):
    default_key(tmp_path)
    monkeypatch.setenv("MY_KEY_PATH", str(tmp_path / "key.txt"))
    cfg = load_config(build(tmp_path, ONE_SERVICE, key_path="${MY_KEY_PATH}"))
    assert cfg.sites["mteam"].resolve_key().startswith("019a3309")


def test_inline_api_key_accepted(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "sites:\n"
        "  mteam:\n"
        "    base_url: https://api.m-team.cc\n"
        "    api_key: inline-key-123\n"
        "services:\n"
        "  - name: s1\n"
        "    site: mteam\n"
    )
    cfg = load_config(config)
    assert cfg.sites["mteam"].resolve_key() == "inline-key-123"


# ---------- 真实响应样本解析 ----------
def test_parse_real_search_fixture():
    from app.sites.mteam import parse_torrents

    payload = json.loads((FIXTURE_DIR / "mteam_search.json").read_text(encoding="utf-8"))
    candidates = parse_torrents(payload)
    assert len(candidates) == 2
    first = candidates[0]
    assert first.tid == "1275479"
    assert first.size > 0
    assert first.created is not None
    assert first.seeders >= 0 and first.leechers >= 0
    assert first.discount in {
        "FREE", "PERCENT_50", "PERCENT_70", "_2X", "_2X_FREE", "_2X_PERCENT_50", "NORMAL",
    }


def test_discount_end_matches_source():
    from app.sites.mteam import parse_torrents

    payload = json.loads((FIXTURE_DIR / "mteam_search.json").read_text(encoding="utf-8"))
    candidate = parse_torrents(payload)[0]
    status = payload["data"]["data"][0]["status"]
    if status.get("discountEndTime") and not status.get("promotionRule"):
        assert candidate.discount_end is not None
    else:
        assert candidate.discount_end is None


def test_service_config_roundtrip():
    svc = ServiceConfig(
        name="demo", site="mteam", disk_gb=150, size_mb=[5120, 102400],
        max_torrents=60, add_per_tick=5, search_interval_seconds=1800,
        delete_unfinished_after_discount=False,
        limits=Limits(search_per_day=1000, dl_per_day=100, safety=0.9),
    )
    again = ServiceConfig.from_dict(svc.to_dict())
    assert again.to_dict() == svc.to_dict()


def test_site_and_downloader_roundtrip():
    site = SiteConfig(name="mteam", base_url="https://api.m-team.cc",
                      user_agent="UA/1.0", api_key_file="~/.rss/key", timeout=30)
    assert SiteConfig.from_dict(site.to_dict()).to_dict() == site.to_dict()

    # 结尾斜杠会被规整掉，这是预期行为
    slashed = SiteConfig(name="mteam", base_url="https://api.m-team.cc/",
                         user_agent="UA/1.0", timeout=30)
    assert SiteConfig.from_dict(slashed.to_dict()).base_url == "https://api.m-team.cc"

    dl = DownloaderConfig(base_url="http://127.0.0.1:8085", username="u",
                          password="p", timeout=15)
    assert DownloaderConfig.from_dict(dl.to_dict()).to_dict() == dl.to_dict()


def test_service_config_requires_name_and_site():
    import pytest
    with pytest.raises(ConfigError):
        ServiceConfig.from_dict({"name": "", "site": "mteam"})
