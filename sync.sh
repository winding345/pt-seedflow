#!/usr/bin/env bash
# 只负责同步代码：先试默认远端，失败再试 GitHub 加速镜像。
# 拉取成功后再运行 deploy.sh。
set -uo pipefail
cd "$(dirname "$0")"

MIRROR="${PT_MIRROR:-https://ghproxy.net/https://github.com/winding345/pt-seedflow.git}"

echo "--- 尝试 origin（最多 45s）---"
if GIT_TERMINAL_PROMPT=0 timeout 45 git pull --ff-only; then
  echo "[ok] 已从 origin 更新"
  git log --oneline -1
  exit 0
fi

echo "--- origin 失败，尝试镜像 ---"
echo "    $MIRROR"
if GIT_TERMINAL_PROMPT=0 timeout 60 git pull --ff-only "$MIRROR" main; then
  echo "[ok] 已从镜像更新"
  git log --oneline -1
  exit 0
fi

echo "[fail] origin 与镜像都不可用"
echo "提示：可改用 Gitee，见 README 的同步说明"
exit 1
