"""Cache capacity policy and process-local leases. Never operates on originals."""
import math
import os
import shutil
import threading
import time
from contextlib import contextmanager

GIB = 1024 ** 3
DEFAULTS = {"maxGB": 0, "minFreeGB": 2, "lowWatermark": .8,
            "checkIntervalSeconds": 60, "graceSeconds": 300,
            "temporaryGraceSeconds": 3600,
            "shares": {"thumb": .2, "photo": .3, "trans": .5}}


def config(raw=None):
    raw = raw or {}
    out = dict(DEFAULTS)
    for name in ("maxGB", "minFreeGB", "checkIntervalSeconds", "graceSeconds",
                 "temporaryGraceSeconds", "lowWatermark"):
        value = float(raw.get(name, out[name]))
        if not math.isfinite(value) or value < 0:
            raise ValueError("缓存配置非法: " + name)
        out[name] = value
    if not 0 < out["lowWatermark"] < 1 or out["checkIntervalSeconds"] < 1:
        raise ValueError("缓存水位应在 0 与 1 之间，检查间隔至少 1 秒")
    shares = raw.get("shares", DEFAULTS["shares"])
    out["shares"] = {k: float(shares.get(k, 0)) for k in ("thumb", "photo", "trans")}
    if any(not math.isfinite(v) or v < 0 for v in out["shares"].values()) or abs(sum(out["shares"].values()) - 1) > .00001:
        raise ValueError("缓存 shares 必须非负且总和为 1")
    return out


def choose_victims(entries, total, free, cfg, now=None, pressure=False):
    """Soft category budgets can borrow space. Thumbnail share is a reserve."""
    now = time.time() if now is None else now
    maximum = int(cfg["maxGB"] * GIB)
    if not maximum or (not pressure and total < maximum and free >= cfg["minFreeGB"] * GIB):
        return [], 0
    needed = max(total - int(maximum * cfg["lowWatermark"]),
                 int(cfg["minFreeGB"] * GIB - free), 0)
    usage = {k: sum(e["bytes"] for e in entries if e["kind"] == k) for k in cfg["shares"]}
    targets = {k: int(maximum * cfg["lowWatermark"] * v) for k, v in cfg["shares"].items()}
    candidates = sorted((e for e in entries if e.get("eligible", True) and now - max(e["created"], e.get("last_access", 0)) >= cfg["graceSeconds"]),
                        key=lambda e: (e.get("last_access") or e["created"], -e["bytes"], e["key"]))
    picked, freed = [], 0
    while candidates and freed < needed:
        over = [e for e in candidates if usage[e["kind"]] > targets[e["kind"]]]
        pool = over or candidates
        # Keep the thumbnail reserve, unless the disk is critically short of space.
        pool = [e for e in pool if e["kind"] != "thumb" or
                usage["thumb"] - e["bytes"] >= targets["thumb"] or free + freed < cfg["minFreeGB"] * GIB]
        if not pool:
            pool = [e for e in candidates if e["kind"] != "thumb"]
        if not pool:
            break
        priority = {"trans": 0, "photo": 1, "thumb": 2}
        victim = min(pool, key=lambda e: (priority[e["kind"]], e.get("last_access") or e["created"], -e["bytes"], e["key"]))
        candidates.remove(victim)
        picked.append(victim)
        freed += victim["bytes"]
        usage[victim["kind"]] -= victim["bytes"]
    return picked, needed


class CacheSpaceError(OSError):
    pass


class Governor:
    def __init__(self, root, store):
        self.root = os.path.realpath(root)
        self.store = store
        self.lock = threading.RLock()
        self.maintenance = threading.RLock()
        self.active = {}
        self.pending = {}
        self.local = threading.local()
        self.reserved = 0
        self.last = {}

    def within(self, path):
        path = os.path.realpath(path)
        return path.startswith(self.root + os.sep)

    def protected(self, path):
        path = os.path.realpath(path)
        with self.lock:
            return any(path == p or path.startswith(p + ".") for p in self.active)

    @contextmanager
    def request(self):
        previous = getattr(self.local, "leases", None)
        leases = []
        self.local.leases = leases
        try:
            yield
        finally:
            with self.lock:
                for path in leases:
                    self._release(path)
            self.local.leases = previous

    def _release(self, path):
        self.active[path] -= 1
        if not self.active[path]:
            del self.active[path]

    @contextmanager
    def pin(self, path):
        path = os.path.realpath(path)
        if not self.within(path):
            raise ValueError("缓存路径越界")
        with self.lock:
            self.active[path] = self.active.get(path, 0) + 1
        try:
            yield
        finally:
            with self.lock:
                leases = getattr(self.local, "leases", None)
                if leases is None:
                    self._release(path)
                else:
                    leases.append(path)

    def touch(self, key):
        with self.lock:
            self.pending[key] = int(time.time())

    def flush(self):
        if self.store is None:
            return
        with self.lock:
            pending, self.pending = self.pending, {}
        try:
            self.store.touch_many(pending)
        except Exception:
            with self.lock:
                for key, ts in pending.items():
                    self.pending[key] = max(ts, self.pending.get(key, 0))
            raise

    def disk_free(self):
        path = self.root
        while not os.path.exists(path):
            path = os.path.dirname(path)
        return shutil.disk_usage(path).free

    @contextmanager
    def reserve(self, size, cfg):
        size = max(0, int(size))
        with self.lock:
            if self.disk_free() - self.reserved - size < cfg["minFreeGB"] * GIB:
                raise CacheSpaceError("磁盘剩余空间不足，暂缓生成缓存")
            self.reserved += size
        try:
            yield
        finally:
            with self.lock:
                self.reserved -= size

    def remove(self, path, size=None, mtime_ns=None):
        """Coordinate removal with new readers/producers, and recheck identity."""
        with self.lock:
            if not self.within(path) or os.path.islink(path) or self.protected(path):
                return False
            st = os.stat(path)
            if size is not None and st.st_size != size:
                return False
            if mtime_ns is not None and st.st_mtime_ns != mtime_ns:
                return False
            os.remove(path)
            return True
