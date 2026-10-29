"""配置加载与校验。

设计原则：
- 代码里不含任何密钥；所有凭据来自 config.yaml 或其指向的文件。
- 配置非法时拒绝启动，并给出可操作的中文错误信息。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

CONFIG_HOME = Path(os.environ.get("PT_SEEDFLOW_HOME", "~/.pt-seedflow")).expanduser()
CONFIG_FILE = CONFIG_HOME / "config.yaml"
STATE_DB = CONFIG_HOME / "state.db"


class ConfigError(RuntimeError):
    """配置不合法，无法启动。"""


_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: Any) -> Any:
    """展开 ${VAR} 与 ${VAR:-default}，便于 Docker 环境注入。"""
    if isinstance(value, str):
        def _sub(match: "re.Match[str]") -> str:
            name, default = match.group(1), match.group(2)
            resolved = os.environ.get(name, default)
            if resolved is None:
                raise ConfigError("配置引用了未设置的环境变量 %s" % name)
            return resolved
        return _ENV_RE.sub(_sub, value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


@dataclass
class Limits:
    search_per_day: int = 1000
    dl_per_day: int = 1000
    safety: float = 0.9

    def to_dict(self) -> Dict[str, Any]:
        return {"search_per_day": self.search_per_day,
                "dl_per_day": self.dl_per_day,
                "safety": self.safety}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Limits":
        data = data or {}
        return cls(
            search_per_day=int(data.get("search_per_day", 1000)),
            dl_per_day=int(data.get("dl_per_day", 1000)),
            safety=float(data.get("safety", 0.9)),
        )

    @property
    def search_cap(self) -> int:
        return max(1, int(self.search_per_day * self.safety))

    @property
    def dl_cap(self) -> int:
        return max(1, int(self.dl_per_day * self.safety))


@dataclass
class SiteConfig:
    name: str
    base_url: str
    user_agent: str
    api_key: str = ""
    api_key_file: str = ""
    timeout: int = 25
    # 适配器类型；留空则用 name（m-team 这类专属适配器直接按名字注册）
    type: str = ""
    # NexusPHP 站点的 RSS 地址，通常已带 passkey
    rss_url: str = ""
    # 促销关键词 → 统一促销标记，例如 {"免费": "FREE", "2X免费": "_2X_FREE"}
    discount_tags: Dict[str, str] = field(default_factory=dict)
    # NexusPHP HTML 列表页：登录 cookie 与字段选择器
    cookie: str = ""
    # 下载链接需要的 passkey（部分站点下载链接只带 session，qB 用不了）
    passkey: str = ""
    # 该站点走代理：http://127.0.0.1:7890 之类；留空则直连
    proxy: str = ""
    list_url: str = ""
    selectors: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "base_url": self.base_url,
                "user_agent": self.user_agent, "api_key_file": self.api_key_file,
                "timeout": self.timeout, "type": self.type,
                "rss_url": self.rss_url, "discount_tags": dict(self.discount_tags),
                "cookie": self.cookie, "passkey": self.passkey,
                "proxy": self.proxy, "list_url": self.list_url,
                "selectors": dict(self.selectors)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SiteConfig":
        data = data or {}
        return cls(
            name=str(data.get("name") or ""),
            base_url=str(data.get("base_url") or "").rstrip("/"),
            user_agent=str(data.get("user_agent") or "Mozilla/5.0"),
            api_key=str(data.get("api_key") or ""),
            api_key_file=str(data.get("api_key_file") or ""),
            timeout=int(data.get("timeout", 25)),
            type=str(data.get("type") or ""),
            rss_url=str(data.get("rss_url") or ""),
            discount_tags={str(k): str(v) for k, v in (data.get("discount_tags") or {}).items()},
            cookie=str(data.get("cookie") or ""),
            passkey=str(data.get("passkey") or ""),
            proxy=str(data.get("proxy") or ""),
            list_url=str(data.get("list_url") or ""),
            selectors=dict(data.get("selectors") or {}),
        )

    def resolve_key(self) -> str:
        """优先使用 api_key，其次读文件；两者都没有则报错。"""
        if self.api_key:
            return self.api_key.strip()
        if self.api_key_file:
            path = Path(self.api_key_file).expanduser()
            if not path.is_file():
                raise ConfigError("site %s 的 api_key_file 不存在: %s" % (self.name, path))
            key = path.read_text(encoding="utf-8").strip()
            if not key:
                raise ConfigError("site %s 的 api_key_file 内容为空: %s" % (self.name, path))
            return key
        raise ConfigError("site %s 未配置 api_key 或 api_key_file" % self.name)


@dataclass
class DownloaderConfig:
    base_url: str = "http://127.0.0.1:8085"
    username: str = ""
    password: str = ""
    timeout: int = 20


@dataclass
class DownloaderConfig:
    base_url: str = "http://127.0.0.1:8085"
    username: str = ""
    password: str = ""
    timeout: int = 20

    def to_dict(self) -> Dict[str, Any]:
        return {"base_url": self.base_url, "username": self.username,
                "password": self.password, "timeout": self.timeout}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DownloaderConfig":
        data = data or {}
        return cls(
            base_url=str(data.get("base_url") or "http://127.0.0.1:8085").rstrip("/"),
            username=str(data.get("username") or ""),
            password=str(data.get("password") or ""),
            timeout=int(data.get("timeout", 20)),
        )


@dataclass
class ServiceConfig:
    name: str
    site: str
    enabled: bool = True
    dry_run: bool = True
    disk_gb: float = 50
    discounts: List[str] = field(default_factory=list)
    categories: List[int] = field(default_factory=list)
    size_mb: List[float] = field(default_factory=lambda: [1024, 102400])
    save_path: str = ""
    category: str = ""
    delete_threshold_mb: float = 1024
    window_minutes: int = 60
    exempt_minutes: int = 60
    min_keep_minutes: int = 120
    max_torrents: int = 30
    add_per_tick: int = 2
    # 未下完但促销已结束 → 删除
    delete_unfinished_after_discount: bool = True
    # search 强制最小间隔（秒）
    search_interval_seconds: int = 3600
    # 搜索最多翻几页；只抓第 1 页时会被"已加过"的候选吃光
    search_max_pages: int = 5
    # 过滤掉已加过之后，池子至少要有这么多条才停止翻页
    search_min_pool: int = 30
    # 候选池被吃光时是否提前补一轮
    refill_when_empty: bool = True
    # 提前补池的最小间隔（秒），避免对站点太频繁
    refill_min_seconds: int = 600
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self) -> None:
        # qB 分类默认与服名一致，避免配置里漏填导致操作到别人的任务
        if not self.category:
            self.category = self.name

    @property
    def disk_bytes(self) -> int:
        return int(self.disk_gb * 1024 ** 3)

    @property
    def threshold_bytes(self) -> int:
        return int(self.delete_threshold_mb * 1024 ** 2)

    @property
    def size_bytes_range(self) -> "tuple[int, int]":
        low, high = self.size_mb[0], self.size_mb[1]
        return int(low * 1024 ** 2), int(high * 1024 ** 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "site": self.site,
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "disk_gb": self.disk_gb,
            "discounts": list(self.discounts),
            "categories": list(self.categories),
            "size_mb": list(self.size_mb),
            "save_path": self.save_path,
            "category": self.category,
            "delete_threshold_mb": self.delete_threshold_mb,
            "window_minutes": self.window_minutes,
            "exempt_minutes": self.exempt_minutes,
            "min_keep_minutes": self.min_keep_minutes,
            "max_torrents": self.max_torrents,
            "add_per_tick": self.add_per_tick,
            "delete_unfinished_after_discount": self.delete_unfinished_after_discount,
            "search_interval_seconds": self.search_interval_seconds,
            "search_max_pages": self.search_max_pages,
            "search_min_pool": self.search_min_pool,
            "refill_when_empty": self.refill_when_empty,
            "refill_min_seconds": self.refill_min_seconds,
            "limits": self.limits.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServiceConfig":
        data = data or {}
        name = str(data.get("name") or "")
        site = str(data.get("site") or "")
        if not name or not site:
            raise ConfigError("服务配置缺少 name 或 site")
        return cls(
            name=name,
            site=site,
            enabled=bool(data.get("enabled", True)),
            dry_run=bool(data.get("dry_run", True)),
            disk_gb=float(data.get("disk_gb", 50)),
            discounts=[str(x) for x in (data.get("discounts") or [])],
            categories=[int(x) for x in (data.get("categories") or [])],
            size_mb=[float((data.get("size_mb") or [1024, 102400])[0]),
                     float((data.get("size_mb") or [1024, 102400])[1])],
            save_path=str(data.get("save_path") or ""),
            category=str(data.get("category") or name),
            delete_threshold_mb=float(data.get("delete_threshold_mb", 1024)),
            window_minutes=int(data.get("window_minutes", 60)),
            exempt_minutes=int(data.get("exempt_minutes", 60)),
            min_keep_minutes=int(data.get("min_keep_minutes", 120)),
            max_torrents=int(data.get("max_torrents", 30)),
            add_per_tick=int(data.get("add_per_tick", 2)),
            delete_unfinished_after_discount=bool(
                data.get("delete_unfinished_after_discount", True)),
            search_interval_seconds=int(data.get("search_interval_seconds", 3600)),
            search_max_pages=int(data.get("search_max_pages", 5)),
            search_min_pool=int(data.get("search_min_pool", 30)),
            refill_when_empty=bool(data.get("refill_when_empty", True)),
            refill_min_seconds=int(data.get("refill_min_seconds", 600)),
            limits=Limits.from_dict(data.get("limits") or {}),
        )

    def allows_discount(self, discount: Optional[str]) -> bool:
        value = (discount or "NORMAL").strip()
        if value in ("", "NORMAL"):
            return False
        if not self.discounts:
            return True
        return value in self.discounts


@dataclass
class AppConfig:
    host: str
    port: int
    tick_seconds: int
    refresh_every_ticks: int
    sites: Dict[str, SiteConfig]
    services: List[ServiceConfig]
    downloader: DownloaderConfig


def _require(mapping: Dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError("%s 缺少必填项 %s" % (where, key))
    return mapping[key]


def seed_file() -> Path:
    """随镜像发布的种子配置（新增服务用）。"""
    return Path(__file__).resolve().parent.parent / "config" / "services.seed.yaml"


def load_seed(path: Optional[Path] = None):
    """加载种子配置，返回 (services, sites)。

    与 load_config 不同：不做密钥存在性校验，cookie 允许为空——这些都在面板里补。
    """
    import yaml

    target = Path(path) if path else seed_file()
    if not target.is_file():
        return [], {}
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}

    sites: Dict[str, SiteConfig] = {}
    for name, cfg in (raw.get("sites") or {}).items():
        cfg = dict(cfg or {})
        cfg.setdefault("name", name)
        cfg.setdefault("base_url", "https://%s" % name)
        cfg.setdefault("user_agent", "Mozilla/5.0")
        sites[name] = SiteConfig.from_dict(cfg)

    services: List[ServiceConfig] = []
    for item in raw.get("services") or []:
        services.append(ServiceConfig.from_dict(dict(item)))
    return services, sites


def load_config(path: Optional[Path] = None) -> AppConfig:
    """读取并校验配置；任何不合法都抛 ConfigError。"""
    target = Path(path) if path else CONFIG_FILE
    if not target.is_file():
        raise ConfigError("配置文件不存在: %s" % target)

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError("配置文件不是合法 YAML: %s" % exc)

    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是映射结构")

    raw = _expand(raw)

    server = raw.get("server") or {}
    defaults = raw.get("defaults") or {}
    limits_raw = defaults.get("limits") or {}

    limits = Limits(
        search_per_day=int(limits_raw.get("search_per_day", 1000)),
        dl_per_day=int(limits_raw.get("dl_per_day", 1000)),
        safety=float(limits_raw.get("safety", 0.9)),
    )
    if not 0 < limits.safety <= 1:
        raise ConfigError("defaults.limits.safety 必须位于 (0, 1] 区间")

    sites_raw = raw.get("sites") or {}
    if not isinstance(sites_raw, dict) or not sites_raw:
        raise ConfigError("至少需要配置一个 site")

    sites: Dict[str, SiteConfig] = {}
    for name, cfg in sites_raw.items():
        if not isinstance(cfg, dict):
            raise ConfigError("site %s 必须是映射结构" % name)
        sites[name] = SiteConfig(
            name=name,
            base_url=str(_require(cfg, "base_url", "site %s" % name)).rstrip("/"),
            user_agent=str(cfg.get("user_agent") or "Mozilla/5.0"),
            api_key=str(cfg.get("api_key") or ""),
            api_key_file=str(cfg.get("api_key_file") or ""),
            timeout=int(cfg.get("timeout", 25)),
            type=str(cfg.get("type") or ""),
            rss_url=str(cfg.get("rss_url") or ""),
            discount_tags={str(k): str(v)
                           for k, v in (cfg.get("discount_tags") or {}).items()},
            cookie=str(cfg.get("cookie") or ""),
            passkey=str(cfg.get("passkey") or ""),
            proxy=str(cfg.get("proxy") or ""),
            list_url=str(cfg.get("list_url") or ""),
            selectors=dict(cfg.get("selectors") or {}),
        )
        # 提前校验，避免服务跑起来才发现
        kind = sites[name].type or sites[name].name
        if kind == "nexusphp":
            if not sites[name].rss_url:
                raise ConfigError("site %s 使用 nexusphp 适配器时必须配置 rss_url" % name)
        elif kind == "nexusphp_html":
            if not sites[name].list_url:
                raise ConfigError("site %s 使用 nexusphp_html 适配器时必须配置 list_url" % name)
            # cookie 允许先留空：启动后可在面板补充，届时热加载生效
        else:
            sites[name].resolve_key()

    services_raw = raw.get("services") or []
    if not isinstance(services_raw, list) or not services_raw:
        raise ConfigError("至少需要配置一个 service")

    services: List[ServiceConfig] = []
    seen = set()
    for item in services_raw:
        if not isinstance(item, dict):
            raise ConfigError("service 条目必须是映射结构")
        name = str(_require(item, "name", "service"))
        if name in seen:
            raise ConfigError("service 名称重复: %s" % name)
        seen.add(name)

        site = str(_require(item, "site", "service %s" % name))
        if site not in sites:
            raise ConfigError("service %s 引用了未定义的 site: %s" % (name, site))

        merged = dict(defaults)
        merged.pop("limits", None)
        merged.update(item)

        size_mb = merged.get("size_mb", [1024, 102400])
        if not isinstance(size_mb, list) or len(size_mb) != 2:
            raise ConfigError("service %s 的 size_mb 必须是两个元素的数组" % name)
        if float(size_mb[0]) > float(size_mb[1]):
            raise ConfigError("service %s 的 size_mb 下限不能大于上限" % name)

        service = ServiceConfig(
            name=name,
            site=site,
            enabled=bool(merged.get("enabled", True)),
            dry_run=bool(merged.get("dry_run", True)),
            disk_gb=float(merged.get("disk_gb", 50)),
            discounts=[str(x) for x in (merged.get("discounts") or [])],
            categories=[int(x) for x in (merged.get("categories") or [])],
            size_mb=[float(size_mb[0]), float(size_mb[1])],
            save_path=str(merged.get("save_path") or ""),
            category=str(merged.get("category") or name),
            delete_threshold_mb=float(merged.get("delete_threshold_mb", 1024)),
            window_minutes=int(merged.get("window_minutes", 60)),
            exempt_minutes=int(merged.get("exempt_minutes", 60)),
            min_keep_minutes=int(merged.get("min_keep_minutes", 120)),
            max_torrents=int(merged.get("max_torrents", 30)),
            add_per_tick=int(merged.get("add_per_tick", 2)),
            delete_unfinished_after_discount=bool(
                merged.get("delete_unfinished_after_discount", True)),
            search_interval_seconds=int(merged.get("search_interval_seconds", 3600)),
            limits=limits,
        )
        if service.disk_gb <= 0:
            raise ConfigError("service %s 的 disk_gb 必须大于 0" % name)
        if service.max_torrents <= 0:
            raise ConfigError("service %s 的 max_torrents 必须大于 0" % name)
        services.append(service)

    if not any(s.enabled for s in services):
        raise ConfigError("所有 service 都是 enabled: false，没有可运行的服务")

    downloader_raw = raw.get("downloader") or {}
    downloader = DownloaderConfig(
        base_url=str(downloader_raw.get("base_url") or "http://127.0.0.1:8085").rstrip("/"),
        username=str(downloader_raw.get("username") or ""),
        password=str(downloader_raw.get("password") or ""),
        timeout=int(downloader_raw.get("timeout", 20)),
    )

    return AppConfig(
        host=str(server.get("host", "0.0.0.0")),
        port=int(server.get("port", 8600)),
        tick_seconds=int(server.get("tick_seconds", 300)),
        refresh_every_ticks=int(server.get("refresh_every_ticks", 12)),
        sites=sites,
        services=services,
        downloader=downloader,
    )
