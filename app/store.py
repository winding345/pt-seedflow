"""状态持久化：配额计数、上传量采样、删除历史。

只用标准库 sqlite3，避免额外依赖。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS quota (
    service TEXT NOT NULL,
    kind    TEXT NOT NULL,
    day     TEXT NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (service, kind, day)
);

CREATE TABLE IF NOT EXISTS upload_samples (
    service  TEXT NOT NULL,
    hash     TEXT NOT NULL,
    ts       INTEGER NOT NULL,
    uploaded INTEGER NOT NULL,
    PRIMARY KEY (service, hash, ts)
);

CREATE TABLE IF NOT EXISTS removal_history (
    service TEXT NOT NULL,
    tid     TEXT NOT NULL,
    name    TEXT,
    ts      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS added_history (
    service TEXT NOT NULL,
    tid     TEXT NOT NULL,
    name    TEXT,
    ts      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS torrent_archive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    service     TEXT NOT NULL,
    hash        TEXT,
    name        TEXT,
    size        INTEGER,
    downloaded  INTEGER,
    uploaded    INTEGER,
    progress    REAL,
    discount    TEXT,
    added_on    INTEGER,
    removed_at  INTEGER NOT NULL,
    reason      TEXT,
    published_at INTEGER
);

CREATE TABLE IF NOT EXISTS torrent_meta (
    service      TEXT NOT NULL,
    hash         TEXT NOT NULL,
    tid          TEXT,
    discount     TEXT,
    discount_end INTEGER,
    published_at INTEGER,
    added_at     INTEGER,
    PRIMARY KEY (service, hash)
);

CREATE TABLE IF NOT EXISTS service_state (
    service TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   INTEGER NOT NULL,
    PRIMARY KEY (service, key)
);

CREATE TABLE IF NOT EXISTS counters (
    service TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (service, key)
);

CREATE INDEX IF NOT EXISTS idx_archive_lookup ON torrent_archive(service, removed_at);
CREATE INDEX IF NOT EXISTS idx_added_lookup ON added_history(service, tid, ts);
CREATE INDEX IF NOT EXISTS idx_samples_lookup ON upload_samples(service, hash, ts);
CREATE INDEX IF NOT EXISTS idx_removal_lookup ON removal_history(service, tid, ts);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """给旧库补列，保持向后兼容。"""
        cur = self._conn.cursor()
        cur.execute("PRAGMA table_info(torrent_archive)")
        columns = {row[1] for row in cur.fetchall()}
        if "discount_end" not in columns:
            cur.execute("ALTER TABLE torrent_archive ADD COLUMN discount_end INTEGER")
        if "published_at" not in columns:
            cur.execute("ALTER TABLE torrent_archive ADD COLUMN published_at INTEGER")

        cur.execute("PRAGMA table_info(torrent_meta)")
        meta_columns = {row[1] for row in cur.fetchall()}
        if meta_columns and "published_at" not in meta_columns:
            cur.execute("ALTER TABLE torrent_meta ADD COLUMN published_at INTEGER")
        cur.close()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            finally:
                cur.close()

    # ---------- 配额 ----------
    def quota_used(self, service: str, kind: str, day: str) -> int:
        with self._cursor() as cur:
            cur.execute(
                "SELECT count FROM quota WHERE service=? AND kind=? AND day=?",
                (service, kind, day),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def quota_add(self, service: str, kind: str, day: str, amount: int = 1) -> int:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO quota(service, kind, day, count) VALUES (?,?,?,?) "
                "ON CONFLICT(service, kind, day) DO UPDATE SET count = count + ?",
                (service, kind, day, amount, amount),
            )
            cur.execute(
                "SELECT count FROM quota WHERE service=? AND kind=? AND day=?",
                (service, kind, day),
            )
            row = cur.fetchone()
            return int(row[0]) if row else amount

    # ---------- 上传量 ----------
    def add_sample(self, service: str, torrent_hash: str, uploaded: int, ts: Optional[int] = None) -> None:
        stamp = int(ts if ts is not None else time.time())
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO upload_samples(service, hash, ts, uploaded) VALUES (?,?,?,?)",
                (service, torrent_hash, stamp, int(uploaded)),
            )

    def upload_window(self, service: str, torrent_hash: str, since_ts: int) -> Optional[int]:
        """计算窗口内的上传量；样本不足时返回 None。

        用累计值差分，起点取"窗口开始前最近一条样本"，这样即使启动时间
        落在窗口中间，也能正确反映整段窗口的真实增量。
        """
        since_ts = int(since_ts)
        with self._cursor() as cur:
            cur.execute(
                "SELECT uploaded FROM upload_samples WHERE service=? AND hash=? "
                "AND ts <= ? ORDER BY ts DESC LIMIT 1",
                (service, torrent_hash, since_ts),
            )
            row = cur.fetchone()
            baseline = int(row[0]) if row else None

            cur.execute(
                "SELECT COUNT(*), MIN(uploaded), MAX(uploaded) FROM upload_samples "
                "WHERE service=? AND hash=? AND ts > ?",
                (service, torrent_hash, since_ts),
            )
            count, low, high = cur.fetchone()
            count = int(count or 0)
            if count == 0:
                return None
            if baseline is None:
                # 没有窗口前的锚点，至少要有两条窗口内样本才能算差分
                if count < 2:
                    return None
                baseline = int(low)

            cur.execute(
                "SELECT uploaded FROM upload_samples WHERE service=? AND hash=? "
                "ORDER BY ts DESC LIMIT 1",
                (service, torrent_hash),
            )
            latest_row = cur.fetchone()

        if latest_row is None:
            return None
        return max(0, int(latest_row[0]) - baseline)

    def uploaded_since(self, service: str, torrent_hash: str, since_ts: int) -> int:
        """兼容旧调用：无法测量时返回 0。"""
        value = self.upload_window(service, torrent_hash, since_ts)
        return 0 if value is None else value

    def sample_count(self, service: str, torrent_hash: str, since_ts: int) -> int:
        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM upload_samples WHERE service=? AND hash=? AND ts >= ?",
                (service, torrent_hash, int(since_ts)),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def prune_samples(self, before_ts: int) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM upload_samples WHERE ts < ?", (int(before_ts),))

    # ---------- 删除历史（避免短时间重复加同一个种） ----------
    def mark_removed(self, service: str, tid: str, name: str, ts: Optional[int] = None) -> None:
        stamp = int(ts if ts is not None else time.time())
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO removal_history(service, tid, name, ts) VALUES (?,?,?,?)",
                (service, str(tid), name, stamp),
            )

    def removed_tids_since(self, service: str, since_ts: int) -> List[str]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tid FROM removal_history WHERE service=? AND ts >= ?",
                (service, int(since_ts)),
            )
            return [str(row[0]) for row in cur.fetchall()]

    # ---------- 加入历史（按 tid 判重，避免重复消耗配额） ----------
    def mark_added(self, service: str, tid: str, name: str, ts: Optional[int] = None) -> None:
        stamp = int(ts if ts is not None else time.time())
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO added_history(service, tid, name, ts) VALUES (?,?,?,?)",
                (service, str(tid), name, stamp),
            )

    def added_tids_since(self, service: str, since_ts: int) -> List[str]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tid FROM added_history WHERE service=? AND ts >= ?",
                (service, int(since_ts)),
            )
            return [str(row[0]) for row in cur.fetchall()]

    # ---------- 已删除任务归档 ----------
    def archive_torrent(self, service: str, hash_: str, name: str, size: int,
                        downloaded: int, uploaded: int, progress: float,
                        discount: str = "", added_on: Optional[int] = None,
                        reason: str = "", removed_at: Optional[int] = None,
                        discount_end: Optional[int] = None,
                        published_at: Optional[int] = None) -> None:
        stamp = int(removed_at if removed_at is not None else time.time())
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO torrent_archive(service, hash, name, size, downloaded, uploaded,"
                " progress, discount, added_on, removed_at, reason, discount_end,"
                " published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (service, hash_, name, int(size), int(downloaded), int(uploaded),
                 float(progress), discount, int(added_on or 0), stamp, reason,
                 int(discount_end) if discount_end else None,
                 int(published_at) if published_at else None),
            )

    def archived_torrents(self, service: str, limit: int = 200) -> List[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT name, size, downloaded, uploaded, progress, discount,"
                " added_on, removed_at, reason, discount_end, published_at FROM torrent_archive"
                " WHERE service=? ORDER BY removed_at DESC LIMIT ?",
                (service, int(limit)),
            )
            rows = cur.fetchall()
        keys = ("name", "size", "downloaded", "uploaded", "progress",
                "discount", "added_on", "removed_at", "reason", "discount_end",
                "published_at")
        return [dict(zip(keys, row)) for row in rows]

    # ---------- 种子元数据（促销截止等） ----------
    def set_meta(self, service: str, hash_: str, tid: str, discount: str,
                 discount_end: Optional[int], added_at: Optional[int] = None,
                 published_at: Optional[int] = None) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO torrent_meta(service, hash, tid, discount, discount_end,"
                " published_at, added_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(service, hash) DO UPDATE SET"
                " tid=excluded.tid, discount=excluded.discount,"
                " discount_end=excluded.discount_end,"
                " published_at=excluded.published_at",
                (service, hash_, str(tid), discount,
                 int(discount_end) if discount_end else None,
                 int(published_at) if published_at else None,
                 int(added_at or time.time())),
            )

    def meta_map(self, service: str) -> dict:
        with self._cursor() as cur:
            cur.execute(
                "SELECT hash, tid, discount, discount_end, added_at, published_at"
                " FROM torrent_meta"
                " WHERE service=?", (service,))
            rows = cur.fetchall()
        return {row[0]: {"tid": row[1], "discount": row[2],
                         "discount_end": row[3], "added_at": row[4],
                         "published_at": row[5]} for row in rows}

    # ---------- 服务级键值（如上次 search 时间） ----------
    def kv_set(self, service: str, key: str, value: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO service_state(service, key, value) VALUES (?,?,?)"
                " ON CONFLICT(service, key) DO UPDATE SET value=excluded.value",
                (service, key, int(value)),
            )

    def kv_get(self, service: str, key: str, default: int = 0) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT value FROM service_state WHERE service=? AND key=?",
                        (service, key))
            row = cur.fetchone()
        return int(row[0]) if row else default

    # ---------- 持久化计数 ----------
    def counter_add(self, service: str, key: str, amount: int = 1) -> int:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO counters(service, key, value) VALUES (?,?,?)"
                " ON CONFLICT(service, key) DO UPDATE SET value = value + ?",
                (service, key, amount, amount),
            )
            cur.execute("SELECT value FROM counters WHERE service=? AND key=?", (service, key))
            row = cur.fetchone()
            return int(row[0]) if row else amount

    def counter_get(self, service: str, key: str) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT value FROM counters WHERE service=? AND key=?", (service, key))
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
