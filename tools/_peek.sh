#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
PW=$(grep '^MYSQL_PASSWORD=' .env | cut -d= -f2)
docker exec pt-seedflow-mysql mysql -uptsf -p"$PW" -N -e \
  "select name, json_extract(config,'$.enabled') from ptsf.services" 2>/dev/null
echo "--- sites ---"
docker exec pt-seedflow-mysql mysql -uptsf -p"$PW" -N -e \
  "select name from ptsf.sites" 2>/dev/null
