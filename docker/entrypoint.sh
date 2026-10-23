#!/usr/bin/env bash
# 容器入口：先拉起内置 MySQL，再启动应用。
set -euo pipefail

DB_PORT="${PT_DB_PORT:-3366}"
DB_NAME="${PT_DB_NAME:-ptsf}"
APP_USER="${PT_DB_USER:-ptsf}"
APP_PW="${PT_DB_PASSWORD:-ptsf_change_me}"
ROOT_PW="${MYSQL_ROOT_PASSWORD:-ptsf_root_change_me}"
SOCKET=/var/run/mysqld/mysqld.sock
DATADIR=/var/lib/mysql

mkdir -p /var/run/mysqld
chown -R mysql:mysql /var/run/mysqld "$DATADIR"

fresh=0
if [ ! -d "$DATADIR/mysql" ]; then
  echo "[mysql] 首次启动，初始化数据目录"
  mysqld --initialize-insecure --user=mysql --datadir="$DATADIR"
  fresh=1
fi

echo "[mysql] 启动，端口 $DB_PORT"
mysqld --user=mysql --datadir="$DATADIR" \
       --port="$DB_PORT" --bind-address=0.0.0.0 \
       --socket="$SOCKET" --skip-name-resolve \
       --secure-file-priv=/var/lib/mysql-files \
       --mysqlx=0 &
MYSQL_PID=$!

ready=0
for _ in $(seq 1 60); do
  if mysqladmin --socket="$SOCKET" ping >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != "1" ]; then
  echo "[mysql] 启动超时" >&2
  exit 1
fi

if [ "$fresh" = "1" ]; then
  echo "[mysql] 初始化账号与数据库"
  mysql --socket="$SOCKET" -uroot <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY '${ROOT_PW}';
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${APP_USER}'@'%' IDENTIFIED BY '${APP_PW}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${APP_USER}'@'%';
FLUSH PRIVILEGES;
SQL
fi

# 应用退出时一并结束 mysqld，避免容器内残留进程
trap 'kill -TERM "$MYSQL_PID" 2>/dev/null || true' EXIT

exec "$@"
