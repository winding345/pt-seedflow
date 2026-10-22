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
        self._conn.commit()

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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
