"""启动引导：准备配置目录，并在缺失配置时给出明确指引。"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .config import CONFIG_FILE, CONFIG_HOME, ConfigError, load_config

EXAMPLE_FILE = Path(__file__).resolve().parent.parent / "config" / "services.example.yaml"


def ensure_config_dir() -> None:
    CONFIG_HOME.mkdir(parents=True, exist_ok=True)


def materialize_example() -> bool:
    """若配置不存在则复制示例；返回是否发生了复制。"""
    if CONFIG_FILE.exists():
        return False
    if not EXAMPLE_FILE.is_file():
        raise ConfigError("找不到示例配置: %s" % EXAMPLE_FILE)
    ensure_config_dir()
    shutil.copyfile(EXAMPLE_FILE, CONFIG_FILE)
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        # 某些挂载点不支持改权限，不阻塞启动流程
        pass
    return True


def prepare() -> None:
    """启动前准备：复制示例配置并校验，失败则退出。"""
    ensure_config_dir()
    if materialize_example():
        sys.stderr.write(
            "\n已生成默认配置: %s\n"
            "请先编辑其中的 api_key_file、services 等参数，然后重新启动。\n"
            "本次不启动服务。\n\n" % CONFIG_FILE
        )
        raise SystemExit(2)
    try:
        load_config()
    except ConfigError as exc:
        sys.stderr.write("配置校验失败: %s\n配置文件: %s\n" % (exc, CONFIG_FILE))
        raise SystemExit(2)
