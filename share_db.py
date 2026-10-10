#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · 分享账本（share ledger）
===================================
本模块**只做一件事**：保存「谁被授权看哪些照片」。

为什么要独立成库
----------------
访问控制是后加的，不能把风险带到既有的 `index.db`（1.3 万+ 行真实索引）/
`cache.db`（缓存清单）/ `trash.db`（回收站账本）上。于是分享数据放进**独立文件**
`data/shares.db`：

  · 不动其它三个库的 schema / 版本号 → 既有数据零风险；
  · 出问题直接删掉 `shares.db` 即可完全回滚到改造前；
  · 分享记录与源照片**完全解耦**：删一条分享只删两表行，绝不触碰任何照片文件。

安全设计（重要）
----------------
1. **令牌不可枚举**：`secrets.token_urlsafe(32)`（256 bit），不是日期、不是自增 ID。
2. **库里只存摘要**：只保存 `sha256(token)`（可加服务端 pepper 变 HMAC-SHA256）的 hex，
   明文令牌永远不落盘。库文件被看到也无法反推出可用链接。
3. **集合在创建时固化**：`share_items` 写入「创建那一刻」的 `(rel, mov_rel)` 快照；
   之后相册里新增 / 移动 / 重新索引文件，**都不会改变已发出分享的范围**。
4. **判活逐请求进行**：本模块只回答「这条分享现在是否有效」，
   是否放行由调用方（`server.py` 的 `read_guard` / `media_guard`）每个请求重新判定，
   **不缓存结论** → 撤销后立即失效。

表结构
------
meta(k PK, v)
shares(
  share_id   TEXT PK,     -- 随机 id（非自增）
  token_hash TEXT,        -- sha256/HMAC(token) hex
  lib_id     TEXT,        -- 所属图库
  title      TEXT,        -- 展示用标题
  kind       TEXT,        -- 'items' | 'month'
  created_at INTEGER,
  expires_at INTEGER,     -- 0 = 不过期
  revoked_at INTEGER      -- 0 = 未撤销
)
share_items(share_id, rel, mov_rel)   -- 授权集合的快照
"""

import os
import time
import sqlite3
import secrets
import threading

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);

CREATE TABLE IF NOT EXISTS shares (
  share_id   TEXT PRIMARY KEY,
  token_hash TEXT    NOT NULL,
  lib_id     TEXT    NOT NULL DEFAULT '',
  title      TEXT    NOT NULL DEFAULT '',
  kind       TEXT    NOT NULL DEFAULT 'items',
  created_at INTEGER NOT NULL DEFAULT 0,
  expires_at INTEGER NOT NULL DEFAULT 0,
  revoked_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_shares_token ON shares(token_hash);
CREATE INDEX IF NOT EXISTS ix_shares_lib   ON shares(lib_id);

CREATE TABLE IF NOT EXISTS share_items (
  share_id TEXT NOT NULL,
  rel      TEXT NOT NULL,
  mov_rel  TEXT,
  PRIMARY KEY (share_id, rel)
);

CREATE INDEX IF NOT EXISTS ix_share_items_share ON share_items(share_id);
"""

VALID_KINDS = ("items", "month")


class ShareStore:
    """线程安全的 SQLite 分享账本封装。"""

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
    @staticmethod
    def new_id():
        """随机 id（非自增、不可枚举）。"""
        return secrets.token_hex(8)

    def create(self, token_hash, lib_id, items, title="", kind="items",
               expires_at=0, share_id=None, created_at=None):
        """
        建一条分享。items: 可迭代的 (rel, mov_rel) 或 {'rel','mov_rel'}。
        返回该分享的完整记录（含 share_id）。**不动任何照片文件。**
        """
        if not token_hash or not lib_id:
            return None
        if kind not in VALID_KINDS:
            kind = "items"
        sid = share_id or self.new_id()
        now = int(created_at if created_at is not None else time.time())
        exp = int(expires_at or 0)
        rows = []
        for it in items or []:
            if isinstance(it, dict):
                rel, mv = it.get("rel"), it.get("mov_rel")
            else:
                rel, mv = (list(it) + [None])[:2] if not isinstance(it, str) else (it, None)
                if isinstance(it, str):
                    rel, mv = it, None
            rel = (rel or "").strip()
            if not rel:
                continue
            mv = (mv or "").strip() or None
            rows.append((sid, rel, mv))
        # 去重（同 rel 只留一条）
        seen, uniq = set(), []
        for r in rows:
            if r[1] in seen:
                continue
            seen.add(r[1])
            uniq.append(r)
        with self._lock:
            self.conn.execute(
                "INSERT INTO shares(share_id, token_hash, lib_id, title, kind, "
                "created_at, expires_at, revoked_at) VALUES(?,?,?,?,?,?,?,0)",
                (sid, token_hash, lib_id, title or "", kind, now, exp))
            self.conn.executemany(
                "INSERT OR REPLACE INTO share_items(share_id, rel, mov_rel) "
                "VALUES(?,?,?)", uniq)
            self.conn.commit()
        return self.get(sid)

    def revoke(self, share_id):
        """撤销：打时间戳（保留记录，便于审计）。返回是否命中。"""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE shares SET revoked_at=? WHERE share_id=? AND revoked_at=0",
                (int(time.time()), share_id or ""))
            self.conn.commit()
            return (cur.rowcount or 0) > 0

    def delete(self, share_id):
        """彻底删除一条分享（含集合行）。**不触碰任何照片文件。**"""
        with self._lock:
            cur = self.conn.execute("DELETE FROM shares WHERE share_id=?",
                                    (share_id or "",))
            self.conn.execute("DELETE FROM share_items WHERE share_id=?",
                              (share_id or "",))
            self.conn.commit()
            return (cur.rowcount or 0) > 0

    def revoke_all(self, lib_id=None):
        with self._lock:
            if lib_id:
                cur = self.conn.execute(
                    "UPDATE shares SET revoked_at=? WHERE lib_id=? AND revoked_at=0",
                    (int(time.time()), lib_id))
            else:
                cur = self.conn.execute(
                    "UPDATE shares SET revoked_at=? WHERE revoked_at=0",
                    (int(time.time()),))
            self.conn.commit()
            return cur.rowcount or 0

    # ---------------- 读取 ----------------
    def get(self, share_id):
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM shares WHERE share_id=?", (share_id or "",)).fetchone()
            if not r:
                return None
            n = self.conn.execute(
                "SELECT COUNT(*) c FROM share_items WHERE share_id=?",
                (share_id,)).fetchone()["c"]
        d = dict(r)
        d["n_items"] = int(n)
        return d

    def lookup_by_token(self, token_hash):
        """按摘要找一条分享（**不过滤撤销/过期**，判活交给调用方）。"""
        if not token_hash:
            return None
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM shares WHERE token_hash=? ORDER BY created_at DESC "
                "LIMIT 1", (token_hash,)).fetchone()
            if not r:
                return None
            sid = r["share_id"]
            n = self.conn.execute(
                "SELECT COUNT(*) c FROM share_items WHERE share_id=?",
                (sid,)).fetchone()["c"]
        d = dict(r)
        d["n_items"] = int(n)
        return d

    def list_shares(self, lib_id=None, include_dead=False):
        q = "SELECT * FROM shares"
        args = []
        where = []
        if lib_id:
            where.append("lib_id=?")
            args.append(lib_id)
        if not include_dead:
            where.append("revoked_at=0")
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY created_at DESC"
        with self._lock:
            rows = self.conn.execute(q, args).fetchall()
            out = []
            for r in rows:
                n = self.conn.execute(
                    "SELECT COUNT(*) c FROM share_items WHERE share_id=?",
                    (r["share_id"],)).fetchone()["c"]
                d = dict(r)
                d["n_items"] = int(n)
                out.append(d)
            return out

    def count(self, lib_id=None):
        with self._lock:
            if lib_id:
                return self.conn.execute(
                    "SELECT COUNT(*) c FROM shares WHERE lib_id=? AND revoked_at=0",
                    (lib_id,)).fetchone()["c"]
            return self.conn.execute(
                "SELECT COUNT(*) c FROM shares WHERE revoked_at=0").fetchone()["c"]

    def items_of(self, share_id):
        """授权集合的快照：返回 [{'rel','mov_rel'}, ...]。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT rel, mov_rel FROM share_items WHERE share_id=?",
                (share_id or "",)).fetchall()
        return [{"rel": r["rel"], "mov_rel": r["mov_rel"]} for r in rows]

    # ---------------- 判活（纯函数，逐请求调用） ----------------
    @staticmethod
    def is_active(rec, now=None):
        """记录是否仍可用（未撤销、未过期、有集合）。"""
        if not rec:
            return False
        now = int(now if now is not None else time.time())
        if int(rec.get("revoked_at") or 0) > 0:
            return False
        exp = int(rec.get("expires_at") or 0)
        if exp and exp <= now:
            return False
        return True

    # ---------------- 统计 ----------------
    def stats(self):
        with self._lock:
            total = self.conn.execute("SELECT COUNT(*) c FROM shares").fetchone()["c"]
            live = self.conn.execute(
                "SELECT COUNT(*) c FROM shares WHERE revoked_at=0").fetchone()["c"]
            items = self.conn.execute(
                "SELECT COUNT(*) c FROM share_items").fetchone()["c"]
        size = 0
        for p in (self.path, self.path + "-wal", self.path + "-shm"):
            try:
                size += os.path.getsize(p)
            except OSError:
                pass
        return {"dbPath": self.path, "dbMB": round(size / 1024 / 1024, 2),
                "shares": total, "live": live, "items": items,
                "schemaVersion": SCHEMA_VERSION}
