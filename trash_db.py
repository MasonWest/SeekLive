#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · 回收站数据层（data/trash.db）
========================================
跟"删除源文件"相关的**唯一**账本。本模块只负责"把意图与结果记下来"，
**自身绝不移动、删除任何文件** —— 真正的文件操作在 server.py 的
`mgmt_delete()` / `mgmt_restore()` / （阶段五的）`mgmt_purge()` 里。

崩溃安全设计（重要）
--------------------
删除/恢复都是"先记意图，再动文件，最后记结果"：

    1) create_batch(op, total)            → batches.status = 'pending'
    2) add_items(status='pending') + link → 每个文件一行，含 plan 的 trash_rel
    3) set_item(status='moving')          → 动这一项之前先标记
    4) 真正移动文件
    5) set_item(status='trashed')         → 移动确认后才落最终状态
    6) finish_batch(status='done'|'partial')

于是任何一步崩溃，重启后 `pending_batches()` 都能看到"有未收尾的批次"，
再由 server.recover_trash() 依据"文件现在到底在哪"把状态**对账**回来 ——
不会静默丢文件，也不会静默丢记录。

表结构
------
batches     (batch_id PK, lib_id, op, created, total, ok, failed, status, note)
items       (item_id PK, batch_id, lib_id, orig_rel, trash_rel, name, kind,
             is_video, size, mtime, group_key, pair_role, live,
             deleted_at, expire_at, status, restored_at, purged_at, new_rel, error)
batch_items (batch_id, item_id)  PK —— 批次覆盖了哪些条目（delete/restore/purge 通用）
audit       (id PK, ts, op, lib_id, batch_id, n, result, detail)
meta        (k PK, v)
"""

import os
import time
import uuid
import sqlite3
import threading

SCHEMA_VERSION = 1

# 文件级状态机
#   pending  → 已登记意图，尚未动
#   moving   → 正在移动（动文件前先置此态，崩溃后可被识别）
#   trashed  → 已在回收站（删除完成）
#   restored → 已恢复到原位
#   purged   → 已永久删除
#   missing  → 两处都找不到（异常，需人工关注，绝不静默忽略）
#   failed   → 操作失败（文件仍在原处 / 仍在回收站），可重试
#   conflict → 恢复时目标冲突且策略为跳过
ITEM_STATES = ("pending", "moving", "trashed", "restored", "purged",
               "missing", "failed", "conflict")
# 批次状态
BATCH_STATES = ("pending", "done", "partial", "failed")
VALID_OP = ("delete", "restore", "purge")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);

CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY,
  lib_id   TEXT    NOT NULL DEFAULT '',
  op       TEXT    NOT NULL DEFAULT '',
  created  INTEGER NOT NULL DEFAULT 0,
  total    INTEGER NOT NULL DEFAULT 0,
  ok       INTEGER NOT NULL DEFAULT 0,
  failed   INTEGER NOT NULL DEFAULT 0,
  status   TEXT    NOT NULL DEFAULT 'pending',
  note     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_batches_status ON batches(status);
CREATE INDEX IF NOT EXISTS ix_batches_lib    ON batches(lib_id, created);

CREATE TABLE IF NOT EXISTS items (
  item_id    TEXT PRIMARY KEY,
  batch_id   TEXT    NOT NULL DEFAULT '',
  lib_id     TEXT    NOT NULL DEFAULT '',
  orig_rel   TEXT    NOT NULL DEFAULT '',
  trash_rel  TEXT    NOT NULL DEFAULT '',
  name       TEXT    NOT NULL DEFAULT '',
  kind       TEXT    NOT NULL DEFAULT '',
  is_video   INTEGER NOT NULL DEFAULT 0,
  size       INTEGER NOT NULL DEFAULT 0,
  mtime      REAL    NOT NULL DEFAULT 0,
  group_key  TEXT    NOT NULL DEFAULT '',
  pair_role  TEXT    NOT NULL DEFAULT 'solo',
  live       INTEGER NOT NULL DEFAULT 0,
  deleted_at INTEGER NOT NULL DEFAULT 0,
  expire_at  INTEGER NOT NULL DEFAULT 0,
  status     TEXT    NOT NULL DEFAULT 'pending',
  restored_at INTEGER NOT NULL DEFAULT 0,
  purged_at   INTEGER NOT NULL DEFAULT 0,
  new_rel     TEXT    NOT NULL DEFAULT '',
  error       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_items_status ON items(status);
CREATE INDEX IF NOT EXISTS ix_items_expire ON items(status, expire_at);
CREATE INDEX IF NOT EXISTS ix_items_src    ON items(lib_id, orig_rel);
CREATE INDEX IF NOT EXISTS ix_items_group  ON items(group_key);
CREATE INDEX IF NOT EXISTS ix_items_batch  ON items(batch_id);

CREATE TABLE IF NOT EXISTS batch_items (
  batch_id TEXT NOT NULL,
  item_id  TEXT NOT NULL,
  PRIMARY KEY (batch_id, item_id)
);

CREATE TABLE IF NOT EXISTS audit (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       INTEGER NOT NULL DEFAULT 0,
  op       TEXT    NOT NULL DEFAULT '',
  lib_id   TEXT    NOT NULL DEFAULT '',
  batch_id TEXT    NOT NULL DEFAULT '',
  n        INTEGER NOT NULL DEFAULT 0,
  result   TEXT    NOT NULL DEFAULT '',
  detail   TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts);
"""

_ITEM_COLS = ("lib_id", "orig_rel", "trash_rel", "name", "kind", "is_video",
              "size", "mtime", "group_key", "pair_role", "live",
              "deleted_at", "expire_at", "status", "restored_at",
              "purged_at", "new_rel", "error")


def make_id(prefix):
    """生成人类可读、有序、唯一的批次/条目 ID。"""
    return f"{prefix}-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


class Trash:
    """回收站账本。线程安全；写操作自带 commit。"""

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

    def close(self):
        try:
            with self._lock:
                self.conn.commit()
                self.conn.close()
        except sqlite3.Error:
            pass

    # ---------------- meta ----------------
    def get_meta(self, k, default=None):
        with self._lock:
            r = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k, v):
        with self._lock:
            self.conn.execute(
                "INSERT INTO meta(k, v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
            self.conn.commit()

    # ---------------- 批次 ----------------
    def create_batch(self, batch_id, lib_id, op, total, note=""):
        if op not in VALID_OP:
            raise ValueError(f"bad op: {op}")
        with self._lock:
            self.conn.execute(
                "INSERT INTO batches(batch_id, lib_id, op, created, total, "
                "ok, failed, status, note) VALUES(?,?,?,?,?,0,0,'pending',?)",
                (batch_id, lib_id, op, int(time.time()), int(total), note))
            self.conn.commit()

    def finish_batch(self, batch_id, ok, failed, status, note=None):
        if status not in BATCH_STATES:
            status = "partial" if failed else "done"
        with self._lock:
            if note is None:
                self.conn.execute(
                    "UPDATE batches SET ok=?, failed=?, status=? WHERE batch_id=?",
                    (int(ok), int(failed), status, batch_id))
            else:
                self.conn.execute(
                    "UPDATE batches SET ok=?, failed=?, status=?, note=? "
                    "WHERE batch_id=?", (int(ok), int(failed), status, note, batch_id))
            self.conn.commit()

    def batch(self, batch_id):
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        return dict(r) if r else None

    def pending_batches(self):
        """未收尾的批次（status='pending'）—— 崩溃恢复的入口。"""
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM batches WHERE status='pending' ORDER BY created")
            return [dict(r) for r in cur]

    def list_batches(self, lib_id=None, op=None, limit=100):
        sql = "SELECT * FROM batches WHERE 1=1"
        args = []
        if lib_id:
            sql += " AND lib_id=?"; args.append(lib_id)
        if op:
            sql += " AND op=?"; args.append(op)
        sql += " ORDER BY created DESC LIMIT ?"; args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args)]

    # ---------------- 条目 ----------------
    def add_items(self, rows):
        """rows: [{lib_id, orig_rel, trash_rel, name, kind, is_video, size,
        mtime, group_key, pair_role, live, deleted_at, expire_at, status,...}]"""
        if not rows:
            return []
        payload, ids = [], []
        with self._lock:
            for r in rows:
                iid = r.get("item_id") or make_id("it")
                ids.append(iid)
                payload.append((
                    iid, r.get("batch_id", ""), r.get("lib_id", ""),
                    r.get("orig_rel", ""), r.get("trash_rel", ""),
                    r.get("name", ""), r.get("kind", ""),
                    1 if r.get("is_video") else 0, int(r.get("size") or 0),
                    float(r.get("mtime") or 0), r.get("group_key", ""),
                    r.get("pair_role", "solo"), 1 if r.get("live") else 0,
                    int(r.get("deleted_at") or 0), int(r.get("expire_at") or 0),
                    r.get("status", "pending"), int(r.get("restored_at") or 0),
                    int(r.get("purged_at") or 0), r.get("new_rel", ""),
                    r.get("error", ""),
                ))
            self.conn.executemany(
                "INSERT INTO items(item_id, batch_id, lib_id, orig_rel, "
                "trash_rel, name, kind, is_video, size, mtime, group_key, "
                "pair_role, live, deleted_at, expire_at, status, restored_at, "
                "purged_at, new_rel, error) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
            self.conn.commit()
        return ids

    def item(self, item_id):
        with self._lock:
            r = self.conn.execute("SELECT * FROM items WHERE item_id=?",
                                  (item_id,)).fetchone()
        return dict(r) if r else None

    def set_item(self, item_id, **kw):
        cols = [c for c in kw if c in _ITEM_COLS]
        if not cols:
            return
        sets = ", ".join(f"{c}=?" for c in cols)
        with self._lock:
            self.conn.execute(f"UPDATE items SET {sets} WHERE item_id=?",
                              (*[kw[c] for c in cols], item_id))
            self.conn.commit()

    def link(self, batch_id, item_ids):
        item_ids = list(item_ids)
        if not item_ids:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT OR IGNORE INTO batch_items(batch_id, item_id) VALUES(?,?)",
                [(batch_id, i) for i in item_ids])
            self.conn.commit()

    def items_of_batch(self, batch_id):
        with self._lock:
            cur = self.conn.execute(
                "SELECT i.* FROM items i JOIN batch_items b ON b.item_id=i.item_id "
                "WHERE b.batch_id=? ORDER BY i.orig_rel", (batch_id,))
            return [dict(r) for r in cur]

    def list_items(self, lib_id=None, status="trashed"):
        """status 可以是单个状态，也可以是状态元组。
        回收站界面用 ("trashed","failed","conflict","missing")：失败/冲突/丢失的条目
        也必须可见、可处理，否则它们会变成"看不见又清不掉"的幽灵记录。"""
        if isinstance(status, str):
            states = [status]
        else:
            states = list(status)
        q = ",".join("?" * len(states))
        sql = f"SELECT * FROM items WHERE status IN ({q})"
        args = list(states)
        if lib_id:
            sql += " AND lib_id=?"; args.append(lib_id)
        sql += " ORDER BY deleted_at DESC, orig_rel"
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args)]

    def items_by_ids(self, item_ids):
        item_ids = list(item_ids)
        if not item_ids:
            return []
        out = []
        with self._lock:
            for i in range(0, len(item_ids), 400):
                chunk = item_ids[i:i + 400]
                q = ",".join("?" * len(chunk))
                cur = self.conn.execute(
                    f"SELECT * FROM items WHERE item_id IN ({q})", chunk)
                out.extend(dict(r) for r in cur)
        return out

    def is_trashed(self, lib_id, rel):
        """该 (lib, rel) 是否正躺在回收站里（供缓存 GC 做保护判定）。"""
        with self._lock:
            r = self.conn.execute(
                "SELECT 1 FROM items WHERE lib_id=? AND orig_rel=? "
                "AND status='trashed' LIMIT 1", (lib_id, rel)).fetchone()
        return r is not None

    def expired(self, now=None):
        now = int(now if now is not None else time.time())
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM items WHERE status='trashed' AND expire_at>0 "
                "AND expire_at<=? ORDER BY expire_at", (now,))
            return [dict(r) for r in cur]

    def purge_rows(self, item_ids):
        """物理清账（永久删除成功后把行删掉）。测试/维护用。"""
        item_ids = list(item_ids)
        if not item_ids:
            return 0
        n = 0
        with self._lock:
            for i in range(0, len(item_ids), 400):
                chunk = item_ids[i:i + 400]
                q = ",".join("?" * len(chunk))
                cur = self.conn.execute(
                    f"DELETE FROM items WHERE item_id IN ({q})", chunk)
                n += cur.rowcount or 0
            self.conn.commit()
        return n

    # ---------------- 审计 ----------------
    def audit(self, op, lib_id, batch_id, n, result, detail=""):
        with self._lock:
            self.conn.execute(
                "INSERT INTO audit(ts, op, lib_id, batch_id, n, result, detail) "
                "VALUES(?,?,?,?,?,?,?)",
                (int(time.time()), op, lib_id or "", batch_id or "",
                 int(n or 0), result or "", detail or ""))
            self.conn.commit()

    def audit_tail(self, limit=100):
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (int(limit),))
            return [dict(r) for r in cur]

    # ---------------- 统计 ----------------
    def stats(self, lib_id=None):
        with self._lock:
            if lib_id:
                r = self.conn.execute(
                    "SELECT COUNT(*) c, COALESCE(SUM(size),0) b FROM items "
                    "WHERE status='trashed' AND lib_id=?", (lib_id,)).fetchone()
                e = self.conn.execute(
                    "SELECT COUNT(*) c FROM items WHERE status='trashed' "
                    "AND expire_at>0 AND expire_at<=? AND lib_id=?",
                    (int(time.time()), lib_id)).fetchone()["c"]
                stuck = self.conn.execute(
                    "SELECT COUNT(*) c FROM items WHERE lib_id=? AND status IN "
                    "('failed','conflict','missing')", (lib_id,)).fetchone()["c"]
            else:
                r = self.conn.execute(
                    "SELECT COUNT(*) c, COALESCE(SUM(size),0) b FROM items "
                    "WHERE status='trashed'").fetchone()
                e = self.conn.execute(
                    "SELECT COUNT(*) c FROM items WHERE status='trashed' "
                    "AND expire_at>0 AND expire_at<=?", (int(time.time()),)
                ).fetchone()["c"]
                stuck = self.conn.execute(
                    "SELECT COUNT(*) c FROM items WHERE status IN "
                    "('failed','conflict','missing')").fetchone()["c"]
            by_lib = {row["lib_id"]: {"items": row["c"], "bytes": row["b"]}
                      for row in self.conn.execute(
                          "SELECT lib_id, COUNT(*) c, COALESCE(SUM(size),0) b "
                          "FROM items WHERE status='trashed' GROUP BY lib_id")}
        size = 0
        for p in (self.path, self.path + "-wal", self.path + "-shm"):
            try:
                size += os.path.getsize(p)
            except OSError:
                pass
        return {"dbPath": self.path, "dbMB": round(size / 1024 / 1024, 2),
                "items": r["c"], "bytes": r["b"], "expired": e, "stuck": stuck,
                "byLib": by_lib, "schemaVersion": SCHEMA_VERSION}
