#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · 缓存清单（cache manifest）
=====================================
本模块**只做一件事**：记录「每个缓存文件是谁、用什么参数生成的」，
从而让缓存可以被安全地回收。

为什么需要它
------------
缓存文件名是 ``sha256(lib_id | rel | mtime | size | kind | CACHE_VERSION | extra)``，
哈希不可逆 —— 光看盘上的文件，无法知道它对应哪张源图。于是：

  · 源文件被删 / 被改   → 旧缓存永远读不到，成为孤儿，却没人知道该删它；
  · 升级 ``CACHE_VERSION`` → 整批旧缓存同时失效，同样无从识别；
  · 转码中途进程被杀     → 留下 ``*.tmp.mp4`` 残片，只能靠人工发现。

把「生成时的来源信息」记下来（本清单），孤儿就能被**精确地**定位、预览、清理。

安全边界（重要）
----------------
本模块是**纯存储层**，自身不删除任何文件。
实际的删除动作在 ``server.py`` 的 ``cache_gc_apply()`` 里，并且必须满足：
  1. 目标路径 ``realpath`` 必须位于 ``cache/`` 目录之内（双重前缀校验）；
  2. 只允许删除清单里被判定为孤儿的缓存文件；
  3. **绝不触碰源照片 / 源视频目录**。

换句话说：清缓存与删原图是两条完全独立的路径，前者不可能影响后者。

表结构
------
meta(k PK, v)
entries(
  key        TEXT PRIMARY KEY,   -- 缓存文件名（sha256 hex）
  lib_id     TEXT,               -- 来源图库
  rel        TEXT,               -- 来源文件相对路径（正斜杠）
  src_mtime  REAL,               -- 生成时的源文件 mtime（判断源是否变化）
  src_size   INTEGER,            -- 生成时的源文件 size
  kind       TEXT,               -- 'thumb' | 'photo' | 'trans'
  variant    TEXT,               -- 人类可读参数，如 'webp/320'
  bytes      INTEGER,            -- 缓存文件大小
  created    INTEGER              -- 生成时间（epoch 秒）
)
"""

import os
import time
import sqlite3
import threading

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);

CREATE TABLE IF NOT EXISTS entries (
  key        TEXT PRIMARY KEY,
  lib_id     TEXT    NOT NULL DEFAULT '',
  rel        TEXT    NOT NULL DEFAULT '',
  src_mtime  REAL    NOT NULL DEFAULT 0,
  src_size   INTEGER NOT NULL DEFAULT 0,
  kind       TEXT    NOT NULL DEFAULT 'thumb',
  variant    TEXT    NOT NULL DEFAULT '',
  bytes      INTEGER NOT NULL DEFAULT 0,
  created    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_entries_src  ON entries(lib_id, rel);
CREATE INDEX IF NOT EXISTS ix_entries_kind ON entries(kind);
"""

# 允许的来源类型。任何不在此集合里的 kind 都视为异常数据，不予记录。
VALID_KINDS = ("thumb", "photo", "trans")


class CacheIndex:
    """线程安全的 SQLite 缓存清单封装。"""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        with self._lock:
            self.conn.executescript(_SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(k, v) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),))
            self.conn.commit()

    # ---------------- 生命周期 ----------------
    def close(self):
        try:
            with self._lock:
                self.conn.commit()
                self.conn.close()
        except sqlite3.Error:
            pass

    def _meta_get(self, k, default=None):
        with self._lock:
            r = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def _meta_set(self, k, v):
        with self._lock:
            self.conn.execute(
                "INSERT INTO meta(k, v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
            self.conn.commit()

    # ---------------- 写入 ----------------
    def record(self, key, lib_id, rel, src_mtime, src_size, kind, variant,
               bytes_, created=None):
        """记录一条缓存来源。key 冲突时覆盖（同 key 必然同来源）。"""
        if not key or kind not in VALID_KINDS:
            return
        row = (key, lib_id or "", rel or "", float(src_mtime or 0),
               int(src_size or 0), kind, variant or "", int(bytes_ or 0),
               int(created if created is not None else time.time()))
        with self._lock:
            self.conn.execute(
                "INSERT INTO entries(key, lib_id, rel, src_mtime, src_size, "
                "kind, variant, bytes, created) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "  lib_id=excluded.lib_id, rel=excluded.rel, "
                "  src_mtime=excluded.src_mtime, src_size=excluded.src_size, "
                "  kind=excluded.kind, variant=excluded.variant, "
                "  bytes=excluded.bytes, created=excluded.created", row)
            self.conn.commit()          # ★ 必须提交，否则单条写入会一直挂在未提交事务里

    def record_batch(self, rows):
        """rows: 可迭代的元组 (key, lib_id, rel, src_mtime, src_size,
        kind, variant, bytes, created)。已存在的 key 跳过（用于批量收养）。"""
        rows = list(rows)
        if not rows:
            return 0
        now = int(time.time())
        data = [r if len(r) == 9 else (*r, now) for r in rows]
        data = [d for d in data if d[0] and d[5] in VALID_KINDS]
        with self._lock:
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO entries(key, lib_id, rel, src_mtime, "
                "src_size, kind, variant, bytes, created) "
                "VALUES(?,?,?,?,?,?,?,?,?)", data)
            self.conn.commit()
            return cur.rowcount or 0

    # ---------------- 读取 ----------------
    def all_entries(self):
        with self._lock:
            cur = self.conn.execute(
                "SELECT key, lib_id, rel, src_mtime, src_size, kind, variant, "
                "bytes, created FROM entries")
            return [dict(r) for r in cur]

    def all_keys(self):
        with self._lock:
            return {r["key"] for r in self.conn.execute("SELECT key FROM entries")}

    def count(self):
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) c FROM entries").fetchone()["c"]

    # ---------------- 删除（仅清单行，不碰文件） ----------------
    def delete_keys(self, keys):
        keys = list(keys)
        if not keys:
            return 0
        n = 0
        with self._lock:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                qs = ",".join("?" * len(chunk))
                cur = self.conn.execute(
                    f"DELETE FROM entries WHERE key IN ({qs})", chunk)
                n += cur.rowcount or 0
            self.conn.commit()
        return n

    def forget_probe(self, kind=None):
        """忘记某类缓存记录（用于强制重建清单）。"""
        with self._lock:
            if kind:
                cur = self.conn.execute("DELETE FROM entries WHERE kind=?", (kind,))
            else:
                cur = self.conn.execute("DELETE FROM entries")
            self.conn.commit()
            return cur.rowcount or 0

    # ---------------- 统计 ----------------
    def stats(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT kind, COUNT(*) c, COALESCE(SUM(bytes),0) b "
                "FROM entries GROUP BY kind").fetchall()
        by_kind = {r["kind"]: {"files": r["c"], "bytes": r["b"]} for r in rows}
        total_files = sum(v["files"] for v in by_kind.values())
        total_bytes = sum(v["bytes"] for v in by_kind.values())
        size = 0
        for p in (self.path, self.path + "-wal", self.path + "-shm"):
            try:
                size += os.path.getsize(p)
            except OSError:
                pass
        return {"dbPath": self.path, "dbMB": round(size / 1024 / 1024, 2),
                "entries": total_files, "bytes": total_bytes,
                "byKind": by_kind, "schemaVersion": SCHEMA_VERSION,
                "adoptedAt": self._meta_get("adopted_at")}

    def mark_adopted(self):
        self._meta_set("adopted_at", int(time.time()))

    def adopted_at(self):
        return self._meta_get("adopted_at")

    def get_meta(self, k, default=None):
        return self._meta_get(k, default)

    def set_meta(self, k, v):
        self._meta_set(k, v)
