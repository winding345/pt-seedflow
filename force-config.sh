#!/usr/bin/env bash
# 用 data/config.yaml 覆盖数据库里的配置，然后重启容器
set -euo pipefail
cd "$(dirname "$0")"
PT_CFG_FORCE=1 docker compose up -d --build --force-recreate
echo "已按 --cfg_force 语义用配置文件覆盖数据库"
