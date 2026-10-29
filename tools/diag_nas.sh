#!/usr/bin/env bash
# NAS 侧一次性诊断脚本（只读，不改任何状态）
# 用法：bash tools/diag_nas.sh [service_name]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

SVC="${1:-auto-mteam-1}"
API=http://127.0.0.1:8600

PW=$(grep '^MYSQL_PASSWORD=' .env 2>/dev/null | cut -d= -f2)
MYSQL="docker exec pt-seedflow-mysql mysql -uptsf -p${PW} -N"

echo "=== 0. TLS 诊断（容器内 Python vs 宿主机）==="
echo "-- 容器环境代理 --"
docker exec pt-seedflow env 2>/dev/null | grep -i proxy || echo "(无 proxy 变量)"
echo "-- 容器时间 --"
docker exec pt-seedflow date 2>/dev/null
echo "-- 宿主机解析(v4) --"
getent ahostsv4 api.m-team.cc | head -2
echo "-- 宿主机解析(v6) --"
getent ahostsv6 api.m-team.cc | head -2
echo "-- 宿主机证书签发者 --"
timeout 8 openssl s_client -connect api.m-team.cc:443 -servername api.m-team.cc </dev/null 2>/dev/null \
  | openssl x509 -noout -issuer -subject 2>/dev/null || echo "(openssl 超时或不可用)"
echo "-- 容器内 Python 直连 --"
docker exec pt-seedflow python -c "
import urllib.request, ssl
for desc, ctx in (('verify', ssl.create_default_context()), ('noverify', ssl._create_unverified_context())):
    try:
        r = urllib.request.urlopen('https://api.m-team.cc/', timeout=10, context=ctx)
        print(desc, 'ok', r.status)
    except Exception as e:
        print(desc, 'fail', type(e).__name__, str(e)[:90])
" 2>&1 | head -6
echo "-- 容器内证书链 --"
docker exec pt-seedflow python -c "
import ssl, socket
ctx = ssl.create_default_context()
try:
    with socket.create_connection(('api.m-team.cc', 443), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname='api.m-team.cc') as ss:
            print('peer cert subject:', dict(x[0] for x in ss.getpeercert()['subject']))
            print('issuer:', dict(x[0] for x in ss.getpeercert()['issuer']))
except Exception as e:
    print('chain fail:', type(e).__name__, str(e)[:120])
" 2>&1 | head -4

echo
echo "=== 1. TLS 可达性（从 NAS 测 m-team）==="
curl -sS -o /dev/null -m 15 -w 'https://api.m-team.cc -> %{http_code}\n' \
     https://api.m-team.cc/ 2>&1 | head -2

echo
echo "=== 2. 服务概览 ==="
curl -s "$API/api/status" > /tmp/ptsf_status.json
python3 - "$SVC" <<'PY'
import json, sys
svc = sys.argv[1]
d = json.load(open('/tmp/ptsf_status.json'))
for s in d['services']:
    c, st = s['config'], s['stats']
    mark = '*' if s['name'] == svc else ' '
    print("%s %-14s en=%-5s dry=%-5s disk=%-5s used=%-6s trr=%-3s cand=%-4s dl=%s/%s srch=%s/%s itv=%s refill=%s/%s" % (
        mark, s['name'], c.get('enabled'), c.get('dry_run'), c.get('disk_gb'),
        st['used_gb'], st['torrents'], st['candidates'], st['dl_used'], st['dl_cap'],
        st['search_used'], st['search_cap'], c.get('search_interval_seconds'),
        c.get('refill_when_empty'), c.get('refill_min_seconds')))
PY

echo
echo "=== 3. 去重集合大小 ==="
$MYSQL -e "
select 'added_7d', count(distinct tid) from ptsf.added_history where service='$SVC' and ts > unix_timestamp()-7*86400;
select 'removed_24h', count(distinct tid) from ptsf.removal_history where service='$SVC' and ts > unix_timestamp()-86400;
" 2>/dev/null

echo
echo "=== 4. 候选池构成（解析缓存）==="
$MYSQL -e "select payload from ptsf.candidate_cache where service='$SVC';" 2>/dev/null > /tmp/ptsf_cand.json
$MYSQL -e "
select tid from ptsf.added_history where service='$SVC' and ts > unix_timestamp()-7*86400
union
select tid from ptsf.removal_history where service='$SVC' and ts > unix_timestamp()-86400;
" 2>/dev/null > /tmp/ptsf_excl.txt

python3 - <<'PY'
import json, collections
try:
    cands = json.load(open('/tmp/ptsf_cand.json'))
except Exception as exc:
    print("候选缓存不可解析:", exc)
    raise SystemExit
cands = [c for c in cands if isinstance(c, dict)]
excl = {l.strip() for l in open('/tmp/ptsf_excl.txt') if l.strip()}
print("候选总数      :", len(cands))
print("去重集合大小  :", len(excl))
print("促销分布      :", dict(collections.Counter(c.get('discount') for c in cands)))
free = [c for c in cands if c.get('discount') in ('FREE', '_2X_FREE')]
print("免费类        :", len(free))
sized = [c for c in free if 5 * 1024**3 <= int(c.get('size') or 0) <= 100 * 1024**3]
print("免费且体积合格:", len(sized))
left = [c for c in sized if str(c.get('tid')) not in excl]
print("再排除已加/已删:", len(left))
if left:
    for c in left[:5]:
        print("   可加:", c.get('name', '')[:40], "| %0.2fGB" % (int(c.get('size') or 0) / 1024**3))
PY

echo
echo "=== 5. 最近动作（$SVC）==="
curl -s "$API/api/status" | python3 -c "
import json, sys
for s in json.load(sys.stdin)['services']:
    if s['name'] == '$SVC':
        for a in s['actions'][:10]:
            print(a['time'], a['kind'].ljust(8), a['name'][:26], '|', a['detail'][:50])
"
