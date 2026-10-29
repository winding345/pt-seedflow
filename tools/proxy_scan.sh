#!/usr/bin/env bash
# 探测 NAS 上的代理组件与 m-team 连通性（只读，不改配置）
set -uo pipefail

echo "=== 1. 代理相关容器 ==="
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null \
  | grep -iE 'clash|v2ray|xray|sing|meta|proxy|trojan|shadowsocks|ss-|mihomo' \
  || echo "(没有匹配的代理容器)"

echo
echo "=== 2. 全部容器（前 30）==="
docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | head -30

echo
echo "=== 3. 常见代理端口监听情况 ==="
ss -ltn 2>/dev/null \
  | grep -E ':(7890|7891|7892|7893|1080|1081|1087|2080|2017|8888|10808|10809|9090|10810)\b' \
  || echo "(常见代理端口未监听)"

echo
echo "=== 4. 当前 shell 的代理变量 ==="
env | grep -i proxy || echo "(无)"

echo
echo "=== 5. m-team 直连测试 ==="
echo "-- DNS(v4) --"
getent ahostsv4 api.m-team.cc 2>/dev/null | head -2
echo "-- 直连 --"
curl -4 -sS -o /dev/null -m 8 -w 'direct=%{http_code}\n' https://api.m-team.cc/ 2>&1 | head -2

echo
echo "=== 6. 若有端口，逐个试走代理 ==="
for port in 7890 7891 1080 10809 20171; do
  if ss -ltn 2>/dev/null | grep -q ":${port} "; then
    code=$(curl -sS -o /dev/null -m 10 -x "http://127.0.0.1:${port}" \
           -w '%{http_code}' https://api.m-team.cc/ 2>/dev/null || echo "fail")
    echo "127.0.0.1:${port} -> ${code}"
  fi
done

echo
echo "=== 6.5 DoH 解析对比（加密 DNS，不受投毒影响）==="
for resolver in "https://223.5.5.5/resolve?name=api.m-team.cc&type=A" \
                "https://1.1.1.1/dns-query?name=api.m-team.cc&type=A"; do
  echo "-- $resolver --"
  timeout 10 curl -sS -H 'accept: application/dns-json' "$resolver" 2>/dev/null \
    | tr ',' '\n' | grep -E '"data"' | head -3 || echo "(失败)"
done

echo
echo "=== 6.6 拿到干净 IP 后直连测试 ==="
for ip in 104.26.10.251 172.67.68.150; do
  code=$(timeout 8 curl -sS -o /dev/null -m 8 --resolve "api.m-team.cc:443:$ip" \
         -w '%{http_code}' https://api.m-team.cc/ 2>/dev/null || echo fail)
  echo "$ip -> $code"
done

echo
echo "=== 7. 订阅/配置目录线索 ==="
for d in /etc/v2raya /etc/clash /etc/mihomo /etc/sing-box "$HOME/.config/clash" "$HOME/.config/mihomo"; do
  [ -d "$d" ] && echo "存在: $d" && ls "$d" 2>/dev/null | head -5
done
echo "(以上为空表示没找到常见配置目录)"
