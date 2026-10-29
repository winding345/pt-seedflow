"""调度核心：每 tick 先删后补，每小时刷新候选池。"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from .config import AppConfig, ServiceConfig
from .lifecycle import (
    AddPlan,
    Pick,
    decide_delete,
    filter_candidates,
    plan_adds,
    verify_deletable,
)
from .qb import QBError, QBittorrent
from .scoring import SEARCH_PAGE_SIZE, Candidate
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
    # 本 tick 内累计的流量，结束时统一写入小时桶
    _pending_traffic: Dict[int, tuple] = field(default_factory=dict)
    # 最近一次 qB 任务快照，供详情页使用（避免 Web 端反查 qB）
    torrents_cache: List[object] = field(default_factory=list)
    _cache_restored: bool = False
    _pool_exhausted: bool = False
    _last_prune_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def apply_config(self, cfg) -> None:
        """热更新服务配置（来自数据库）。"""
        with self._lock:
            old = self.cfg.to_dict()
            if old == cfg.to_dict():
                return
            self.cfg = cfg
            # 配额放开后重新允许加种
            if not self.quota_exhausted:
                pass
            self._log("config", "配置已更新", "来自数据库")

    # ---------- 工具 ----------
    def _log(self, kind: str, name: str, detail: str = "") -> None:
        self.actions.appendleft(Action(int(time.time()), kind, name, detail))

    def _restore_candidates(self) -> None:
        """容器重启后恢复上次的候选池，避免界面空白一小时。"""
        if self._cache_restored:
            return
        self._cache_restored = True
        payload, saved_at = self.store.load_candidates(self.cfg.name)
        if not payload:
            return
        try:
            restored = [Candidate.from_dict(item) for item in json.loads(payload)]
        except Exception:  # noqa: BLE001  缓存损坏就重新 search
            return
        if restored:
            self.candidates = restored
            self.last_search_at = float(saved_at)

    def _need_refresh(self) -> bool:
        """定时刷新；候选池被吃空时按更短的冷却提前补一轮。"""
        if self.ticks_since_search >= self.refresh_every_ticks:
            return True
        if self._pool_exhausted and self.cfg.refill_when_empty and self.last_search_at:
            return (time.time() - self.last_search_at) >= self.cfg.refill_min_seconds
        return False

    def _account(self, torrents) -> None:
        """把每个任务的增量记到当前小时桶（含已删除任务，删除前会补最后一次）。"""
        alive = set()
        hour = int(time.time() // 3600 * 3600)
        dl_sum = up_sum = 0
        for torrent in torrents:
            alive.add(torrent.hash)
            prev = self._prev.get(torrent.hash)
            if prev is not None:
                up_delta = torrent.uploaded - prev[0]
                dl_delta = torrent.downloaded - prev[1]
                if up_delta > 0:
                    up_sum += up_delta
                if dl_delta > 0:
                    dl_sum += dl_delta
            self._prev[torrent.hash] = (torrent.uploaded, torrent.downloaded)
        self.store.add_traffic(self.cfg.name, hour, dl_sum, up_sum)

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
            self._restore_candidates()
            try:
                torrents = self.qb.list_torrents(category=self.cfg.category)
                self.used_bytes = sum(t.size for t in torrents)
                self.torrent_count = len(torrents)
                self._account(torrents)
                self._step_delete(torrents)
                if self._need_refresh():
                    refilling = (self._pool_exhausted
                                 and self.ticks_since_search < self.refresh_every_ticks)
                    self._step_search(force=refilling)
                # 删除后重新取一次，保证空间与任务数是最新的
                torrents = self.qb.list_torrents(category=self.cfg.category)
                self.used_bytes = sum(t.size for t in torrents)
                self.torrent_count = len(torrents)
                if not self.quota_exhausted:
                    self._step_add(torrents)
                self._maybe_prune()
            except QBError as exc:
                self.last_error = str(exc)
                self._log("error", "-", str(exc))
            except Exception as exc:  # noqa: BLE001  单 tick 失败不应终止服务
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                self._log("error", "-", self.last_error)

    def manual_search(self) -> dict:
        """手动刷新候选池：跳过最小间隔，但仍受每日配额限制。"""
        with self._lock:
            self.last_error = ""
            self._restore_candidates()
            try:
                ok = self._step_search(force=True)
            except Exception as exc:  # noqa: BLE001
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                self._log("error", "search", self.last_error)
                ok = False
            return {
                "service": self.cfg.name,
                "ok": ok,
                "candidates": len(self.candidates),
                "snapshot": self.snapshot(),
            }

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

    def _maybe_prune(self) -> None:
        """每天清理一次过期明细，避免 SQLite 无限增长。"""
        now = time.time()
        if now - self._last_prune_at < 86400:
            return
        self._last_prune_at = now
        try:
            # 上传量采样只需保留窗口的一倍
            self.store.prune_samples(int(now - max(self.cfg.window_minutes * 60 * 2, 7200)))
            # 流量明细保留 90 天
            self.store.prune_traffic(int(now - 90 * 86400))
        except Exception:  # noqa: BLE001  清理失败不影响主流程
            pass

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

    def _genlink(self, tid: str, retries: int = 2) -> Optional[str]:
        """换下载链接；连接类错误短暂重试，业务错误直接返回。

        m-team 偶尔会 reset 连接（Errno 104），重试通常立刻就好。
        """
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                return self.site.genlink(tid)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                text = str(exc)
                transient = any(k in text for k in (
                    "连接失败", "Connection reset", "timed out", "超时", "Errno 104"))
                if not transient or attempt >= retries:
                    raise
                time.sleep(1.5 * (attempt + 1))
        if last_error:
            raise last_error
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
            # 统计窗口从"qB 添加时间"和"窗口起点"里取较晚的那个：
            # 刚加进来 20 分钟的种子，不应该拿 1 小时的阈值去衡量它
            since = int(now - window)
            if torrent.added_on:
                since = max(since, int(torrent.added_on))
            window_bytes = self.store.upload_window(self.cfg.name, torrent.hash, since)

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
                hour_ts = int(time.time() // 3600 * 3600)
                for item_hash, item_name in verified:
                    snap = by_hash.get(item_hash)
                    info_x = meta.get(item_hash) or {}
                    prev = self._prev.get(item_hash)
                    if snap and prev:
                        self.store.add_traffic(
                            self.cfg.name, hour_ts,
                            max(0, snap.downloaded - prev[1]),
                            max(0, snap.uploaded - prev[0]))
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
                        discount_end=(info_x.get("discount_end") if info_x else None),
                        published_at=(info_x.get("published_at") if info_x else None),
                        removed_at=now_ts,
                    )
                    self._prev.pop(item_hash, None)
                self.store.counter_add(self.cfg.name, "removed", len(hashes))
                self._log("deleted", "%d 个任务" % len(hashes), "已连同文件删除")
            else:
                self._log("error", "-", "删除失败，共 %d 个" % len(hashes))

    # ---------- 搜索 ----------
    def _step_search(self, force: bool = False) -> bool:
        """刷新候选池。force=True 由手动触发，跳过最小间隔但仍受每日配额限制。"""
        # 强制最小间隔：即使重启或 tick 计数被重置，也不会多刷
        last_ts = self.store.kv_get(self.cfg.name, "last_search_ts", 0)
        elapsed = time.time() - last_ts
        if not force and last_ts and elapsed < self.cfg.search_interval_seconds:
            self._log("idle", "跳过 search",
                      "距上次仅 %d 秒（下限 %d 秒）" % (elapsed, self.cfg.search_interval_seconds))
            self.ticks_since_search = 0
            return False

        cap = self.cfg.limits.search_cap
        used = self.store.quota_used(self.cfg.name, "search", today())
        if used >= cap:
            self._log("quota", "search", "每日额度已用尽 %d/%d" % (used, cap))
            return False

        # 搜索阶段就把"已加过 / 刚删过"的候选剔掉，并分页补足池子
        now_ts = int(time.time())
        exclude = set(self.store.added_tids_since(self.cfg.name, now_ts - 7 * 86400))
        exclude |= set(self.store.removed_tids_since(self.cfg.name, now_ts - 86400))

        collected = []
        seen = set()
        usable = 0
        skipped_excluded = 0
        pages_used = 0
        failed_pages = 0

        for page in range(1, max(1, self.cfg.search_max_pages) + 1):
            part = None
            for attempt in range(3):
                try:
                    part = self.site.search(page=page, page_size=SEARCH_PAGE_SIZE)
                    break
                except Exception as exc:  # noqa: BLE001
                    text = str(exc)
                    transient = any(k in text for k in (
                        "连接失败", "Connection reset", "timed out", "超时", "Errno 104"))
                    if not transient or attempt >= 2:
                        self._log("error", "search", text)
                        self.last_error = text
                        part = None
                        break
                    time.sleep(2.0 * (attempt + 1))

            if part is None:
                failed_pages += 1
                break
            if not part:
                break

            pages_used += 1
            self.store.quota_add(self.cfg.name, "search", today())

            for candidate in part:
                tid = str(candidate.tid)
                if tid in seen:
                    continue
                seen.add(tid)
                if tid in exclude:
                    skipped_excluded += 1
                    continue
                collected.append(candidate)

            # 只有"促销/体积/分类都合格"的才算可用，否则会翻页过早停止
            eligible, _ = filter_candidates(collected, self.cfg, time.time(), exclude)
            usable = len(eligible)
            if usable >= self.cfg.search_min_pool:
                break

        if pages_used == 0:
            if failed_pages:
                return False
            self.last_error = "搜索无结果"
            self._log("error", "search", "站点没有返回任何候选")
            return False

        candidates = collected
        self.candidates = candidates
        self.last_search_at = time.time()
        self.ticks_since_search = 0
        # 持久化上次搜索时间：重启后仍能守住最小间隔
        self.store.kv_set(self.cfg.name, "last_search_ts", int(self.last_search_at))
        self.store.save_candidates(
            self.cfg.name,
            json.dumps([c.as_dict() for c in candidates], ensure_ascii=False),
            int(self.last_search_at),
        )
        detail = "%d 个可用候选" % len(candidates)
        if pages_used > 1 or skipped_excluded:
            detail += "（翻 %d 页，剔除已加过 %d 条）" % (pages_used, skipped_excluded)
        if force and self._pool_exhausted:
            detail += " · 池子空了提前补"
        elif force:
            detail += " · 手动"
        self._log("search", "候选池", detail)
        return True

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

        if not plan.picks:
            # 空间不足/配额/并发上限都不是"池子空"，只有候选本身没有可用项才算
            self._pool_exhausted = plan.stop_reason in ("没有符合条件的新候选",)
        else:
            self._pool_exhausted = False

        added_hashes: List[str] = []
        for pick in plan.picks:
            if pick.candidate.name in current_names:
                continue
            if self.cfg.dry_run:
                self._log("would-add", pick.candidate.name,
                          "%.2fGB score=%.3f" % (pick.candidate.size / 1024 ** 3, pick.score))
                self.used_bytes += pick.candidate.size
                self.torrent_count += 1
                continue

            # RSS 场景站点已直接给出下载链接，省掉一次接口调用
            url = pick.candidate.url or self._genlink(pick.candidate.tid)
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
                    added_hashes.append(new_hash)
                    self.store.set_meta(
                        self.cfg.name, new_hash, pick.candidate.tid,
                        pick.candidate.discount, pick.candidate.discount_end,
                        published_at=pick.candidate.created)
                self.used_bytes += pick.candidate.size
                self.torrent_count += 1
                self._log("add", pick.candidate.name,
                          "%.2fGB %s" % (pick.candidate.size / 1024 ** 3, pick.candidate.discount))
            else:
                self._log("error", pick.candidate.name, "qB 拒绝添加")

        # 新加的种子统一设为「强制继续」，避免卡在暂停/排队状态
        if added_hashes and not self.cfg.dry_run:
            try:
                if not self.qb.set_force_start(added_hashes):
                    self._log("error", "-", "%d 个新种设置强制继续失败" % len(added_hashes))
            except QBError as exc:
                self._log("error", "-", "设置强制继续失败: %s" % exc)

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
        # "上线以来" = 所有任务（运行中 + 已删除）的下载/上传总和，
        # 不依赖本进程启动时间，也不依赖 traffic 表何时建立
        archived_dl, archived_ul = self.store.archive_totals(cfg.name)
        total_dl = current_dl + archived_dl
        total_ul = current_ul + archived_ul

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
                "since_launch_downloaded": total_dl,
                "since_launch_uploaded": total_ul,
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
            since = window_start
            if t.added_on:
                since = max(since, int(t.added_on))
            active.append({
                "name": t.name,
                "size": t.size,
                "downloaded": t.downloaded,
                "uploaded": t.uploaded,
                "last_hour_upload": self.store.upload_window(self.cfg.name, t.hash, since) or 0,
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
                "published_at": (meta.get(t.hash) or {}).get("published_at"),
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
                "published_at": row.get("published_at"),
            })
        return {"active": active, "removed": removed}


class Scheduler(threading.Thread):
    """后台调度线程：固定 tick，逻辑顺序由各自 runner 保证。"""

    def __init__(self, runners: List[ServiceRunner], tick_seconds: int,
                 repo=None, reload_seconds: int = 300, failures: Optional[dict] = None,
                 store=None, downloader=None):
        super().__init__(daemon=True, name="pt-seedflow-scheduler")
        self.runners = runners
        self.tick_seconds = max(10, tick_seconds)
        self.repo = repo
        self.reload_seconds = max(5, reload_seconds)
        self._last_reload = 0.0
        self._last_tick = 0.0
        self._stop_event = threading.Event()
        # 初始化失败的服务（面板会显示），配置重载时自动重试
        self.failures = failures if failures is not None else {}
        self.store = store
        self.downloader = downloader
        self._qb = None

    def _reload_configs(self) -> None:
        """从数据库重读配置并热更新，改了 MySQL 五分钟内生效。"""
        if not self.repo:
            return
        now = time.time()
        if now - self._last_reload < self.reload_seconds:
            return
        self._last_reload = now
        try:
            configs = self.repo.service_configs()
            sites = self.repo.site_configs()
        except Exception:  # noqa: BLE001  数据库抖动时保持旧配置
            return

        from .sites import create_site

        for runner in self.runners:
            incoming = configs.get(runner.cfg.name)
            if incoming is not None:
                runner.apply_config(incoming)

            # 站点配置（cookie / list_url 等）变化时重建适配器
            site_cfg = sites.get(runner.cfg.site)
            if site_cfg is None:
                continue
            old = getattr(runner, "_site_signature", None)
            signature = repr(sorted(site_cfg.to_dict().items(), key=lambda kv: kv[0]))
            if old == signature:
                continue
            try:
                runner.site = create_site(site_cfg)
                runner._site_signature = signature
                self.failures.pop(runner.cfg.name, None)
                if old is not None:
                    runner._log("config", "站点配置已更新", site_cfg.name)
            except Exception as exc:  # noqa: BLE001
                self.failures[runner.cfg.name] = {
                    "name": runner.cfg.name, "site": runner.cfg.site,
                    "error": str(exc), "config": runner.cfg.to_dict(),
                }
                runner._log("error", "站点配置更新失败", str(exc))

        self._revive_failed(configs, sites)

    def _revive_failed(self, configs: dict, sites: dict) -> None:
        """把启动时失败/后来新增的服务补进来，成功后从 failures 移除。"""
        if not configs or self.store is None:
            return
        alive = {r.cfg.name for r in self.runners}
        for name, service in configs.items():
            if not service.enabled or name in alive:
                continue
            site_cfg = sites.get(service.site)
            if site_cfg is None:
                self.failures[name] = {
                    "name": name, "site": service.site,
                    "error": "站点 %s 未配置" % service.site,
                    "config": service.to_dict(),
                }
                continue
            try:
                from .sites import create_site
                site = create_site(site_cfg)
            except Exception as exc:  # noqa: BLE001
                self.failures[name] = {
                    "name": name, "site": service.site,
                    "error": str(exc), "config": service.to_dict(),
                }
                continue
            if self._qb is None:
                from .qb import QBittorrent
                self._qb = QBittorrent(self.downloader)
            runner = ServiceRunner(
                cfg=service, site=site, qb=self._qb, store=self.store,
                tick_seconds=self.tick_seconds,
                refresh_every_ticks=getattr(self, "refresh_every_ticks", 1),
            )
            self.runners.append(runner)
            self.failures.pop(name, None)
            runner._log("config", "服务已恢复", service.site)

    def run(self) -> None:
        # 配置重载与业务 tick 各自独立计时：
        # 改了数据库配置不必等下一个 tick 才生效。
        while not self._stop_event.is_set():
            self._reload_configs()
            now = time.time()
            if now - self._last_tick >= self.tick_seconds:
                self._last_tick = now
                for runner in self.runners:
                    if runner.cfg.enabled:
                        runner.tick()
            self._stop_event.wait(1.0)

    def stop(self) -> None:
        self._stop_event.set()
