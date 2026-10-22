"""调度核心：每 tick 先删后补，每小时刷新候选池。"""
from __future__ import annotations

import datetime as dt
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from .config import AppConfig, ServiceConfig
from .lifecycle import AddPlan, Pick, decide_delete, plan_adds, verify_deletable
from .qb import QBError, QBittorrent
from .scoring import Candidate
from .store import Store


def today() -> str:
    return dt.date.today().isoformat()


@dataclass
class Action:
    ts: int
    kind: str  # add / delete / skip / error
    name: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "time": dt.datetime.fromtimestamp(self.ts).strftime("%H:%M:%S"),
            "kind": self.kind,
            "name": self.name,
            "detail": self.detail,
        }


@dataclass
class ServiceRunner:
    cfg: ServiceConfig
    site: object
    qb: QBittorrent
    store: Store
    tick_seconds: int
    refresh_every_ticks: int

    candidates: List[Candidate] = field(default_factory=list)
    actions: Deque[Action] = field(default_factory=lambda: deque(maxlen=60))
    last_search_at: float = 0.0
    last_tick_at: float = 0.0
    ticks_since_search: int = 99
    used_bytes: int = 0
    torrent_count: int = 0
    last_error: str = ""
    quota_exhausted: bool = False
    deleted_total: int = 0
    added_total: int = 0
    _progress: Dict[str, float] = field(default_factory=dict)
    # 每个任务的累计值快照，用于计算增量
    _prev: Dict[str, tuple] = field(default_factory=dict)
    # 服务启动以来的累计下载/上传字节数
    dl_since_start: int = 0
    ul_since_start: int = 0
    # 最近一次 qB 任务快照，供详情页使用（避免 Web 端反查 qB）
    torrents_cache: List[object] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---------- 工具 ----------
    def _log(self, kind: str, name: str, detail: str = "") -> None:
        self.actions.appendleft(Action(int(time.time()), kind, name, detail))

    def _need_refresh(self) -> bool:
        return self.ticks_since_search >= self.refresh_every_ticks

    def _account(self, torrents) -> None:
        """把每个任务的增量累加到"服务启动以来"的统计里。"""
        alive = set()
        for torrent in torrents:
            alive.add(torrent.hash)
            prev = self._prev.get(torrent.hash)
            if prev is not None:
                up_delta = torrent.uploaded - prev[0]
                dl_delta = torrent.downloaded - prev[1]
                if up_delta > 0:
                    self.ul_since_start += up_delta
                if dl_delta > 0:
                    self.dl_since_start += dl_delta
            self._prev[torrent.hash] = (torrent.uploaded, torrent.downloaded)
        # 已不存在的任务不再保留快照
        for dead in [h for h in self._prev if h not in alive]:
            self._prev.pop(dead, None)
        self.torrents_cache = list(torrents)

    # ---------- 主循环 ----------
    def tick(self) -> None:
        with self._lock:
            self.last_tick_at = time.time()
            self.ticks_since_search += 1
            self.last_error = ""
            try:
                torrents = self.qb.list_torrents(category=self.cfg.category)
                self.used_bytes = sum(t.size for t in torrents)
                self.torrent_count = len(torrents)
                self._account(torrents)
                self._step_delete(torrents)
                if self._need_refresh():
                    self._step_search()
                # 删除后重新取一次，保证空间与任务数是最新的
                torrents = self.qb.list_torrents(category=self.cfg.category)
                self.used_bytes = sum(t.size for t in torrents)
                self.torrent_count = len(torrents)
                if not self.quota_exhausted:
                    self._step_add(torrents)
            except QBError as exc:
                self.last_error = str(exc)
                self._log("error", "-", str(exc))
            except Exception as exc:  # noqa: BLE001  单 tick 失败不应终止服务
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                self._log("error", "-", self.last_error)

    def manual(self, action: str = "both") -> dict:
        """手动触发一次加/删，不触发 search。

        action: add | delete | both
        """
        if action not in ("add", "delete", "both"):
            raise ValueError("action 只能是 add / delete / both")

        with self._lock:
            self.last_error = ""
            done: List[str] = []
            cfg_limit = max(self.cfg.max_torrents, self.cfg.add_per_tick)
            try:
                torrents = self.qb.list_torrents(category=self.cfg.category)
                self.used_bytes = sum(t.size for t in torrents)
                self.torrent_count = len(torrents)
                self._account(torrents)

                if action in ("delete", "both"):
                    self._step_delete(torrents)
                    done.append("delete")
                    if action == "both":
                        torrents = self.qb.list_torrents(category=self.cfg.category)
                        self.used_bytes = sum(t.size for t in torrents)
                        self.torrent_count = len(torrents)

                if action in ("add", "both"):
                    if not self.candidates:
                        self._log("idle", "手动加种", "候选池为空，先等一次 search")
                    else:
                        # 手动触发时一次性填满：上限交给空间与并发约束
                        self._step_add(torrents, max_adds=cfg_limit)
                        done.append("add")
            except Exception as exc:  # noqa: BLE001
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                self._log("error", "-", self.last_error)

            return {"service": self.cfg.name, "action": action,
                    "done": done, "snapshot": self.snapshot()}

    def _reconcile_new_hash(self, known_hashes) -> Optional[str]:
        """加种后 qB 才分配 hash：对比前后列表，找出新增的那个。"""
        try:
            current = self.qb.list_torrents(category=self.cfg.category)
        except QBError:
            return None
        for torrent in current:
            if torrent.hash not in known_hashes:
                return torrent.hash
        return None

    # ---------- 删除 ----------
    def _step_delete(self, torrents) -> None:
        now = time.time()
        window = self.cfg.window_minutes * 60
        meta = self.store.meta_map(self.cfg.name)
        targets: List[tuple] = []
        reason_for: Dict[str, str] = {}

        for torrent in torrents:
            self.store.add_sample(self.cfg.name, torrent.hash, torrent.uploaded, int(now))
            window_bytes = self.store.upload_window(
                self.cfg.name, torrent.hash, int(now - window))

            previous = self._progress.get(torrent.hash)
            info = meta.get(torrent.hash) or {}
            remove, reason = decide_delete(
                torrent, self.cfg, now, window_bytes, previous,
                discount_end=info.get("discount_end"))
            self._progress[torrent.hash] = torrent.progress

            if not remove:
                continue
            if self.cfg.dry_run:
                self._log("would-delete", torrent.name, reason)
                continue
            targets.append((torrent.hash, torrent.name))
            reason_for[torrent.hash] = reason
            self._log("delete", torrent.name, reason)

        if targets and not self.cfg.dry_run:
            # 删前再校验一次：hash + 名字 + 分类三重匹配，且必须是本服务分类
            verified = verify_deletable(torrents, targets, self.cfg.category)
            if len(verified) != len(targets):
                self._log("error", "-", "校验后仅 %d/%d 个可删" % (len(verified), len(targets)))
            if not verified:
                return
            hashes = [item[0] for item in verified]
            by_hash = {t.hash: t for t in torrents}
            if self.qb.delete(hashes, delete_files=True):
                self.deleted_total += len(hashes)
                now_ts = int(time.time())
                for item_hash, item_name in verified:
                    snap = by_hash.get(item_hash)
                    self.store.archive_torrent(
                        service=self.cfg.name,
                        hash_=item_hash,
                        name=item_name,
                        size=snap.size if snap else 0,
                        downloaded=snap.downloaded if snap else 0,
                        uploaded=snap.uploaded if snap else 0,
                        progress=snap.progress if snap else 0.0,
                        added_on=snap.added_on if snap else None,
                        reason=reason_for.get(item_hash, ""),
                        discount=(meta.get(item_hash) or {}).get("discount", ""),
                        discount_end=(meta.get(item_hash) or {}).get("discount_end"),
                        removed_at=now_ts,
                    )
                    self._prev.pop(item_hash, None)
                self.store.counter_add(self.cfg.name, "removed", len(hashes))
                self._log("deleted", "%d 个任务" % len(hashes), "已连同文件删除")
            else:
                self._log("error", "-", "删除失败，共 %d 个" % len(hashes))

    # ---------- 搜索 ----------
    def _step_search(self) -> None:
        # 强制最小间隔：即使重启或 tick 计数被重置，也不会多刷
        last_ts = self.store.kv_get(self.cfg.name, "last_search_ts", 0)
        elapsed = time.time() - last_ts
        if last_ts and elapsed < self.cfg.search_interval_seconds:
            self._log("idle", "跳过 search",
                      "距上次仅 %d 秒（下限 %d 秒）" % (elapsed, self.cfg.search_interval_seconds))
            self.ticks_since_search = 0
            return

        cap = self.cfg.limits.search_cap
        used = self.store.quota_used(self.cfg.name, "search", today())
        if used >= cap:
            self._log("quota", "search", "每日额度已用尽 %d/%d" % (used, cap))
            return

        try:
            candidates = self.site.search(page=1, page_size=100)
        except Exception as exc:  # noqa: BLE001
            self._log("error", "search", str(exc))
            self.last_error = str(exc)
            return

        self.store.quota_add(self.cfg.name, "search", today())
        self.store.kv_set(self.cfg.name, "last_search_ts", int(time.time()))
        self.candidates = candidates
        self.last_search_at = time.time()
        self.ticks_since_search = 0
        self._log("search", "候选池", "%d 个候选" % len(candidates))

    # ---------- 加种 ----------
    def _step_add(self, torrents, max_adds: Optional[int] = None) -> None:
        if not self.candidates:
            return

        cap = self.cfg.limits.dl_cap
        used = self.store.quota_used(self.cfg.name, "dl", today())
        left = cap - used
        if left <= 0:
            self.quota_exhausted = True
            self._log("quota", "dl", "每日额度已用尽 %d/%d" % (used, cap))
            return

        current_names = {t.name for t in torrents}
        known_hashes = {t.hash for t in torrents}
        # 近期已加过的 tid 与已删过的 tid 都要排除，避免重复消耗下载配额
        added_before = set(self.store.added_tids_since(
            self.cfg.name, int(time.time()) - 7 * 86400))
        removed = set(self.store.removed_tids_since(self.cfg.name, int(time.time()) - 86400))
        exclude = added_before | removed

        plan: AddPlan = plan_adds(
            candidates=self.candidates,
            cfg=self.cfg,
            now=time.time(),
            used_bytes=self.used_bytes,
            current_count=self.torrent_count,
            dl_left=left,
            exclude_tids=exclude,
            max_adds=max_adds,
        )

        for pick in plan.picks:
            if pick.candidate.name in current_names:
                continue
            if self.cfg.dry_run:
                self._log("would-add", pick.candidate.name,
                          "%.2fGB score=%.3f" % (pick.candidate.size / 1024 ** 3, pick.score))
                self.used_bytes += pick.candidate.size
                self.torrent_count += 1
                continue

            url = self.site.genlink(pick.candidate.tid)
            self.store.quota_add(self.cfg.name, "dl", today())
            if not url:
                self._log("error", pick.candidate.name, "换取下载链接失败")
                continue

            ok = self.qb.add_url(
                url=url,
                category=self.cfg.category,
                save_path=self.cfg.save_path,
                paused=False,
            )
            if ok:
                self.added_total += 1
                self.store.counter_add(self.cfg.name, "added")
                self.store.mark_added(self.cfg.name, pick.candidate.tid, pick.candidate.name)
                new_hash = self._reconcile_new_hash(known_hashes)
                if new_hash:
                    known_hashes.add(new_hash)
                    self.store.set_meta(
                        self.cfg.name, new_hash, pick.candidate.tid,
                        pick.candidate.discount, pick.candidate.discount_end)
                self.used_bytes += pick.candidate.size
                self.torrent_count += 1
                self._log("add", pick.candidate.name,
                          "%.2fGB %s" % (pick.candidate.size / 1024 ** 3, pick.candidate.discount))
            else:
                self._log("error", pick.candidate.name, "qB 拒绝添加")

        # 加种后以 qB 为准对账：qB 会按 infohash 去重，站点标题与 qB 名称也可能不同
        try:
            torrents = self.qb.list_torrents(category=self.cfg.category)
            self.used_bytes = sum(t.size for t in torrents)
            self.torrent_count = len(torrents)
        except QBError:
            pass

        if plan.stop_reason and not plan.picks:
            self._log("idle", "本轮未加种", plan.stop_reason)

    # ---------- Web 快照 ----------
    def snapshot(self) -> dict:
        cfg = self.cfg
        cap = cfg.limits
        search_used = self.store.quota_used(cfg.name, "search", today())
        dl_used = self.store.quota_used(cfg.name, "dl", today())
        next_search = max(0, int((self.refresh_every_ticks - self.ticks_since_search) * self.tick_seconds))

        current_dl = sum(getattr(t, "downloaded", 0) for t in self.torrents_cache)
        current_ul = sum(getattr(t, "uploaded", 0) for t in self.torrents_cache)

        return {
            "name": cfg.name,
            "site": cfg.site,
            "last_error": self.last_error,
            "config": {
                "enabled": cfg.enabled,
                "dry_run": cfg.dry_run,
                "disk_gb": cfg.disk_gb,
                "discounts": list(cfg.discounts),
                "categories": list(cfg.categories),
                "size_mb": list(cfg.size_mb),
                "save_path": cfg.save_path,
                "category": cfg.category,
                "delete_threshold_mb": cfg.delete_threshold_mb,
                "window_minutes": cfg.window_minutes,
                "exempt_minutes": cfg.exempt_minutes,
                "min_keep_minutes": cfg.min_keep_minutes,
                "max_torrents": cfg.max_torrents,
                "add_per_tick": cfg.add_per_tick,
                "limits": {
                    "search_per_day": cap.search_per_day,
                    "dl_per_day": cap.dl_per_day,
                    "safety": cap.safety,
                },
            },
            "stats": {
                "used_gb": round(self.used_bytes / 1024 ** 3, 2),
                "torrents": self.torrent_count,
                "candidates": len(self.candidates),
                "current_downloaded": current_dl,
                "current_uploaded": current_ul,
                "since_start_downloaded": self.dl_since_start,
                "since_start_uploaded": self.ul_since_start,
                "added_total": self.store.counter_get(cfg.name, "added"),
                "removed_total": self.store.counter_get(cfg.name, "removed"),
                "search_used": search_used,
                "search_cap": cap.search_cap,
                "dl_used": dl_used,
                "dl_cap": cap.dl_cap,
                "next_search_seconds": next_search,
                "quota_exhausted": self.quota_exhausted,
            },
            "actions": [a.as_dict() for a in list(self.actions)[:20]],
        }

    def torrent_rows(self, now: Optional[float] = None) -> dict:
        """详情页数据：运行中的任务 + 已删除的归档。"""
        now = time.time() if now is None else now
        window_start = int(now - 3600)
        meta = self.store.meta_map(self.cfg.name)
        active = []
        for t in self.torrents_cache:
            ratio = (t.uploaded / t.downloaded) if t.downloaded else 0.0
            active.append({
                "name": t.name,
                "size": t.size,
                "downloaded": t.downloaded,
                "uploaded": t.uploaded,
                "last_hour_upload": self.store.upload_window(self.cfg.name, t.hash, window_start) or 0,
                "ratio": round(ratio, 3),
                "progress": round(t.progress * 100, 1),
                "seeds": t.num_seeds,
                "leechs": t.num_leechs,
                "dlspeed": t.dlspeed,
                "upspeed": t.upspeed,
                "added_on": t.added_on or 0,
                "last_activity": t.last_activity or 0,
                "state": t.state,
                "discount": (meta.get(t.hash) or {}).get("discount", ""),
                "discount_end": (meta.get(t.hash) or {}).get("discount_end"),
            })
        removed = []
        for row in self.store.archived_torrents(self.cfg.name):
            ratio = (row["uploaded"] / row["downloaded"]) if row["downloaded"] else 0.0
            removed.append({
                "name": row["name"],
                "size": row["size"],
                "downloaded": row["downloaded"],
                "uploaded": row["uploaded"],
                "last_hour_upload": 0,
                "ratio": round(ratio, 3),
                "progress": round((row["progress"] or 0) * 100, 1),
                "seeds": 0,
                "leechs": 0,
                "dlspeed": 0,
                "upspeed": 0,
                "added_on": row["added_on"] or 0,
                "last_activity": row["removed_at"] or 0,
                "state": "removed",
                "removed_at": row["removed_at"],
                "discount": row.get("discount") or "",
                "discount_end": row.get("discount_end"),
            })
        return {"active": active, "removed": removed}


class Scheduler(threading.Thread):
    """后台调度线程：固定 tick，逻辑顺序由各自 runner 保证。"""

    def __init__(self, runners: List[ServiceRunner], tick_seconds: int):
        super().__init__(daemon=True, name="pt-seedflow-scheduler")
        self.runners = runners
        self.tick_seconds = max(10, tick_seconds)
        self._stop_event = threading.Event()

    def run(self) -> None:
        # 启动后先跑一轮，便于立刻看到状态
        while not self._stop_event.is_set():
            for runner in self.runners:
                if runner.cfg.enabled:
                    runner.tick()
            self._stop_event.wait(self.tick_seconds)

    def stop(self) -> None:
        self._stop_event.set()
