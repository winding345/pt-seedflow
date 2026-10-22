#!/usr/bin/env bash
# 查看部署进度与容器状态
cd "$(dirname "$0")"

echo "--- 构建日志尾部 ---"
tail -c 400 /tmp/psf.log 2>/dev/null || echo "(暂无日志)"
echo
echo "--- qB 地址 ---"
grep -A2 '^downloader:' data/config.yaml | grep base_url || true
echo "--- 容器 ---"
docker compose ps --format '{{.Name}} {{.State}} {{.Ports}}' 2>/dev/null || true
echo "--- 状态接口 ---"
curl -sS -m 5 http://127.0.0.1:8600/api/status | head -c 400 || echo "(未响应)"
echo
