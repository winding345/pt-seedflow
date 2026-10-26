"""MySQL 存储层。

与 app/store.py（SQLite 版）保持同一套方法签名，调度与 Web 层无需关心后端。
生产环境用 MySQL，单元测试继续用 SQLite（无需外部服务）。
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pymysql

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS quota (
        service VARCHAR(64) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        day VARCHAR(16) NOT NULL,
        count BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (service, kind, day)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS upload_samples (
        service VARCHAR(64) NOT NULL,
        hash VARCHAR(64) NOT NULL,
        ts BIGINT NOT NULL,
        uploaded BIGINT NOT NULL,
        PRIMARY KEY (service, hash, ts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS torrent_archive (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        service VARCHAR(64) NOT NULL,
        hash VARCHAR(64),
        name TEXT,
        size BIGINT,
        downloaded BIGINT,
        uploaded BIGINT,
        progress DOUBLE,
        discount VARCHAR(32),
        added_on BIGINT,
        removed_at BIGINT NOT NULL,
        reason TEXT,
        discount_end BIGINT NULL,
        published_at BIGINT NULL,
        KEY idx_archive (service, removed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS counters (
        service VARCHAR(64) NOT NULL,
        k VARCHAR(32) NOT NULL,
        v BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (service, k)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS torrent_meta (
        service VARCHAR(64) NOT NULL,
        hash VARCHAR(64) NOT NULL,
        tid VARCHAR(64),
        discount VARCHAR(32),
        discount_end BIGINT NULL,
        published_at BIGINT NULL,
        added_at BIGINT,
        PRIMARY KEY (service, hash)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS service_state (
        service VARCHAR(64) NOT NULL,
        k VARCHAR(64) NOT NULL,
        v BIGINT NOT NULL,
        PRIMARY KEY (service, k)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS added_history (
        service VARCHAR(64) NOT NULL,
        tid VARCHAR(64) NOT NULL,
        name TEXT,
        ts BIGINT NOT NULL,
        KEY idx_added (service, tid, ts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS removal_history (
        service VARCHAR(64) NOT NULL,
        tid VARCHAR(64) NOT NULL,
        name TEXT,
        ts BIGINT NOT NULL,
        KEY idx_removal (service, tid, ts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS candidate_cache (
        service VARCHAR(64) PRIMARY KEY,
        payload LONGTEXT NOT NULL,
        saved_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS traffic (
        service VARCHAR(64) NOT NULL,
        ts BIGINT NOT NULL,
        downloaded BIGINT NOT NULL DEFAULT 0,
        uploaded BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (service, ts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS services (
        name VARCHAR(64) PRIMARY KEY,
        config LONGTEXT NOT NULL,
        updated_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS sites (
        name VARCHAR(64) PRIMARY KEY,
        config LONGTEXT NOT NULL,
        updated_at BIGINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

    """CREATE TABLE IF NOT EXISTS meta (
        k VARCHAR(64) PRIMARY KEY,
        v LONGTEXT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


class Database:
    """连接管理与建表。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 3306,
                 user: str = "root", password: str = "", database: str = "ptsf"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self._lock = threading.Lock()
        self._conn = pymysql.connect(
            host=host, port=port, user=user, password=password,
            database=database, charset="utf8mb4", autocommit=True,
            cursorclass=pymysql.cursors.Cursor,
        )

    @classmethod
    def from_env(cls) -> "Database":
        return cls(
            host=os.environ.get("PT_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("PT_DB_PORT", "3366")),
            user=os.environ.get("PT_DB_USER", "root"),
            password=os.environ.get("PT_DB_PASSWORD", ""),
            database=os.environ.get("PT_DB_NAME", "ptsf"),
        )

    def ensure_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            try:
                for statement in SCHEMA:
                    cur.execute(statement)
            finally:
                cur.close()

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def query(self, sql: str, args: Optional[tuple] = None) -> List[tuple]:
        with self.cursor() as cur:
            cur.execute(sql, args or ())
            return list(cur.fetchall())

    def execute(self, sql: str, args: Optional[tuple] = None) -> int:
        with self.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class MySQLStore:
    """与 SQLite Store 同接口的 MySQL 实现。"""

    def __init__(self, db: Database):
        self.db = db

    # ---------- 配额 ----------
    def quota_used(self, service: str, kind: str, day: str) -> int:
        rows = self.db.query(
            "SELECT count FROM quota WHERE service=%s AND kind=%s AND day=%s",
            (service, kind, day))
        return int(rows[0][0]) if rows else 0

    def quota_add(self, service: str, kind: str, day: str, amount: int = 1) -> int:
        self.db.execute(
            "INSERT INTO quota(service, kind, day, count) VALUES (%s,%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE count = count + %s",
            (service, kind, day, amount, amount))
        return self.quota_used(service, kind, day)

    # ---------- 上传量采样 ----------
    def add_sample(self, service: str, torrent_hash: str, uploaded: int,
                   ts: Optional[int] = None) -> None:
        stamp = int(ts if ts is not None else time.time())
        self.db.execute(
            "INSERT INTO upload_samples(service, hash, ts, uploaded)"
            " VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE uploaded=VALUES(uploaded)",
            (service, torrent_hash, stamp, int(uploaded)))

    def uploaded_since(self, service: str, torrent_hash: str, since_ts: int) -> int:
        value = self.upload_window(service, torrent_hash, since_ts)
        return 0 if value is None else value

    def upload_window(self, service: str, torrent_hash: str, since_ts: int) -> Optional[int]:
        since_ts = int(since_ts)
        rows = self.db.query(
            "SELECT uploaded FROM upload_samples WHERE service=%s AND hash=%s"
            " AND ts <= %s ORDER BY ts DESC LIMIT 1",
            (service, torrent_hash, since_ts))
        baseline = int(rows[0][0]) if rows else None

        rows = self.db.query(
            "SELECT COUNT(*), MIN(uploaded), MAX(uploaded) FROM upload_samples"
            " WHERE service=%s AND hash=%s AND ts > %s",
            (service, torrent_hash, since_ts))
        count, low, high = rows[0] if rows else (0, 0, 0)
        count = int(count or 0)
        if count == 0:
            return None
        if baseline is None:
            if count < 2:
                return None
            baseline = int(low)

        latest = self.db.query(
            "SELECT uploaded FROM upload_samples WHERE service=%s AND hash=%s"
            " ORDER BY ts DESC LIMIT 1", (service, torrent_hash))
        if not latest:
            return None
        return max(0, int(latest[0][0]) - baseline)

    def sample_count(self, service: str, torrent_hash: str, since_ts: int) -> int:
        rows = self.db.query(
            "SELECT COUNT(*) FROM upload_samples WHERE service=%s AND hash=%s AND ts >= %s",
            (service, torrent_hash, int(since_ts)))
        return int(rows[0][0]) if rows else 0

    def prune_samples(self, before_ts: int) -> None:
        self.db.execute("DELETE FROM upload_samples WHERE ts < %s", (int(before_ts),))

    # ---------- 删除历史 ----------
    def mark_removed(self, service: str, tid: str, name: str, ts: Optional[int] = None) -> None:
        self.db.execute(
            "INSERT INTO removal_history(service, tid, name, ts) VALUES (%s,%s,%s,%s)",
            (service, str(tid), name, int(ts if ts is not None else time.time())))

    def removed_tids_since(self, service: str, since_ts: int) -> List[str]:
        rows = self.db.query(
            "SELECT DISTINCT tid FROM removal_history WHERE service=%s AND ts >= %s",
            (service, int(since_ts)))
        return [str(r[0]) for r in rows]

    # ---------- 加入历史 ----------
    def mark_added(self, service: str, tid: str, name: str, ts: Optional[int] = None) -> None:
        self.db.execute(
            "INSERT INTO added_history(service, tid, name, ts) VALUES (%s,%s,%s,%s)",
            (service, str(tid), name, int(ts if ts is not None else time.time())))

    def added_tids_since(self, service: str, since_ts: int) -> List[str]:
        rows = self.db.query(
            "SELECT DISTINCT tid FROM added_history WHERE service=%s AND ts >= %s",
            (service, int(since_ts)))
        return [str(r[0]) for r in rows]

    # ---------- 归档 ----------
    def archive_torrent(self, service: str, hash_: str, name: str, size: int,
                        downloaded: int, uploaded: int, progress: float,
                        discount: str = "", added_on: Optional[int] = None,
                        reason: str = "", removed_at: Optional[int] = None,
                        discount_end: Optional[int] = None,
                        published_at: Optional[int] = None) -> None:
        self.db.execute(
            "INSERT INTO torrent_archive(service, hash, name, size, downloaded, uploaded,"
            " progress, discount, added_on, removed_at, reason, discount_end, published_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (service, hash_, name, int(size), int(downloaded), int(uploaded),
             float(progress), discount, int(added_on or 0),
             int(removed_at if removed_at is not None else time.time()), reason,
             int(discount_end) if discount_end else None,
             int(published_at) if published_at else None))

    def archived_torrents(self, service: str, limit: int = 200) -> List[dict]:
        rows = self.db.query(
            "SELECT name, size, downloaded, uploaded, progress, discount,"
            " added_on, removed_at, reason, discount_end, published_at"
            " FROM torrent_archive WHERE service=%s ORDER BY removed_at DESC LIMIT %s",
            (service, int(limit)))
        keys = ("name", "size", "downloaded", "uploaded", "progress", "discount",
                "added_on", "removed_at", "reason", "discount_end", "published_at")
        return [dict(zip(keys, row)) for row in rows]

    def archive_totals(self, service: str) -> tuple:
        rows = self.db.query(
            "SELECT COALESCE(SUM(downloaded),0), COALESCE(SUM(uploaded),0)"
            " FROM torrent_archive WHERE service=%s", (service,))
        return int(rows[0][0]), int(rows[0][1])

    # ---------- 计数 ----------
    def counter_add(self, service: str, key: str, amount: int = 1) -> int:
        self.db.execute(
            "INSERT INTO counters(service, k, v) VALUES (%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE v = v + %s",
            (service, key, amount, amount))
        return self.counter_get(service, key)

    def counter_get(self, service: str, key: str) -> int:
        rows = self.db.query("SELECT v FROM counters WHERE service=%s AND k=%s",
                             (service, key))
        return int(rows[0][0]) if rows else 0

    # ---------- 种子元数据（促销截止、发布时间） ----------
    def set_meta(self, service: str, hash_: str, tid: str, discount: str,
                 discount_end: Optional[int], added_at: Optional[int] = None,
                 published_at: Optional[int] = None) -> None:
        self.db.execute(
            "INSERT INTO torrent_meta(service, hash, tid, discount, discount_end,"
            " published_at, added_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE tid=VALUES(tid), discount=VALUES(discount),"
            " discount_end=VALUES(discount_end), published_at=VALUES(published_at)",
            (service, hash_, str(tid), discount,
             int(discount_end) if discount_end else None,
             int(published_at) if published_at else None,
             int(added_at if added_at is not None else time.time())))

    def meta_map(self, service: str) -> dict:
        rows = self.db.query(
            "SELECT hash, tid, discount, discount_end, added_at, published_at"
            " FROM torrent_meta WHERE service=%s", (service,))
        return {row[0]: {"tid": row[1], "discount": row[2], "discount_end": row[3],
                         "added_at": row[4], "published_at": row[5]} for row in rows}

    # ---------- 键值 ----------
    def kv_set(self, service: str, key: str, value: int) -> None:
        self.db.execute(
            "INSERT INTO service_state(service, k, v) VALUES (%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE v=VALUES(v)",
            (service, key, int(value)))

    def kv_get(self, service: str, key: str, default: int = 0) -> int:
        rows = self.db.query("SELECT v FROM service_state WHERE service=%s AND k=%s",
                             (service, key))
        return int(rows[0][0]) if rows else default

    # ---------- 候选池缓存 ----------
    def save_candidates(self, service: str, payload: str, saved_at: Optional[int] = None) -> None:
        self.db.execute(
            "INSERT INTO candidate_cache(service, payload, saved_at) VALUES (%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE payload=VALUES(payload), saved_at=VALUES(saved_at)",
            (service, payload, int(saved_at if saved_at is not None else time.time())))

    def load_candidates(self, service: str) -> tuple:
        rows = self.db.query(
            "SELECT payload, saved_at FROM candidate_cache WHERE service=%s", (service,))
        if not rows:
            return None, 0
        return rows[0][0], int(rows[0][1] or 0)

    # ---------- 流量 ----------
    def add_traffic(self, service: str, ts: int, downloaded: int = 0,
                    uploaded: int = 0) -> None:
        if downloaded <= 0 and uploaded <= 0:
            return
        self.db.execute(
            "INSERT INTO traffic(service, ts, downloaded, uploaded) VALUES (%s,%s,%s,%s)"
            " ON DUPLICATE KEY UPDATE downloaded = downloaded + VALUES(downloaded),"
            " uploaded = uploaded + VALUES(uploaded)",
            (service, int(ts), int(downloaded), int(uploaded)))

    def traffic_total(self, service: str) -> tuple:
        rows = self.db.query(
            "SELECT COALESCE(SUM(downloaded),0), COALESCE(SUM(uploaded),0)"
            " FROM traffic WHERE service=%s", (service,))
        return int(rows[0][0]), int(rows[0][1])

    def traffic_rows(self, service: str, since_ts: int) -> List[Tuple[int, int, int]]:
        rows = self.db.query(
            "SELECT ts, downloaded, uploaded FROM traffic"
            " WHERE service=%s AND ts >= %s ORDER BY ts ASC",
            (service, int(since_ts)))
        return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]

    def prune_traffic(self, before_ts: int) -> None:
        self.db.execute("DELETE FROM traffic WHERE ts < %s", (int(before_ts),))

    def close(self) -> None:
        pass
