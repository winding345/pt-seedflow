# 单容器：MySQL + 应用（MySQL 与应用同容器同生命周期）
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 阻止安装期尝试用 systemd 启动 mysql（容器里没有 systemd）
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv \
        mysql-server-core-8.0 mysql-client-8.0 \
        tzdata ca-certificates \
    && rm -f /usr/sbin/policy-rc.d \
    && rm -rf /var/lib/apt/lists/*

# 复用 uv 安装依赖，保持与本地一致的锁文件
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY config ./config
COPY static ./static
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && groupadd -r mysql 2>/dev/null || true \
    && (id -u mysql >/dev/null 2>&1 || useradd -r -g mysql -s /usr/sbin/nologin mysql) \
    && mkdir -p /var/lib/mysql /var/lib/mysql-files /var/run/mysqld /var/log/mysql \
    && chown -R mysql:mysql /var/lib/mysql /var/lib/mysql-files /var/run/mysqld /var/log/mysql

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8600 3366

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "app.main"]
