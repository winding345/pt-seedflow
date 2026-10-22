"""程序入口：校验配置 → 组装依赖 → 启动调度与 Web。"""
from __future__ import annotations

import sys
import time

import uvicorn

from . import bootstrap
from .config import STATE_DB, load_config
from .qb import QBittorrent
from .scheduler import Scheduler, ServiceRunner
from .sites import create_site
from .store import Store
from .web import create_app


def build_runners(config) -> list:
    store = Store(STATE_DB)
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


def main() -> int:
    bootstrap.prepare()
    config = load_config()

    runners = build_runners(config)
    if not runners:
        sys.stderr.write("没有启用的服务\n")
        return 2

    scheduler = Scheduler(runners, config.tick_seconds)
    scheduler.start()

    app = create_app(runners, started_at=time.time())
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
