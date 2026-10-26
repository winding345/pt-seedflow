#!/usr/bin/env python3
"""把旧版 SQLite（state.db）里的历史数据一次性导入 MySQL。

用法（在容器里跑，最省事）：
    docker compose run --rm app python -m tools.migrate_sqlite \
        --source /root/.pt-seedflow/state.db

常用参数：
    --source PATH    SQLite 文件路径（默认 /root/.pt-seedflow/state.db）
    --dry-run        只统计不写入
    --reset          导入前清空目标表（慎用，默认是合并写入）

设计：
- 幂等：重复执行不会产生重复行（都是 INSERT ... ON DUPLICATE KEY / INSERT IGNORE）
- 只迁移有长期价值的数据：归档、流量、配额、新增/删除记录、种子元数据、计数器
- 不迁移：upload_samples（只服务删除判定窗口）、candidate_cache（临时缓存）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter

from app.db import Database

TABLES = ("quota", "torrent_archive", "torrent_meta", "traffic",
          "added_history", "removal_history", "service_state", "counters")


def connect_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def migrate_quota(src, db, dry):
    rows = src.execute("SELECT service, kind, day, count FROM quota").fetchall()
    for r in rows:
        if not dry:
            db.execute(
                "INSERT INTO quota(service, kind, day, count) VALUES (%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE count=GREATEST(count, VALUES(count))",
                (r["service"], r["kind"], r["day"], int(r["count"])))
    return len(rows)


def migrate_traffic(src, db, dry):
    rows = src.execute("SELECT service, ts, downloaded, uploaded FROM traffic").fetchall()
    for r in rows:
        if not dry:
            db.execute(
                "INSERT INTO traffic(service, ts, downloaded, uploaded) VALUES (%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE downloaded=VALUES(downloaded),"
                " uploaded=VALUES(uploaded)",
                (r["service"], int(r["ts"]), int(r["downloaded"]), int(r["uploaded"])))
    return len(rows)


def migrate_archive(src, db, dry):
    wanted = ("service", "hash", "name", "size", "downloaded", "uploaded", "progress",
              "discount", "added_on", "removed_at", "reason", "discount_end", "published_at")
    # 老库可能没有后加的列，按实际存在的列取
    have = {row[1] for row in src.execute("PRAGMA table_info(torrent_archive)")}
    cols = [c for c in wanted if c in have]
    raw = src.execute("SELECT %s FROM torrent_archive" % ",".join(cols)).fetchall()
    rows = [dict(zip(cols, tuple(r))) for r in raw]
    # 用 (service, hash, removed_at) 去重，避免重复导入
    existing = {(r[0], r[1], int(r[2] or 0)) for r in db.query(
        "SELECT service, hash, removed_at FROM torrent_archive")}
    inserted = 0
    for r in rows:
        key = (r["service"], r["hash"], int(r.get("removed_at") or 0))
        if key in existing:
            continue
        if not dry:
            db.execute(
                "INSERT INTO torrent_archive(service, hash, name, size, downloaded, uploaded,"
                " progress, discount, added_on, removed_at, reason, discount_end, published_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (r["service"], r["hash"], r.get("name"), int(r.get("size") or 0),
                 int(r.get("downloaded") or 0), int(r.get("uploaded") or 0),
                 float(r.get("progress") or 0), r.get("discount") or "",
                 int(r.get("added_on") or 0), int(r.get("removed_at") or 0),
                 r.get("reason") or "", r.get("discount_end"), r.get("published_at")))
        inserted += 1
    return inserted


def migrate_meta(src, db, dry):
    rows = src.execute(
        "SELECT service, hash, tid, discount, discount_end, published_at, added_at"
        " FROM torrent_meta").fetchall()
    for r in rows:
        if not dry:
            db.execute(
                "INSERT INTO torrent_meta(service, hash, tid, discount, discount_end,"
                " published_at, added_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE tid=VALUES(tid), discount=VALUES(discount),"
                " discount_end=VALUES(discount_end), published_at=VALUES(published_at)",
                (r["service"], r["hash"], r["tid"], r["discount"],
                 r["discount_end"], r["published_at"], int(r["added_at"] or 0)))
    return len(rows)


def migrate_history(src, db, dry, table):
    rows = src.execute("SELECT service, tid, name, ts FROM %s" % table).fetchall()
    existing = {(r[0], r[1], int(r[2])) for r in db.query(
        "SELECT service, tid, ts FROM %s" % table)}
    inserted = 0
    for r in rows:
        key = (r["service"], r["tid"], int(r["ts"]))
        if key in existing:
            continue
        if not dry:
            db.execute(
                "INSERT INTO %s(service, tid, name, ts) VALUES (%%s,%%s,%%s,%%s)" % table,
                (r["service"], r["tid"], r["name"], int(r["ts"])))
        inserted += 1
    return inserted


def migrate_service_state(src, db, dry):
    rows = src.execute("SELECT service, key, value FROM service_state").fetchall()
    for r in rows:
        if not dry:
            db.execute(
                "INSERT INTO service_state(service, k, v) VALUES (%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE v=GREATEST(v, VALUES(v))",
                (r["service"], r["key"], int(r["value"])))
    return len(rows)


def migrate_counters(src, db, dry):
    rows = src.execute("SELECT service, key, value FROM counters").fetchall()
    for r in rows:
        if not dry:
            db.execute(
                "INSERT INTO counters(service, k, v) VALUES (%s,%s,%s)"
                " ON DUPLICATE KEY UPDATE v=GREATEST(v, VALUES(v))",
                (r["service"], r["key"], int(r["value"])))
    return len(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SQLite → MySQL 历史数据迁移")
    parser.add_argument("--source", default="/root/.pt-seedflow/state.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)

    try:
        src = connect_sqlite(args.source)
    except sqlite3.Error as exc:
        print("打开 SQLite 失败: %s" % exc, file=sys.stderr)
        return 1

    present = [t for t in TABLES if table_exists(src, t)]
    print("源库表: %s" % ", ".join(present) or "(空)")

    db = Database.from_env()
    db.ensure_schema()

    if args.reset and not args.dry_run:
        for table in ("quota", "torrent_archive", "torrent_meta", "traffic",
                      "added_history", "removal_history", "service_state", "counters"):
            db.execute("DELETE FROM %s" % table)
        print("已清空目标表")

    stats = Counter()
    if "quota" in present:
        stats["quota"] = migrate_quota(src, db, args.dry_run)
    if "traffic" in present:
        stats["traffic"] = migrate_traffic(src, db, args.dry_run)
    if "torrent_archive" in present:
        stats["torrent_archive"] = migrate_archive(src, db, args.dry_run)
    if "torrent_meta" in present:
        stats["torrent_meta"] = migrate_meta(src, db, args.dry_run)
    if "added_history" in present:
        stats["added_history"] = migrate_history(src, db, args.dry_run, "added_history")
    if "removal_history" in present:
        stats["removal_history"] = migrate_history(src, db, args.dry_run, "removal_history")
    if "service_state" in present:
        stats["service_state"] = migrate_service_state(src, db, args.dry_run)
    if "counters" in present:
        stats["counters"] = migrate_counters(src, db, args.dry_run)

    print("\n迁移结果%s:" % ("（演练，未写入）" if args.dry_run else ""))
    for table, count in stats.items():
        print("  %-18s %d 行" % (table, count))
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
