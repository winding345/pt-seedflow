#!/usr/bin/env bash
# 只负责部署：重建容器并启动。不碰 git，永远不会卡在网络上。
# 更新代码请单独运行 sync.sh
set -uo pipefail
cd "$(dirname "$0")"

mkdir -p data
if [ ! -f data/config.yaml ]; then
  cp config/services.example.yaml data/config.yaml
  echo "已生成 data/config.yaml，请修改后重试"
  exit 2
fi

# 容器使用 host 网络，历史配置里的 host.docker.internal 需要迁移
sed -i 's#host\.docker\.internal#127.0.0.1#g' data/config.yaml

echo "--- 当前提交 ---"
git log --oneline -1 2>/dev/null || true

nohup docker compose up -d --build --force-recreate > /tmp/psf.log 2>&1 &
echo "STARTED (log: /tmp/psf.log)"
