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
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---------- 工具 ----------
    def _log(self, kind: str, name: str, detail: str = "") -> None:
        self.actions.appendleft(Action(int(time.time()), kind, name, detail))

    def _need_refresh(self) -> bool:
        return self.ticks_since_search >= self.refresh_every_ticks

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

    # ---------- 删除 ----------
    def _step_delete(self, torrents) -> None:
        now = time.time()
        window = self.cfg.window_minutes * 60
        targets: List[tuple] = []

        for torrent in torrents:
            self.store.add_sample(self.cfg.name, torrent.hash, torrent.uploaded, int(now))
            window_bytes = self.store.upload_window(
                self.cfg.name, torrent.hash, int(now - window))

            previous = self._progress.get(torrent.hash)
            remove, reason = decide_delete(torrent, self.cfg, now, window_bytes, previous)
            self._progress[torrent.hash] = torrent.progress

            if not remove:
                continue
            if self.cfg.dry_run:
                self._log("would-delete", torrent.name, reason)
                continue
            targets.append((torrent.hash, torrent.name))
            self._log("delete", torrent.name, reason)

        if targets and not self.cfg.dry_run:
            # 删前再校验一次：hash + 名字 + 分类三重匹配，且必须是本服务分类
            verified = verify_deletable(torrents, targets, self.cfg.category)
            if len(verified) != len(targets):
                self._log("error", "-", "校验后仅 %d/%d 个可删" % (len(verified), len(targets)))
            if not verified:
                return
            hashes = [item[0] for item in verified]
            if self.qb.delete(hashes, delete_files=True):
                self.deleted_total += len(hashes)
                self._log("deleted", "%d 个任务" % len(hashes), "已连同文件删除")
            else:
                self._log("error", "-", "删除失败，共 %d 个" % len(hashes))

    # ---------- 搜索 ----------
    def _step_search(self) -> None:
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
        self.candidates = candidates
        self.last_search_at = time.time()
        self.ticks_since_search = 0
        self._log("search", "候选池", "%d 个候选" % len(candidates))

    # ---------- 加种 ----------
    def _step_add(self, torrents) -> None:
        if not self.candidates:
            return

        cap = self.cfg.limits.dl_cap
        used = self.store.quota_used(self.cfg.name, "dl", today())
        left = cap - used
        if left <= 0:
            self.quota_exhausted = True
            self._log("quota", "dl", "每日额度已用尽 %d/%d" % (used, cap))
            return

        current_tids = {t.name for t in torrents}
        removed = set(self.store.removed_tids_since(self.cfg.name, int(time.time()) - 86400))

        plan: AddPlan = plan_adds(
            candidates=self.candidates,
            cfg=self.cfg,
            now=time.time(),
            used_bytes=self.used_bytes,
            current_count=self.torrent_count,
            dl_left=left,
            exclude_tids=removed,
        )

        for pick in plan.picks:
            if pick.candidate.name in current_tids:
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
                self.used_bytes += pick.candidate.size
                self.torrent_count += 1
                self._log("add", pick.candidate.name,
                          "%.2fGB %s" % (pick.candidate.size / 1024 ** 3, pick.candidate.discount))
            else:
                self._log("error", pick.candidate.name, "qB 拒绝添加")

        if plan.stop_reason and not plan.picks:
            self._log("idle", "本轮未加种", plan.stop_reason)

    # ---------- Web 快照 ----------
    def snapshot(self) -> dict:
        cap = self.cfg.limits
        search_used = self.store.quota_used(self.cfg.name, "search", today())
        dl_used = self.store.quota_used(self.cfg.name, "dl", today())
        next_search = max(
            0,
            int((self.refresh_every_ticks - self.ticks_since_search) * self.tick_seconds),
        )
        return {
            "name": self.cfg.name,
            "site": self.cfg.site,
            "dry_run": self.cfg.dry_run,
            "disk_gb": self.cfg.disk_gb,
            "used_gb": round(self.used_bytes / 1024 ** 3, 2),
            "torrents": self.torrent_count,
            "max_torrents": self.cfg.max_torrents,
            "candidates": len(self.candidates),
            "quota": {
                "dl_used": dl_used,
                "dl_cap": cap.dl_cap,
                "search_used": search_used,
                "search_cap": cap.search_cap,
            },
            "next_search_seconds": next_search,
            "quota_exhausted": self.quota_exhausted,
            "added_total": self.added_total,
            "deleted_total": self.deleted_total,
            "last_error": self.last_error,
            "actions": [a.as_dict() for a in list(self.actions)[:20]],
        }


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
