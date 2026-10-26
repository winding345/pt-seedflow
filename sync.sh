#!/usr/bin/env bash
# 同步代码到最新 main。
#
# 设计要点：
# - 多镜像轮询，单个源只等 20 秒，避免卡在一个挂掉的镜像上
# - 直接 fetch + reset --hard：这个目录是部署用，不允许有本地改动挡路
# - 可用 PT_MIRROR 指定单个源，比如 PT_MIRROR=https://ghfast.top
set -uo pipefail
cd "$(dirname "$0")"

REPO="https://github.com/winding345/pt-seedflow.git"
MIRRORS=(
  "https://gh-proxy.com/https://github.com/winding345/pt-seedflow.git"
  "https://ghfast.top/https://github.com/winding345/pt-seedflow.git"
  "https://ghproxy.net/https://github.com/winding345/pt-seedflow.git"
  "$REPO"
)

if [ -n "${PT_MIRROR:-}" ]; then
  MIRRORS=("$PT_MIRROR")
fi

for url in "${MIRRORS[@]}"; do
  printf -- '--- %s（最多 20s）---\n' "$url"
  if GIT_TERMINAL_PROMPT=0 timeout 20 git fetch --no-tags "$url" main 2>&1; then
    git reset --hard FETCH_HEAD >/dev/null
    echo "[ok] 已更新"
    git log --oneline -1
    exit 0
  fi
done

echo "[fail] 所有源都不可用" >&2
echo "换一个源重试：PT_MIRROR=https://gh-proxy.com/https://github.com/winding345/pt-seedflow.git bash sync.sh" >&2
exit 1
