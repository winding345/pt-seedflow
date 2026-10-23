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
