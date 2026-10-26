"""MySQL 相关测试：没有可用的 MySQL 库时自动跳过。

本地跑法：
    docker run -d --name ptsf-mysql-test -e MYSQL_ROOT_PASSWORD=testpass \
        -e MYSQL_DATABASE=ptsf_test -p 3366:3306 mysql:8.0
    PT_TEST_MYSQL=1 PT_DB_PORT=3366 PT_DB_PASSWORD=testpass pytest -q tests/test_mysql.py
"""
import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PT_TEST_MYSQL") != "1",
    reason="需要 MySQL（设置 PT_TEST_MYSQL=1）",
)


@pytest.fixture()
def db():
    from app.db import Database
    database = Database.from_env()
    database.ensure_schema()
    for table in ("services", "sites", "meta", "traffic", "torrent_archive"):
        database.execute("DELETE FROM %s" % table)
    yield database
    database.close()


def test_config_repo_save_and_load(db):
    from app.config import AppConfig, DownloaderConfig, Limits, ServiceConfig, SiteConfig
    from app.confrepo import ConfigRepo

    repo = ConfigRepo(db)
    assert repo.is_empty()

    cfg = AppConfig(
        host="0.0.0.0", port=8600, tick_seconds=300, refresh_every_ticks=12,
        sites={"mteam": SiteConfig(name="mteam", base_url="https://api.m-team.cc",
                                   user_agent="UA/1.0")},
        services=[ServiceConfig(name="auto", site="mteam", disk_gb=150,
                                limits=Limits())],
        downloader=DownloaderConfig(),
    )
    repo.save_all(cfg)
    assert not repo.is_empty()

    loaded = repo.load()
    assert loaded.services[0].disk_gb == 150
    assert "mteam" in loaded.sites
    assert loaded.tick_seconds == 300


def test_repo_save_service_updates_existing(db):
    from app.config import ServiceConfig
    from app.confrepo import ConfigRepo

    repo = ConfigRepo(db)
    repo.save_service(ServiceConfig(name="auto", site="mteam", disk_gb=100))
    repo.save_service(ServiceConfig(name="auto", site="mteam", disk_gb=250))
    assert repo.get_service("auto").disk_gb == 250
    assert len(repo.service_configs()) == 1


def test_mysql_store_traffic_and_archive(db):
    from app.db import MySQLStore

    store = MySQLStore(db)
    now = int(time.time())
    store.add_traffic("s1", now, 100, 10)
    store.add_traffic("s1", now, 50, 5)
    assert store.traffic_total("s1") == (150, 15)

    store.archive_torrent("s1", "h1", "n", 10, 4, 8, 0.5, removed_at=now)
    assert store.archive_totals("s1") == (4, 8)


def test_mysql_store_matches_sqlite_api():
    """两套存储必须暴露同一组方法，避免生产缺方法、单测却全绿。"""
    from app.db import MySQLStore
    from app.store import Store

    sqlite_api = {n for n in dir(Store) if not n.startswith("_")}
    mysql_api = {n for n in dir(MySQLStore) if not n.startswith("_")}
    assert sqlite_api - mysql_api == set(), "MySQLStore 缺少: %s" % sorted(sqlite_api - mysql_api)


def test_merge_missing_only_adds_new(db):
    from app.config import AppConfig, DownloaderConfig, ServiceConfig, SiteConfig
    from app.confrepo import ConfigRepo

    repo = ConfigRepo(db)
    repo.save_service(ServiceConfig(name="auto-mteam-1", site="mteam", disk_gb=100))
    repo.save_site(SiteConfig(name="mteam", base_url="https://api.m-team.cc", user_agent="ua"))

    cfg = AppConfig(
        host="0.0.0.0", port=8600, tick_seconds=300, refresh_every_ticks=12,
        sites={
            "mteam": SiteConfig(name="mteam", base_url="https://api.m-team.cc", user_agent="ua"),
            "pttime": SiteConfig(name="pttime", base_url="https://www.pttime.org",
                                 user_agent="ua", type="nexusphp_html",
                                 list_url="torrents.php?spstate=2"),
        },
        services=[
            ServiceConfig(name="auto-mteam-1", site="mteam", disk_gb=999),   # 已存在，不应覆盖
            ServiceConfig(name="auto-pttime-1", site="pttime", disk_gb=50),
        ],
        downloader=DownloaderConfig(),
    )
    added = repo.merge_missing(cfg)
    assert added["services"] == ["auto-pttime-1"]
    assert added["sites"] == ["pttime"]
    # 老服务保持数据库里的值，不被配置文件覆盖
    assert repo.get_service("auto-mteam-1").disk_gb == 100
    assert repo.get_service("auto-pttime-1").disk_gb == 50
