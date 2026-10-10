#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · SQLite 持久化索引
============================
把「目录扫描结果」与「Live Photo 配对结果」落盘，实现：

  · 首次使用      → 全量扫描并建立索引
  · 日常启动      → 只做增量核对（新增 / 删除 / 修改）
  · CID 提取      → 只有新文件或变化文件才重新解析 MOV / 提取 Content Identifier
  · 根目录离线    → 保留原索引，不当作空图库清空
  · 多图库隔离    → 身份由 (lib_id, rel) 唯一确定，不同目录同名文件互不冲突

表结构
------
libs   (lib_id PK, name, root, count, live, 统计与扫描状态...)
files  (lib_id, rel) PK   每个媒体文件一行；图片、视频都在这
pairs  (lib_id, img_rel) PK   图片 ↔ 视频 配对结果（派生表，可随时重建）

派生字段缓存（避免每次启动重复解析）：
  files.mov_checked / cid / live_markers   —— MOV 视频侧
  files.img_checked / live_ids             —— 图片侧（Content Identifier）
  files.codec                              —— 视频码流（h264 / hevc / ...）
"""

import os
import json
import time
import sqlite3
import threading

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);

CREATE TABLE IF NOT EXISTS libs (
  lib_id        TEXT PRIMARY KEY,
  name          TEXT,
  root          TEXT,
  count         INTEGER DEFAULT 0,
  live          INTEGER DEFAULT 0,
  scanned       INTEGER DEFAULT 0,     -- 本次核对的文件总数
  added         INTEGER DEFAULT 0,     -- 新增
  changed       INTEGER DEFAULT 0,     -- 修改
  removed       INTEGER DEFAULT 0,     -- 删除
  mov_parsed    INTEGER DEFAULT 0,     -- 本次实际重新解析的 MOV 数量
  last_scan_ms  INTEGER DEFAULT 0,
  last_scan_at  INTEGER DEFAULT 0,
  last_full_at  INTEGER DEFAULT 0,
  root_exists   INTEGER DEFAULT 1,
  stale         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS files (
  lib_id       TEXT NOT NULL,
  rel          TEXT NOT NULL,          -- 相对路径（正斜杠）
  name         TEXT NOT NULL,
  dir          TEXT NOT NULL,          -- 所在子目录（配对用）
  stem         TEXT NOT NULL,          -- 主名（配对用）
  ext          TEXT,                   -- 清洗后的扩展名
  kind         TEXT NOT NULL,          -- 'img' | 'mov'
  size         INTEGER NOT NULL,
  mtime        REAL NOT NULL,
  ts           INTEGER NOT NULL,       -- 时间轴显示时间（epoch 秒）
  -- 视频侧缓存
  mov_checked  INTEGER DEFAULT 0,
  cid          TEXT,
  live_markers INTEGER DEFAULT 0,
  codec        TEXT,                   -- NULL=未探测, ''=探测为未知
  -- 图片侧缓存
  img_checked  INTEGER DEFAULT 0,
  live_ids     TEXT,
  PRIMARY KEY (lib_id, rel)
);

CREATE INDEX IF NOT EXISTS ix_files_pair ON files(lib_id, dir, stem);
CREATE INDEX IF NOT EXISTS ix_files_kind ON files(lib_id, kind);

CREATE TABLE IF NOT EXISTS pairs (
  lib_id   TEXT NOT NULL,
  img_rel  TEXT NOT NULL,
  mov_rel  TEXT NOT NULL,
  live     INTEGER NOT NULL DEFAULT 0,
  method   TEXT NOT NULL DEFAULT 'none',
  PRIMARY KEY (lib_id, img_rel)
);

CREATE INDEX IF NOT EXISTS ix_pairs_mov ON pairs(lib_id, mov_rel);
"""


class Index:
    """线程安全的 SQLite 索引封装（服务端只在一个后台线程里写，读走内存）。"""

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

    # ---------------- 图库元信息 ----------------
    def lib_meta(self, lib_id):
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM libs WHERE lib_id=?", (lib_id,)).fetchone()
        return dict(r) if r else None

    def touch_lib(self, lib_id, name, root):
        with self._lock:
            self.conn.execute(
                "INSERT INTO libs(lib_id, name, root) VALUES(?,?,?) "
                "ON CONFLICT(lib_id) DO UPDATE SET name=excluded.name, root=excluded.root",
                (lib_id, name, root))
            self.conn.commit()

    def save_lib_stats(self, lib_id, **kw):
        if not kw:
            return
        cols = ", ".join(f"{k}=?" for k in kw)
        with self._lock:
            self.conn.execute(f"UPDATE libs SET {cols} WHERE lib_id=?",
                              (*kw.values(), lib_id))
            self.conn.commit()

    # ---------------- 文件行 ----------------
    def all_files(self, lib_id):
        """返回 {rel: {dir, stem, ext, kind, size, mtime}}，供增量核对比对。"""
        out = {}
        with self._lock:
            cur = self.conn.execute(
                "SELECT rel, dir, stem, ext, kind, size, mtime FROM files WHERE lib_id=?",
                (lib_id,))
            for r in cur:
                out[r["rel"]] = {"dir": r["dir"], "stem": r["stem"], "ext": r["ext"],
                                 "kind": r["kind"], "size": r["size"],
                                 "mtime": r["mtime"]}
        return out

    def file_row(self, lib_id, rel):
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM files WHERE lib_id=? AND rel=?", (lib_id, rel)).fetchone()
        return dict(r) if r else None

    def iterate_files(self, lib_id):
        """返回 [(rel, name, mtime)]，供「时间戳取值链升级后重新推导 ts」使用。"""
        with self._lock:
            cur = self.conn.execute(
                "SELECT rel, name, mtime FROM files WHERE lib_id=?", (lib_id,))
            return [(r["rel"], r["name"], r["mtime"]) for r in cur]

    def set_ts_many(self, lib_id, pairs):
        """pairs: [(ts, rel)] —— 批量更新 files.ts。"""
        pairs = list(pairs)
        if not pairs:
            return 0
        with self._lock:
            self.conn.executemany(
                "UPDATE files SET ts=? WHERE lib_id=? AND rel=?",
                [(int(ts), lib_id, rel) for ts, rel in pairs])
            self.conn.commit()
        return len(pairs)

    def upsert_files(self, rows):
        """rows: 可迭代的 dict，键同 files 表列（缺失列用默认值）。"""
        if not rows:
            return 0
        cols = ("lib_id", "rel", "name", "dir", "stem", "ext", "kind", "size",
                "mtime", "ts", "mov_checked", "cid", "live_markers", "codec",
                "img_checked", "live_ids")
        setc = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("lib_id", "rel"))
        sql = (f"INSERT INTO files({','.join(cols)}) "
               f"VALUES({','.join('?' * len(cols))}) "
               f"ON CONFLICT(lib_id, rel) DO UPDATE SET {setc}")
        payload = []
        for r in rows:
            payload.append((
                r["lib_id"], r["rel"], r["name"], r["dir"], r["stem"], r.get("ext"),
                r["kind"], int(r["size"]), float(r["mtime"]), int(r["ts"]),
                int(r.get("mov_checked", 0)), r.get("cid"),
                int(r.get("live_markers", 0) or 0), r.get("codec"),
                int(r.get("img_checked", 0)), r.get("live_ids"),
            ))
        with self._lock:
            self.conn.executemany(sql, payload)
            self.conn.commit()
        return len(payload)

    def delete_files(self, lib_id, rels):
        rels = list(rels)
        if not rels:
            return 0
        with self._lock:
            for i in range(0, len(rels), 400):
                chunk = rels[i:i + 400]
                q = ",".join("?" * len(chunk))
                self.conn.execute(
                    f"DELETE FROM files WHERE lib_id=? AND rel IN ({q})",
                    (lib_id, *chunk))
            self.conn.commit()
        return len(rels)

    # ---------------- 分组（配对用） ----------------
    def group(self, lib_id, d, stem):
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM files WHERE lib_id=? AND dir=? AND stem=?",
                (lib_id, d, stem))
            return [dict(r) for r in cur]

    def set_mov_meta(self, lib_id, rel, cid, live_markers):
        with self._lock:
            self.conn.execute(
                "UPDATE files SET mov_checked=1, cid=?, live_markers=? "
                "WHERE lib_id=? AND rel=?",
                (cid, 1 if live_markers else 0, lib_id, rel))
            self.conn.commit()

    def set_img_meta(self, lib_id, rel, ids):
        with self._lock:
            self.conn.execute(
                "UPDATE files SET img_checked=1, live_ids=? WHERE lib_id=? AND rel=?",
                (",".join(sorted(ids)) if ids else "", lib_id, rel))
            self.conn.commit()

    def set_codec(self, lib_id, rel, codec):
        with self._lock:
            self.conn.execute(
                "UPDATE files SET codec=? WHERE lib_id=? AND rel=?",
                (codec, lib_id, rel))
            self.conn.commit()

    def movies_missing_codec(self, lib_id, unclaimed_only=True):
        sql = ("SELECT f.rel FROM files f WHERE f.lib_id=? AND f.kind='mov' "
               "AND f.codec IS NULL")
        if unclaimed_only:
            sql += (" AND NOT EXISTS(SELECT 1 FROM pairs p "
                    "WHERE p.lib_id=f.lib_id AND p.mov_rel=f.rel)")
        with self._lock:
            return [r["rel"] for r in self.conn.execute(sql, (lib_id,))]

    def count_unclaimed_movies(self, lib_id):
        """独立视频数（未被任何静图认领的 mov/mp4）—— 供进度面板的总览统计。"""
        with self._lock:
            r = self.conn.execute(
                "SELECT COUNT(*) c FROM files f WHERE f.lib_id=? AND f.kind='mov' "
                "AND NOT EXISTS(SELECT 1 FROM pairs p "
                "               WHERE p.lib_id=f.lib_id AND p.mov_rel=f.rel)",
                (lib_id,)).fetchone()
        return int(r["c"] if r else 0)

    # ---------------- 配对表 ----------------
    def pairs(self, lib_id):
        with self._lock:
            cur = self.conn.execute(
                "SELECT img_rel, mov_rel, live, method FROM pairs WHERE lib_id=?",
                (lib_id,))
            return {r["img_rel"]: dict(r) for r in cur}

    def replace_group_pairs(self, lib_id, img_rels, new_pairs):
        """先删掉这些图片的旧配对，再写入新配对（同一事务）。"""
        with self._lock:
            try:
                self.conn.execute("BEGIN")
                if img_rels:
                    for i in range(0, len(img_rels), 400):
                        chunk = list(img_rels)[i:i + 400]
                        q = ",".join("?" * len(chunk))
                        self.conn.execute(
                            f"DELETE FROM pairs WHERE lib_id=? AND img_rel IN ({q})",
                            (lib_id, *chunk))
                if new_pairs:
                    self.conn.executemany(
                        "INSERT OR REPLACE INTO pairs(lib_id, img_rel, mov_rel, live, method) "
                        "VALUES(?,?,?,?,?)",
                        [(lib_id, p["img_rel"], p["mov_rel"], p["live"], p["method"])
                         for p in new_pairs])
                self.conn.commit()
            except sqlite3.Error:
                self.conn.rollback()
                raise

    def drop_pairs_for_movies(self, lib_id, mov_rels):
        mov_rels = list(mov_rels)
        if not mov_rels:
            return
        with self._lock:
            for i in range(0, len(mov_rels), 400):
                chunk = mov_rels[i:i + 400]
                q = ",".join("?" * len(chunk))
                self.conn.execute(
                    f"DELETE FROM pairs WHERE lib_id=? AND mov_rel IN ({q})",
                    (lib_id, *chunk))
            self.conn.commit()

    # ---------------- 全量重置 ----------------
    def reset_lib(self, lib_id):
        with self._lock:
            self.conn.execute("DELETE FROM pairs WHERE lib_id=?", (lib_id,))
            self.conn.execute("DELETE FROM files WHERE lib_id=?", (lib_id,))
            self.conn.commit()

    def forget_lib(self, lib_id):
        with self._lock:
            self.conn.execute("DELETE FROM pairs WHERE lib_id=?", (lib_id,))
            self.conn.execute("DELETE FROM files WHERE lib_id=?", (lib_id,))
            self.conn.execute("DELETE FROM libs WHERE lib_id=?", (lib_id,))
            self.conn.commit()

    # ---------------- 构建内存索引 ----------------
    def build_index(self, lib_id):
        """
        从 files + pairs 重建内存视图，返回
          (items, months, day_order, by_day, n_live)
        items 按时间倒序；结构与原实现完全一致（前端无需改动）。
        """
        items = []
        with self._lock:
            cur = self.conn.execute(
                "SELECT f.rel AS rel, f.name AS name, f.ts AS ts, f.size AS size, "
                "       p.mov_rel AS mv, p.live AS live, p.method AS method "
                "FROM files f "
                "LEFT JOIN pairs p ON p.lib_id=f.lib_id AND p.img_rel=f.rel "
                "WHERE f.lib_id=? AND f.kind='img'", (lib_id,))
            for r in cur:
                items.append({"p": r["rel"], "n": r["name"], "t": r["ts"],
                              "z": r["size"], "k": "img",
                              "lv": int(r["live"] or 0), "pr": r["method"] or "none",
                              "mv": r["mv"], "c": ""})
            cur = self.conn.execute(
                "SELECT f.rel AS rel, f.name AS name, f.ts AS ts, f.size AS size, "
                "       f.codec AS codec "
                "FROM files f WHERE f.lib_id=? AND f.kind='mov' "
                "AND NOT EXISTS(SELECT 1 FROM pairs p "
                "               WHERE p.lib_id=f.lib_id AND p.mov_rel=f.rel)",
                (lib_id,))
            for r in cur:
                items.append({"p": r["rel"], "n": r["name"], "t": r["ts"],
                              "z": r["size"], "k": "vid", "lv": 0, "pr": "none",
                              "mv": r["rel"], "c": r["codec"] or ""})

        # 时间的“并列”必须有稳定的次序：否则删除/恢复过一次后，索引行插入顺序变化
        # 会让同秒的照片在时间轴上换位（同一批导入的实况照片尤其常见）。
        # 这里以 rel 作为次级键，保证「相同时间戳 → 同一顺序」。
        items.sort(key=lambda x: (-x["t"], x["p"]))

        import collections
        by_day = collections.defaultdict(list)
        day_order = []
        for it in items:
            lt = time.localtime(it["t"])
            ym = time.strftime("%Y-%m", lt)
            dd = time.strftime("%d", lt)
            key = ym + "|" + dd
            if key not in by_day:
                day_order.append(key)
            by_day[key].append(it)

        months = collections.OrderedDict()
        for key in day_order:
            ym, dd = key.split("|")
            m = months.setdefault(ym, {"ym": ym, "n": 0, "days": []})
            m["n"] += len(by_day[key])
            m["days"].append({"d": dd, "n": len(by_day[key]),
                              "ts": by_day[key][0]["t"]})

        n_live = sum(1 for it in items if it["lv"] == 1)
        return items, list(months.values()), day_order, dict(by_day), n_live

    def stats(self):
        with self._lock:
            files = self.conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
            pairs = self.conn.execute("SELECT COUNT(*) c FROM pairs").fetchone()["c"]
            libs = self.conn.execute("SELECT COUNT(*) c FROM libs").fetchone()["c"]
        size = 0
        for p in (self.path, self.path + "-wal", self.path + "-shm"):
            try:
                size += os.path.getsize(p)
            except OSError:
                pass
        return {"dbPath": self.path, "dbMB": round(size / 1024 / 1024, 2),
                "rows": files, "pairs": pairs, "libs": libs,
                "schemaVersion": SCHEMA_VERSION}

    def sync_probe_version(self, version):
        """
        码流探测算法升级时，作废全部已缓存的 codec，下次扫描重新探测。
        返回 True 表示本次确实做了失效处理。
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT v FROM meta WHERE k='codec_probe_version'").fetchone()
            if row and row["v"] == str(version):
                return False
            self.conn.execute("UPDATE files SET codec=NULL")
            self.conn.execute(
                "INSERT INTO meta(k, v) VALUES('codec_probe_version', ?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(version),))
            self.conn.commit()
        return True

    # ---------------- meta 读写 ----------------
    def meta_get(self, k, default=None):
        with self._lock:
            r = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def meta_set(self, k, v):
        with self._lock:
            self.conn.execute(
                "INSERT INTO meta(k, v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
            self.conn.commit()

    def needs_ts_refresh(self, version):
        """
        时间戳取值链升级？只读判断，不改状态。
        （真正的重新推导由服务端做，全部成功后再调 mark_ts_version，
          这样中途中断也不会把升级标记吞掉。）
        """
        return self.meta_get("ts_probe_version") != str(version)

    def mark_ts_version(self, version):
        self.meta_set("ts_probe_version", version)
