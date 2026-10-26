"""把配置存进 MySQL，并在运行时热加载。

优先级规则：
- 启动带 --cfg_force：用 config.yaml 覆盖数据库
- 否则：数据库里有服务配置就以数据库为准；数据库为空才用 config.yaml 初始化
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from .config import (AppConfig, ConfigError, DownloaderConfig, ServiceConfig,
                     SiteConfig)
from .db import Database


class ConfigRepo:
    def __init__(self, db: Database):
        self.db = db

    # ---------- 读 ----------
    def is_empty(self) -> bool:
        rows = self.db.query("SELECT COUNT(*) FROM services")
        return int(rows[0][0]) == 0

    def load(self) -> AppConfig:
        rows = self.db.query("SELECT config FROM services")
        services: List[ServiceConfig] = []
        for (payload,) in rows:
            try:
                services.append(ServiceConfig.from_dict(json.loads(payload)))
            except Exception as exc:  # noqa: BLE001
                raise ConfigError("数据库里的服务配置无法解析: %s" % exc)

        site_rows = self.db.query("SELECT config FROM sites")
        sites: Dict[str, SiteConfig] = {}
        for (payload,) in site_rows:
            cfg = SiteConfig.from_dict(json.loads(payload))
            site = SiteConfig.from_dict({**cfg.to_dict(), "name": cfg.name})
            sites[site.name] = site

        meta = {k: v for k, v in self.db.query("SELECT k, v FROM meta")}
        server = json.loads(meta.get("server") or "{}")
        downloader = DownloaderConfig.from_dict(
            json.loads(meta.get("downloader") or "{}"))

        if not services:
            raise ConfigError("数据库里没有服务配置")
        if not sites:
            raise ConfigError("数据库里没有站点配置")

        return AppConfig(
            host=str(server.get("host", "0.0.0.0")),
            port=int(server.get("port", 8600)),
            tick_seconds=int(server.get("tick_seconds", 300)),
            refresh_every_ticks=int(server.get("refresh_every_ticks", 12)),
            sites=sites,
            services=services,
            downloader=downloader,
        )

    def service_configs(self) -> Dict[str, ServiceConfig]:
        """热加载用：只取服务配置。"""
        rows = self.db.query("SELECT config FROM services")
        result: Dict[str, ServiceConfig] = {}
        for (payload,) in rows:
            svc = ServiceConfig.from_dict(json.loads(payload))
            result[svc.name] = svc
        return result

    def get_site(self, name: str) -> Optional[SiteConfig]:
        rows = self.db.query("SELECT config FROM sites WHERE name=%s", (name,))
        if not rows:
            return None
        return SiteConfig.from_dict(json.loads(rows[0][0]))

    def site_configs(self) -> Dict[str, SiteConfig]:
        rows = self.db.query("SELECT config FROM sites")
        result: Dict[str, SiteConfig] = {}
        for (payload,) in rows:
            site = SiteConfig.from_dict(json.loads(payload))
            result[site.name] = site
        return result

    def save_site(self, site: SiteConfig) -> None:
        self.db.execute(
            "INSERT INTO sites(name, config, updated_at) VALUES (%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE config=VALUES(config), updated_at=VALUES(updated_at)",
            (site.name, json.dumps(site.to_dict(), ensure_ascii=False), int(time.time())))

    def service_names(self) -> List[str]:
        return [r[0] for r in self.db.query("SELECT name FROM services ORDER BY name")]

    def get_service(self, name: str) -> Optional[ServiceConfig]:
        rows = self.db.query("SELECT config FROM services WHERE name=%s", (name,))
        if not rows:
            return None
        return ServiceConfig.from_dict(json.loads(rows[0][0]))

    # ---------- 写 ----------
    def save_all(self, cfg: AppConfig) -> None:
        now = int(time.time())
        for service in cfg.services:
            self.db.execute(
                "INSERT INTO services(name, config, updated_at) VALUES (%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE config=VALUES(config), updated_at=VALUES(updated_at)",
                (service.name, json.dumps(service.to_dict(), ensure_ascii=False), now))
        for site in cfg.sites.values():
            self.db.execute(
                "INSERT INTO sites(name, config, updated_at) VALUES (%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE config=VALUES(config), updated_at=VALUES(updated_at)",
                (site.name, json.dumps(site.to_dict(), ensure_ascii=False), now))
        self._set_meta("server", {
            "host": cfg.host, "port": cfg.port,
            "tick_seconds": cfg.tick_seconds,
            "refresh_every_ticks": cfg.refresh_every_ticks,
        })
        self._set_meta("downloader", cfg.downloader.to_dict())

    def save_service(self, service: ServiceConfig) -> None:
        self.db.execute(
            "INSERT INTO services(name, config, updated_at) VALUES (%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE config=VALUES(config), updated_at=VALUES(updated_at)",
            (service.name, json.dumps(service.to_dict(), ensure_ascii=False),
             int(time.time())))

    def _set_meta(self, key: str, value: dict) -> None:
        self.db.execute(
            "INSERT INTO meta(k, v) VALUES (%s,%s)"
            " ON DUPLICATE KEY UPDATE v=VALUES(v)",
            (key, json.dumps(value, ensure_ascii=False)))
