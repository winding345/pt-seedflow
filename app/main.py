"""程序入口：配置写库 → 组装依赖 → 启动调度与 Web。"""
from __future__ import annotations

import argparse
import os
import sys
import time

import uvicorn

from . import bootstrap
from .config import ConfigError, load_config
from .confrepo import ConfigRepo
from .db import Database, MySQLStore
from .qb import QBittorrent
from .scheduler import Scheduler, ServiceRunner
from .sites import create_site
from .web import create_app


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pt-seedflow")
    parser.add_argument("--cfg_force", action="store_true",
                        help="用 config.yaml 覆盖数据库里的配置")
    return parser.parse_args(argv)


def build_runners(config, store) -> list:
    qb = QBittorrent(config.downloader)
    runners = []
    for service in config.services:
        if not service.enabled:
            continue
        site_cfg = config.sites[service.site]
        runners.append(ServiceRunner(
            cfg=service,
            site=create_site(site_cfg),
            qb=qb,
            store=store,
            tick_seconds=config.tick_seconds,
            refresh_every_ticks=config.refresh_every_ticks,
        ))
    return runners


def main(argv=None) -> int:
    args = parse_args(argv)
    bootstrap.prepare()
    file_config = load_config()

    try:
        db = Database.from_env()
        db.ensure_schema()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("连接 MySQL 失败：%s\n" % exc)
        return 3

    repo = ConfigRepo(db)
    force = args.cfg_force or os.environ.get("PT_CFG_FORCE") == "1"
    if force or repo.is_empty():
        repo.save_all(file_config)
        print("[config] 已用 config.yaml %s数据库配置"
              % ("覆盖" if force else "初始化"), flush=True)

    try:
        config = repo.load()
    except ConfigError as exc:
        sys.stderr.write("读取数据库配置失败：%s\n" % exc)
        return 2

    store = MySQLStore(db)
    runners = build_runners(config, store)
    if not runners:
        sys.stderr.write("没有启用的服务\n")
        return 2

    reload_seconds = int(os.environ.get("PT_CFG_RELOAD_SECONDS", "300"))
    scheduler = Scheduler(runners, config.tick_seconds, repo=repo,
                          reload_seconds=reload_seconds)
    scheduler.start()

    app = create_app(runners, started_at=time.time(), repo=repo)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
