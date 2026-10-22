#!/usr/bin/env bash
# 手动触发一次加/删，不触发 search
# 用法: trigger.sh [add|delete|both] [服务名]
set -euo pipefail
cd "$(dirname "$0")"

ACTION="${1:-both}"
SERVICE="${2:-auto-mteam-1}"

curl -sS -m 120 -X POST \
  -H 'Content-Type: application/json' \
  -d "{\"service\":\"$SERVICE\",\"action\":\"$ACTION\"}" \
  http://127.0.0.1:8600/api/trigger \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d.get('error'):
    print('错误:', d['error'])
    sys.exit(1)
print('服务:', d['service'], '| 动作:', d['action'], '| 已执行:', ','.join(d.get('done', [])) or '无')
s = d['snapshot']
print('磁盘: %.2f/%.0f GB | 任务: %d/%d | 候选: %d' % (
    s['used_gb'], s['disk_gb'], s['torrents'], s['max_torrents'], s['candidates']))
for a in s['actions'][:5]:
    print('  %s %-10s %-40s %s' % (a['time'], a['kind'], a['name'][:40], a['detail']))
"
