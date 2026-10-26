"""站点注册表：配置里的 site 名称 → 适配器类。"""
from __future__ import annotations

from typing import Dict, Type

from ..config import SiteConfig
from .base import SiteAdapter, SiteError

_REGISTRY: Dict[str, Type[SiteAdapter]] = {}


def register(name: str):
    """把适配器类登记到站点表。"""

    def wrapper(cls: Type[SiteAdapter]) -> Type[SiteAdapter]:
        _REGISTRY[name] = cls
        cls.name = name
        return cls

    return wrapper


def available() -> Dict[str, Type[SiteAdapter]]:
    return dict(_REGISTRY)


def create_site(cfg: SiteConfig) -> SiteAdapter:
    """按配置创建适配器；未注册的名称直接报错，避免静默走错站点。"""
    # 触发内置站点的注册
    from . import mteam, nexusphp, nexusphp_html  # noqa: F401

    kind = cfg.type or cfg.name
    cls = _REGISTRY.get(kind)
    if cls is None:
        raise SiteError("未知站点类型: %s（已注册: %s）" % (kind, ", ".join(sorted(_REGISTRY))))
    return cls(cfg)
