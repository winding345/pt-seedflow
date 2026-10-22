"""站点适配层。"""
from __future__ import annotations

from typing import Dict, Type

from ..config import SiteConfig

_REGISTRY: Dict[str, Type] = {}


def register(name: str):
    def wrapper(cls):
        _REGISTRY[name] = cls
        return cls

    return wrapper


def create_site(cfg: SiteConfig):
    if cfg.name not in _REGISTRY:
        from . import mteam  # noqa: F401  触发注册
    cls = _REGISTRY.get(cfg.name) or _REGISTRY.get("mteam")
    if cls is None:
        raise ValueError("未知站点类型: %s" % cfg.name)
    return cls(cfg)
