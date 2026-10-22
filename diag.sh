#!/usr/bin/env bash
# 诊断：确认分类隔离是否生效，并查看最近动作
cd "$(dirname "$0")"

echo "--- qB 分类统计 ---"
curl -s 127.0.0.1:8085/api/v2/torrents/info | python3 -c "
import json,sys,collections
d=json.load(sys.stdin)
print('总任务', len(d))
c=collections.Counter((t.get('category') or '(无分类)') for t in d)
for k,v in c.most_common(8): print('  %-24s %d' % (k, v))
mine=[t for t in d if (t.get('category') or '')=='auto-mteam-1']
print('auto-mteam-1 下 %d 个:' % len(mine))
for t in mine[:5]:
    print('   %-50s %.2fGB %s' % (t['name'][:50], t['size']/1024**3, t['state']))
"

echo "--- 服务最近动作 ---"
curl -s 127.0.0.1:8600/api/status | python3 -c "
import json,sys
d=json.load(sys.stdin)
for s in d['services']:
    print('服务', s['name'], 'dry_run=', s['dry_run'], '候选=', s['candidates'])
    for a in s['actions'][:8]:
        print('  %s %-10s %-40s %s' % (a['time'], a['kind'], a['name'][:40], a['detail']))
"
