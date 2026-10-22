#!/usr/bin/env bash
# NAS 上的部署入口：保持命令很短，方便在受限终端里执行
# 用法：
#   bash deploy.sh                 # 先拉代码（最多 45s），再重建
#   PT_SKIP_PULL=1 bash deploy.sh  # 跳过拉取，直接用本地代码
set -uo pipefail
cd "$(dirname "$0")"

if [ "${PT_SKIP_PULL:-0}" != "1" ]; then
  echo "--- 拉取代码（最多 45s）---"
  if GIT_TERMINAL_PROMPT=0 timeout 45 git pull --ff-only; then
    echo "[ok] 代码已更新"
  else
    echo "[warn] 拉取失败或超时，改用本地代码继续"
  fi
fi

mkdir -p data
if [ ! -f data/config.yaml ]; then
  cp config/services.example.yaml data/config.yaml
  echo "已生成 data/config.yaml，请修改后重试"
  exit 2
fi

# 容器使用 host 网络，历史配置里的 host.docker.internal 需要迁移
sed -i 's#host\.docker\.internal#127.0.0.1#g' data/config.yaml

echo "--- 当前提交 ---"
git log --oneline -1 || true

nohup docker compose up -d --build --force-recreate > /tmp/psf.log 2>&1 &
echo "STARTED (log: /tmp/psf.log)"
