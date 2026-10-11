#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回收站（阶段一~三）专项测试
============================
在**完全独立的测试树**里跑：不碰真实三库、不碰真实 data/、不启用任何真实图库的删除权限。

覆盖（对应需求 §九 + 阶段五 + 四项专项）：
  1  普通图片删除 → 从时间线/索引消失、文件进回收站、原路径消失
  2  Live Photo 图片 + 配对 MOV 成组删除 与 完整恢复（配对关系复原）
  3  独立视频删除及恢复
  4  30 天内不可被自动永久删除（expire_at 正确、expired() 为空）
  5  到期项目会被 expired() 识别 → 阶段五永久删除
  6  崩溃恢复：未收尾批次按"文件现在在哪"对账，不静默丢文件/记录
  7  恢复冲突不覆盖（skip 整组跳过 / rename 整组改名保持配对）
  8  部分失败可识别、不静默丢（不存在的条目 / 盘上已消失的文件）
  9  图库离线 → 拒绝管理
 10  只读图库 / 未开启 → 拒绝管理
 11  未配置口令 / 无会话 / 缺 CSRF 头 / 跨站 → 拒绝管理（mgmt_guard 四道闸门）
 12  路径穿越 / 绝对路径 / 符号链接·junction → 拒绝
 13  删除 / 恢复后 索引 / 配对 / 磁盘 一致
 14  缓存孤儿回收不触及源文件与回收站文件（trashed 保护档）
 P1~P6 阶段五：到期自动永久删除（含未到期不动 / 幂等 / 审计）
 专项1 崩溃恢复时 原路径与回收站路径同时存在（delete / restore 两态）
 专项2 原路径存在同名替代文件时，不得仅凭路径存在就认定为原文件
 专项3 Live 组多文件移动/恢复中任一成员失败 → 批次/重试/状态一致
 专项4 永久删除中断后重启 → 不误删其它文件、不丢审计
 E1~E5 永久删除的越界防护 + 清空回收站语义 + 与缓存回收互不干扰
"""
import os
import sys
import time
import shutil
import hashlib
import subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import server as S
import trash_db
import index_db
import cache_db

T = os.path.join(HERE, "tmp", "trash_test")
SRC = os.path.join(T, "src")
CACHE = os.path.join(T, "cache")
TRASH = os.path.join(T, "trash")

PASS = FAIL = SKIP = 0
UUID_A = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
UUID_C = "12345678-1234-1234-1234-1234567890AB"


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2713 {name}" + (f"  [{extra}]" if extra else ""))
    else:
        FAIL += 1
        print(f"  \u2717 {name}  \u2190 FAIL  [{extra}]")


def skip(name, why):
    global SKIP
    SKIP += 1
    print(f"  ~ {name}  [SKIP: {why}]")


def w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def snap(root):
    out = {}
    for d, _s, fs in os.walk(root):
        for n in fs:
            p = os.path.join(d, n)
            out[os.path.relpath(p, root).replace("\\", "/")] = sha(p)
    return out


def make_junction(link, target):
    try:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           capture_output=True, errors="replace")
        return r.returncode == 0 and os.path.isdir(link)
    except Exception:
        return False


def L():
    return S.META["libs"]["lib_t"]


def add_file(rel, data=b"DATA" * 40):
    """把一个文件加进磁盘 + 索引（模拟"库里本来就有的文件"）。"""
    p = os.path.join(SRC, rel.replace("/", os.sep))
    w(p, data)
    st = os.stat(p)
    name = os.path.basename(rel)
    ext, kind = S.classify(rel, name, SRC)
    S.INDEX.upsert_files([{
        "lib_id": "lib_t", "rel": rel, "name": name,
        "dir": os.path.dirname(rel), "stem": S.clean_stem(name),
        "ext": ext, "kind": kind, "size": st.st_size, "mtime": st.st_mtime,
        "ts": S.parse_ts(name) or int(st.st_mtime),
        "mov_checked": 0, "cid": None, "live_markers": 0, "codec": None,
        "img_checked": 0, "live_ids": None}])
    S.sync_memory(L())
    return p


def drop_file(rel):
    """从磁盘 + 索引移除（清理测试样本）。"""
    p = os.path.join(SRC, rel.replace("/", os.sep))
    if os.path.isfile(p):
        os.remove(p)
    S.INDEX.delete_files("lib_t", [rel])
    S.sync_memory(L())


def setup():
    if os.path.isdir(T):
        shutil.rmtree(T)
    for d in (SRC, CACHE, TRASH, os.path.join(SRC, "sub")):
        os.makedirs(d, exist_ok=True)

    w(os.path.join(SRC, "a.heic"), b"FTYPHEIC" + UUID_A.encode())
    w(os.path.join(SRC, "a.mov"),
      b"\x00" * 24 + b"com.apple.quicktime.content.identifier" + UUID_A.encode()
      + b"\x00" * 8 + b"com.apple.quicktime.live-photo")
    w(os.path.join(SRC, "b.jpg"), b"JPEGDATA" * 64)
    w(os.path.join(SRC, "clip.mp4"), b"MP4STANDALONE" * 128)
    w(os.path.join(SRC, "sub", "c.heic"), b"FTYPHEIC" + UUID_C.encode())
    w(os.path.join(SRC, "sub", "c.mov"),
      b"com.apple.quicktime.content.identifier" + UUID_C.encode())

    S.TRASH_DIR = TRASH
    S.TRASH_DB = os.path.join(T, "trash.db")
    S.TRASH = trash_db.Trash(S.TRASH_DB)
    S.INDEX = index_db.Index(os.path.join(T, "index.db"))
    S.CACHE_INDEX = cache_db.CacheIndex(os.path.join(T, "cache.db"))
    S.CACHE_DIR = CACHE
    S.META = {
        "libs": {"lib_t": {
            "id": "lib_t", "name": "测试库", "root": SRC,
            "cfg": {"id": "lib_t", "name": "测试库", "root": SRC,
                    "deletion_enabled": True},
            "readonly": False, "deletion_enabled": True,
            "exists": True, "stale": False, "ignore": [], "_ignoreHidden": True,
            "count": 0, "live": 0, "items": [], "by_day": {},
            "day_order": [], "months": [], "scan_ms": 0,
        }},
        "lib_order": ["lib_t"],
        "scan": dict(S.DEFAULT_SCAN),
        "trash": dict(S.DEFAULT_TRASH),
    }
    S.scan_library(L(), full=True)


def main():
    print("=" * 70)
    print("  回收站（阶段一~三）专项测试 · 独立测试树 tmp/trash_test/")
    print("=" * 70)
    setup()
    lib = L()
    items0 = lib["count"]
    files0 = len(snap(SRC))

    # 记录**真实** data/trash 的初始状态：本套件跑在独立测试树里，结束时它必须与此刻
    # **完全一致**。刻意不假设它本来是空的 —— 用户可能真的删过照片（那是可恢复的真实数据）。
    real_trash = os.path.join(HERE, "data", "trash")

    def _real_trash_listing():
        return sorted(os.path.relpath(os.path.join(d, f), real_trash)
                      for d, _s, fs in os.walk(real_trash) for f in fs)

    real_trash_before = _real_trash_listing()
    print(f"[初始] 时间线条目 {items0} 个 / 磁盘文件 {files0} 个 / "
          f"Live {lib['live']} 组 / 配对 {len(S.INDEX.pairs('lib_t'))} 组")

    # ---------- 9/10/11 权限闸门 ----------
    print("\n[权限闸门]")
    lib["exists"] = False
    ok_off, why_off = S.manage_state(lib)
    lib["exists"] = True
    chk("9 图库离线 → 拒绝管理", ok_off is False, why_off)
    lib["cfg"]["readonly"] = True
    chk("10a 只读图库 → 拒绝管理", S.manage_state(lib)[0] is False)
    lib["cfg"]["readonly"] = False
    lib["cfg"]["deletion_enabled"] = False
    chk("10b 未开启 deletion_enabled → 拒绝", S.manage_state(lib)[0] is False)
    lib["cfg"]["deletion_enabled"] = True

    # ---------- 11 口令/会话闸门（已上移到 mgmt_guard，口径见 管理认证_独立化方案.md） ----------
    # manage_state() 现在**只回答"这个图库允不允许被管理"**（能力），
    # 「有没有配口令 / 有没有有效会话 / 是不是跨站」由 mgmt_guard() 在更外层判定。
    # 所以这里按真实调用链直测四道闸门，而不是再去戳一个已不存在的 META["token"]。
    class _H:
        """借用真实 Handler 的三个判定方法，只补一个 headers 与 admin_sid()。"""
        admin_authed   = S.Handler.admin_authed
        csrf_ok        = S.Handler.csrf_ok
        same_origin_ok = S.Handler.same_origin_ok

        def __init__(self, sid="", hdr=None, host="127.0.0.1:9000", origin=None):
            self.headers = {"Host": host}
            self._sid = sid or ""
            if hdr is not None:
                self.headers["X-SL-Admin"] = hdr
            if origin is not None:
                self.headers["Origin"] = origin

        def admin_sid(self):
            return self._sid

    def _guard(lib, **kw):
        ao = kw.pop("allow_offline", False)
        return S.Handler.mgmt_guard(_H(**kw), lib, allow_offline=ao)

    old_pw = S.ADMIN_PW
    S.ADMIN_PW = None
    chk("11a 未配置口令 → 拒绝管理（403，管理功能整体关闭）",
        _guard(lib)[0] == 403)
    S.ADMIN_PW = old_pw or "test-token"
    chk("11b 已配口令但无有效会话 → 401（前端据此弹授权框）",
        _guard(lib)[0] == 401)
    sid, _exp = S.admin_session_create()
    chk("11c 有会话但缺自定义头 → 403（CSRF 第一道）",
        _guard(lib, sid=sid)[0] == 403)
    chk("11d 有会话 + 有头但跨站 Origin → 403（CSRF 第二道）",
        _guard(lib, sid=sid, hdr="1", origin="http://evil.example.com")[0] == 403)
    code, why = _guard(lib, sid=sid, hdr="1")
    chk("11e 会话 + 头 + 同源 → 放行", code is None, why or "无阻拦")
    S.admin_session_drop(sid)
    chk("11f 会话注销后立即失效 → 又回到 401",
        _guard(lib, sid=sid, hdr="1")[0] == 401)
    chk("11g manage_state 只管图库能力，不再看口令（只管能力/不管授权）",
        S.manage_state(lib)[0] is True)

    # ---------- 12 路径安全 ----------
    print("\n[路径安全]")
    try:
        S.safe_resolve(SRC, "../escape.jpg")
        chk("12a 拒绝 ../ 穿越", False)
    except S.SecurityError:
        chk("12a 拒绝 ../ 穿越", True)
    try:
        S.safe_resolve(SRC, "E:/Windows/win.ini")
        chk("12b 拒绝绝对路径", False)
    except S.SecurityError:
        chk("12b 拒绝绝对路径", True)
    jlink = os.path.join(SRC, "jlink")
    if make_junction(jlink, T):
        chk("12c 识别并拒绝 junction", S._is_link_like(jlink) is True)
        walk = [r for (r, _n, _s, _m) in S.walk_files(SRC, ignore=set(), ignore_hidden=False)]
        chk("12d junction 内容未被扫描进索引",
            all(not r.startswith("jlink") for r in walk))
        try:
            os.rmdir(jlink)
        except OSError:
            pass
    else:
        skip("12c/d junction 用例", "本机无法创建 junction")

    # ---------- 1/4/5 普通图片 + 到期语义 ----------
    print("\n[普通图片删除 + 到期语义]")
    r = S.mgmt_delete(lib, ["b.jpg"])
    chk("1a 普通图片删除：批次成功", r["ok"] and r["moved"] == 1, f"moved={r['moved']}")
    it = S.TRASH.items_of_batch(r["batchId"])[0]
    chk("4a expire_at = deleted_at + 30 天",
        it["expire_at"] - it["deleted_at"] == 30 * 86400,
        f"{it['expire_at'] - it['deleted_at']}s")
    chk("4b 30 天内不被 expired() 识别",
        all(x["item_id"] != it["item_id"] for x in S.TRASH.expired()))
    chk("1b 原路径消失 / 回收站有档 / 索引已移除",
        (not os.path.exists(os.path.join(SRC, "b.jpg")))
        and os.path.isfile(S._abs_trash(it["trash_rel"]))
        and "b.jpg" not in S.INDEX.all_files("lib_t"))
    chk("1c 时间线条目数 -1", lib["count"] == items0 - 1, f"count={lib['count']}")
    S.TRASH.set_item(it["item_id"], expire_at=int(time.time()) - 1)
    chk("5 到期项目被 expired() 识别",
        any(x["item_id"] == it["item_id"] for x in S.TRASH.expired()))
    S.mgmt_restore(lib, items=[it["item_id"]])
    chk("1d 删后可完整恢复（回到原路径）",
        os.path.isfile(os.path.join(SRC, "b.jpg"))
        and "b.jpg" in S.INDEX.all_files("lib_t"))

    # ---------- 2 Live Photo 成组 ----------
    print("\n[Live Photo 成组删除 + 恢复]")
    rb = snap(SRC)
    r = S.mgmt_delete(lib, ["a.heic"])
    items = S.TRASH.items_of_batch(r["batchId"])
    chk("2a 只选图片 → 配对 MOV 一并移走",
        r["moved"] == 2 and sorted(x["kind"] for x in items) == ["img", "mov"],
        f"moved={r['moved']}")
    chk("2b 组标记 live=1，角色 photo/video",
        items[0]["live"] == 1 and {x["pair_role"] for x in items} == {"photo", "video"})
    chk("2c 两个文件都已离开源目录",
        not os.path.exists(os.path.join(SRC, "a.heic"))
        and not os.path.exists(os.path.join(SRC, "a.mov")))
    chk("2d 配对关系已从索引移除", "a.heic" not in S.INDEX.pairs("lib_t"))
    rr = S.mgmt_restore(lib, items=[x["item_id"] for x in items])
    chk("2e 整组恢复成功", rr["restored"] == 2, f"restored={rr['restored']}")
    chk("2f 恢复后源树逐字节一致", snap(SRC) == rb)
    pr = S.INDEX.pairs("lib_t").get("a.heic")
    chk("2g 配对关系复原（live=1 / method=cid）",
        bool(pr) and pr["live"] == 1 and pr["method"] == "cid", str(pr))

    # ---------- 3 独立视频 ----------
    print("\n[独立视频]")
    r = S.mgmt_delete(lib, ["clip.mp4"])
    vit = S.TRASH.items_of_batch(r["batchId"])[0]
    chk("3a 独立视频删除", r["moved"] == 1 and vit["pair_role"] == "solo"
        and vit["is_video"] == 1)
    S.mgmt_restore(lib, items=[vit["item_id"]])
    chk("3b 独立视频恢复且回到索引",
        os.path.isfile(os.path.join(SRC, "clip.mp4"))
        and "clip.mp4" in S.INDEX.all_files("lib_t"))

    # ---------- 7 恢复冲突 ----------
    print("\n[恢复冲突]")
    add_file("e.jpg", b"E-ORIGINAL-CONTENT" * 8)
    r = S.mgmt_delete(lib, ["e.jpg"])
    eit = S.TRASH.items_of_batch(r["batchId"])[0]
    conflict_bytes = b"CONFLICTING-NEW-FILE"
    w(os.path.join(SRC, "e.jpg"), conflict_bytes)
    rs = S.mgmt_restore(lib, items=[eit["item_id"]], on_conflict="skip")
    chk("7a skip：整组跳过且不覆盖",
        rs["skipped"] == 1
        and open(os.path.join(SRC, "e.jpg"), "rb").read() == conflict_bytes
        and S.TRASH.item(eit["item_id"])["status"] == "conflict")
    chk("7b skip 后回收站副本仍在", os.path.isfile(S._abs_trash(eit["trash_rel"])))
    rr = S.mgmt_restore(lib, items=[eit["item_id"]], on_conflict="rename")
    got = S.TRASH.item(eit["item_id"])
    chk("7c rename：恢复为新名，原冲突文件不被覆盖",
        rr["restored"] == 1 and got["new_rel"] == "e_1.jpg"
        and open(os.path.join(SRC, "e.jpg"), "rb").read() == conflict_bytes)
    drop_file("e.jpg")
    drop_file("e_1.jpg")

    # ---------- 8 部分失败 ----------
    print("\n[部分失败 / 不静默丢]")
    add_file("f.jpg")
    r = S.mgmt_delete(lib, ["f.jpg", "ghost.jpg"])
    chk("8a 不存在的条目进 errors，有效项照常移动",
        r["moved"] == 1 and any(e["p"] == "ghost.jpg" for e in r["errors"]),
        f"moved={r['moved']} errors={[e['p'] for e in r['errors']]}")
    add_file("h.jpg")
    add_file("i.jpg")
    os.remove(os.path.join(SRC, "i.jpg"))               # 索引有、盘上已没
    r2 = S.mgmt_delete(lib, ["h.jpg", "i.jpg"])
    chk("8b 盘上已消失的项被复核拦下（不误动）",
        r2["moved"] == 1 and any(e["p"] == "i.jpg" for e in r2["errors"]),
        f"moved={r2['moved']} errors={[e['p'] for e in r2['errors']]}")
    drop_file("i.jpg")

    # ---------- 6 崩溃恢复 ----------
    print("\n[崩溃恢复（先记录、后移动）]")
    bid = trash_db.make_id("del")
    S.TRASH.create_batch(bid, "lib_t", "delete", 1)
    tra = f"lib_t/{bid}/sub/c.heic"
    dst = S._abs_trash(tra)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(os.path.join(SRC, "sub", "c.heic"), dst)     # 文件已移动，状态未更新
    iid = S.TRASH.add_items([{
        "batch_id": bid, "lib_id": "lib_t", "orig_rel": "sub/c.heic",
        "trash_rel": tra, "name": "c.heic", "kind": "img", "is_video": 0,
        "size": 0, "mtime": 0, "group_key": S._group_key("lib_t", "sub/c.heic"),
        "pair_role": "photo", "live": 1, "status": "moving"}])[0]
    S.TRASH.link(bid, [iid])
    out = S.recover_trash()
    chk("6a 已移走的文件 → 对账为 trashed，批次收尾",
        S.TRASH.item(iid)["status"] == "trashed"
        and S.TRASH.batch(bid)["status"] in ("done", "partial")
        and out["batches"] >= 1)
    chk("6b 恢复后索引里已无该条目", "sub/c.heic" not in S.INDEX.all_files("lib_t"))
    S.mgmt_restore(lib, items=[iid])                     # 复原，便于后续一致性检查
    chk("6b2 复原后 sub/c 配对重建",
        "sub/c.heic" in S.INDEX.pairs("lib_t"))
    bid2 = trash_db.make_id("del")
    S.TRASH.create_batch(bid2, "lib_t", "delete", 1)
    iid2 = S.TRASH.add_items([{
        "batch_id": bid2, "lib_id": "lib_t", "orig_rel": "clip.mp4",
        "trash_rel": f"lib_t/{bid2}/clip.mp4", "name": "clip.mp4", "kind": "mov",
        "is_video": 1, "size": 0, "mtime": 0,
        "group_key": S._group_key("lib_t", "clip.mp4"),
        "pair_role": "solo", "status": "moving"}])[0]
    S.TRASH.link(bid2, [iid2])
    S.recover_trash()
    chk("6c 未移动的文件 → 对账为 failed，原文件安然无恙",
        S.TRASH.item(iid2)["status"] == "failed"
        and os.path.isfile(os.path.join(SRC, "clip.mp4")))

    # ---------- 13 索引一致性 ----------
    print("\n[索引一致性]")
    files = S.INDEX.all_files("lib_t")
    chk("13a 索引条目集合 = 磁盘实况",
        set(files.keys()) == set(snap(SRC).keys()),
        f"index={len(files)} disk={len(snap(SRC))}")
    chk("13b Live 配对数量正确（a + sub/c）", len(S.INDEX.pairs("lib_t")) == 2,
        f"pairs={len(S.INDEX.pairs('lib_t'))}")
    chk("13c 时间线条目数回到初始", lib["count"] == items0, f"count={lib['count']}")

    # ---------- 14 缓存回收边界 ----------
    print("\n[缓存回收不越界]")
    f = files["a.heic"]
    # ★ 用当前启用档（640）：本组只验证「源在回收站 → 受保护」，
    #   刻意用 640 以免与 obsolete_variant 类别混在一起（旧档保护另见
    #   tests/test_thumb_variant_gc.py 的专项用例）。
    h = S.cache_key("a.heic", f["mtime"], f["size"],
                    "thumbwebp" + str(S.THUMB_DEFAULT), "",
                    lib_id="lib_t")[2]
    cp = os.path.join(S.CACHE_DIR, h[:2], h[2:4], h + ".cache")
    w(cp, b"CACHE-PAYLOAD" * 20)
    S.CACHE_INDEX.record(h, "lib_t", "a.heic", f["mtime"], f["size"],
                         "thumb", "thumb|webp|" + str(S.THUMB_DEFAULT),
                         os.path.getsize(cp))
    S.mgmt_delete(lib, ["a.heic"])                        # 源进回收站
    picked, _ghost, protected = S._gc_collect(None, True, None)[:3]
    chk("14a 源在回收站 → 其缓存受保护（不判孤儿）",
        cp not in {p["path"] for p in picked} and protected >= 1,
        f"protected={protected}")
    src_snap, trash_snap = snap(SRC), snap(TRASH)
    S.cache_gc_apply(None, None)
    chk("14b 清缓存后源文件字节不变", snap(SRC) == src_snap)
    chk("14c 清缓存后回收站文件字节不变", snap(TRASH) == trash_snap)

    # ==================================================================
    #  阶段五：30 天永久删除 + 四项专项测试
    # ==================================================================
    print("\n[阶段五 · 到期永久删除]")
    add_file("p1.jpg", b"PURGE-1" * 30)
    r = S.mgmt_delete(lib, ["p1.jpg"])
    pit = S.TRASH.items_of_batch(r["batchId"])[0]
    chk("P1 删除后进入回收站待到期（文件确实在回收站）",
        S.TRASH.item(pit["item_id"])["status"] == "trashed"
        and os.path.isfile(S._abs_trash(pit["trash_rel"])))
    pr = S.purge_expired()
    chk("P2 未到期 → 到期检查不碰它", pr["purged"] == 0
        and S.TRASH.item(pit["item_id"])["status"] == "trashed", f"purged={pr['purged']}")
    S.TRASH.set_item(pit["item_id"], expire_at=int(time.time()) - 5)
    pr = S.purge_expired()
    chk("P3 到期 → 永久删除，回收站文件消失",
        pr["purged"] >= 1 and S.TRASH.item(pit["item_id"])["status"] == "purged"
        and not os.path.exists(S._abs_trash(pit["trash_rel"])),
        f"purged={pr['purged']}")
    chk("P4 审计已记录 purge",
        any(a["op"] == "purge" for a in S.TRASH.audit_tail(50)))
    chk("P5 已永久删除的条目不再出现在回收站列表",
        all(x["item_id"] != pit["item_id"]
            for x in S.TRASH.list_items("lib_t", status="trashed")))
    pr = S.purge_expired()
    chk("P6 重复调度幂等（再跑一次不重复删）", pr["purged"] == 0, f"purged={pr['purged']}")

    # ---------- 专项 1：崩溃恢复时 原路径 / 回收站路径同时存在 ----------
    print("\n[专项1 · 崩溃恢复：两处同时存在]")
    # (a) delete 批
    add_file("d1.jpg", b"DUP-DELETE" * 40)
    bid = trash_db.make_id("del")
    S.TRASH.create_batch(bid, "lib_t", "delete", 1)
    tra = f"lib_t/{bid}/d1.jpg"
    dst = S._abs_trash(tra)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    st = os.stat(os.path.join(SRC, "d1.jpg"))
    shutil.copy2(os.path.join(SRC, "d1.jpg"), dst)      # 回收站有副本，原位也还在
    iid = S.TRASH.add_items([{
        "batch_id": bid, "lib_id": "lib_t", "orig_rel": "d1.jpg",
        "trash_rel": tra, "name": "d1.jpg", "kind": "img", "is_video": 0,
        "size": st.st_size, "mtime": st.st_mtime,
        "group_key": S._group_key("lib_t", "d1.jpg"),
        "pair_role": "photo", "live": 0, "status": "moving"}])[0]
    S.TRASH.link(bid, [iid])
    S.recover_trash()
    got = S.TRASH.item(iid)
    chk("专项1a delete 两处都在 → 以回收站为准(trashed)，原位残留不被删除",
        got["status"] == "trashed"
        and os.path.isfile(os.path.join(SRC, "d1.jpg"))
        and os.path.isfile(S._abs_trash(got["trash_rel"])), got["status"])
    chk("专项1a2 恢复过程不误删、批次已收尾",
        S.TRASH.batch(bid)["status"] in ("done", "partial"))
    S.mgmt_purge(lib, item_ids=[iid])
    drop_file("d1.jpg")

    # (b) restore 批，两处都在且内容一致 → 判定已恢复 + 清掉回收站冗余副本
    add_file("d2.jpg", b"DUP-RESTORE-SAME" * 20)
    r = S.mgmt_delete(lib, ["d2.jpg"])
    rit = S.TRASH.items_of_batch(r["batchId"])[0]
    shutil.copy2(S._abs_trash(rit["trash_rel"]), os.path.join(SRC, "d2.jpg"))
    S.TRASH.set_item(rit["item_id"], status="moving")
    bid2 = trash_db.make_id("res")
    S.TRASH.create_batch(bid2, "lib_t", "restore", 1)
    S.TRASH.link(bid2, [rit["item_id"]])
    S.recover_trash()
    got2 = S.TRASH.item(rit["item_id"])
    chk("专项1b restore 两处都在且一致 → 判已恢复，回收站冗余副本被清",
        got2["status"] == "restored"
        and not os.path.exists(S._abs_trash(got2["trash_rel"]))
        and os.path.isfile(os.path.join(SRC, "d2.jpg")), got2["status"])
    drop_file("d2.jpg")

    # (c) restore 批，两处都在但内容不同 → 视为同名替代，绝不覆盖
    add_file("d3.jpg", b"DIFF-ORIGINAL-CONTENT" * 12)
    r = S.mgmt_delete(lib, ["d3.jpg"])
    rit = S.TRASH.items_of_batch(r["batchId"])[0]
    repl = b"REPLACEMENT" * 25
    w(os.path.join(SRC, "d3.jpg"), repl)
    S.TRASH.set_item(rit["item_id"], status="moving")
    bid3 = trash_db.make_id("res")
    S.TRASH.create_batch(bid3, "lib_t", "restore", 1)
    S.TRASH.link(bid3, [rit["item_id"]])
    S.recover_trash()
    got3 = S.TRASH.item(rit["item_id"])
    chk("专项1c restore 两处都在但不同 → conflict，原路径不被覆盖",
        got3["status"] == "conflict"
        and open(os.path.join(SRC, "d3.jpg"), "rb").read() == repl
        and os.path.isfile(S._abs_trash(got3["trash_rel"])), got3["status"])
    rr3 = S.mgmt_restore(lib, items=[rit["item_id"]], on_conflict="rename")
    chk("专项1d conflict 项可经「改名」安全恢复",
        rr3["restored"] == 1 and os.path.isfile(os.path.join(SRC, "d3_1.jpg"))
        and open(os.path.join(SRC, "d3.jpg"), "rb").read() == repl)
    drop_file("d3.jpg")
    drop_file("d3_1.jpg")

    # ---------- 专项 2：原路径同名替代文件 ≠ 原文件 ----------
    print("\n[专项2 · 原路径同名替代文件不得当作原文件]")
    add_file("r2.jpg", b"REAL-ORIGINAL-2" * 18)
    r = S.mgmt_delete(lib, ["r2.jpg"])
    rit = S.TRASH.items_of_batch(r["batchId"])[0]
    os.remove(S._abs_trash(rit["trash_rel"]))          # 模拟恢复中断：回收站副本没了
    impostor = b"IMPOSTOR-CONTENT" * 30
    w(os.path.join(SRC, "r2.jpg"), impostor)           # 原位放一个同名替代文件
    S.TRASH.set_item(rit["item_id"], status="moving")
    bid = trash_db.make_id("res")
    S.TRASH.create_batch(bid, "lib_t", "restore", 1)
    S.TRASH.link(bid, [rit["item_id"]])
    S.recover_trash()
    got = S.TRASH.item(rit["item_id"])
    chk("专项2a 中断恢复：原位同名替代文件不被判为已恢复",
        got["status"] == "failed" and "同名但不同" in got["error"], got["status"])
    chk("专项2b 替代文件未被覆盖 / 未被删除",
        open(os.path.join(SRC, "r2.jpg"), "rb").read() == impostor)
    drop_file("r2.jpg")

    add_file("r3.jpg", b"REAL-ORIGINAL-3" * 16)
    r = S.mgmt_delete(lib, ["r3.jpg"])
    rit3 = S.TRASH.items_of_batch(r["batchId"])[0]
    repl3 = b"DIFFERENT-REPLACEMENT" * 22
    w(os.path.join(SRC, "r3.jpg"), repl3)
    rs = S.mgmt_restore(lib, items=[rit3["item_id"]], on_conflict="skip")
    chk("专项2c 正常恢复遇同名替代 → conflict 跳过，不覆盖",
        rs["skipped"] == 1
        and open(os.path.join(SRC, "r3.jpg"), "rb").read() == repl3)
    S.mgmt_purge(lib, item_ids=[rit3["item_id"]])
    drop_file("r3.jpg")

    # ---------- 专项 3：Live 组部分失败 → 批次 / 重试 / 展示一致 ----------
    print("\n[专项3 · Live 组部分失败（批次/重试/展示一致）]")
    UUID_L = "11111111-2222-3333-4444-555555555555"
    UUID_M = "99999999-8888-7777-6666-555555555555"
    SRC_ = SRC.replace("\\", "/")
    _ = SRC_
    w(os.path.join(SRC, "L.heic"), b"FTYPHEIC" + UUID_L.encode())
    w(os.path.join(SRC, "L.mov"),
      b"\x00" * 24 + b"com.apple.quicktime.content.identifier" + UUID_L.encode()
      + b"\x00" * 8 + b"com.apple.quicktime.live-photo")
    S.scan_library(L(), full=True)
    pr = S.INDEX.pairs("lib_t").get("L.heic")
    chk("专项3a 新 Live 组已正确配对", bool(pr) and pr["live"] == 1, str(pr))

    with open(os.path.join(SRC, "L.mov"), "ab") as f:
        f.write(b"EXTERNAL-EDIT")           # 组内 MOV 被外部改动 → 复核应拦下
    r = S.mgmt_delete(lib, ["L.heic"])
    chk("专项3b 组内成员不符 → 整组跳过，图片未被单独删除",
        r["moved"] == 0 and os.path.isfile(os.path.join(SRC, "L.heic"))
        and any("整组跳过" in e["why"] for e in r["errors"]),
        f"moved={r['moved']} errors={[e['why'] for e in r['errors']]}")
    S.scan_library(L(), full=True)

    r = S.mgmt_delete(lib, ["L.heic"])
    its = S.TRASH.items_of_batch(r["batchId"])
    chk("专项3c Live 组整体删除（2 个文件 / 1 组）",
        r["moved"] == 2 and r["groups"] == 1, f"moved={r['moved']} groups={r['groups']}")
    w(os.path.join(SRC, "L.heic"), b"CONFLICT-PHOTO")     # 占住原位
    rs = S.mgmt_restore(lib, items=[x["item_id"] for x in its], on_conflict="skip")
    chk("专项3d skip：整组跳过（冲突），无部分配对",
        rs["skipped"] == 2 and rs["restored"] == 0
        and not os.path.exists(os.path.join(SRC, "L.mov"))
        and all(S.TRASH.item(x["item_id"])["status"] == "conflict" for x in its))
    rr = S.mgmt_restore(lib, items=[x["item_id"] for x in its], on_conflict="rename")
    chk("专项3f rename 重试：整组改名恢复，配对保持",
        rr["restored"] == 2 and os.path.isfile(os.path.join(SRC, "L_1.heic"))
        and os.path.isfile(os.path.join(SRC, "L_1.mov")), f"restored={rr['restored']}")
    chk("专项3g 恢复后配对关系重建",
        bool(S.INDEX.pairs("lib_t").get("L_1.heic")))
    drop_file("L.heic")
    drop_file("L_1.heic")
    drop_file("L_1.mov")

    w(os.path.join(SRC, "M.heic"), b"FTYPHEIC" + UUID_M.encode())
    w(os.path.join(SRC, "M.mov"),
      b"\x00" * 24 + b"com.apple.quicktime.content.identifier" + UUID_M.encode()
      + b"\x00" * 8 + b"com.apple.quicktime.live-photo")
    S.scan_library(L(), full=True)
    r = S.mgmt_delete(lib, ["M.heic"])
    its = S.TRASH.items_of_batch(r["batchId"])
    mov_it = [x for x in its if x["kind"] == "mov"][0]
    img_it = [x for x in its if x["kind"] == "img"][0]
    os.remove(S._abs_trash(mov_it["trash_rel"]))        # 组内一个成员副本缺失
    rr = S.mgmt_restore(lib, items=[x["item_id"] for x in its])
    chk("专项3h 组内成员副本缺失 → 整组不恢复",
        rr["restored"] == 0 and not os.path.exists(os.path.join(SRC, "M.heic")),
        f"restored={rr['restored']}")
    chk("专项3i 条目状态与批次状态一致（img=failed / mov=missing / batch=failed）",
        S.TRASH.item(img_it["item_id"])["status"] == "failed"
        and S.TRASH.item(mov_it["item_id"])["status"] == "missing"
        and S.TRASH.batch(rr["batchId"])["status"] == "failed",
        f"img={S.TRASH.item(img_it['item_id'])['status']} "
        f"mov={S.TRASH.item(mov_it['item_id'])['status']} "
        f"batch={S.TRASH.batch(rr['batchId'])['status']}")
    pg = S.mgmt_purge(lib, item_ids=[x["item_id"] for x in its])
    chk("专项3j 清理：副本已缺的条目也能安全收尾（记为已永久删除）",
        pg["purged"] == 2 and pg["failed"] == 0, f"purged={pg['purged']}")

    # ---------- 专项 4：永久删除中断后重启 ----------
    print("\n[专项4 · 永久删除中断后重启]")
    add_file("q1.jpg", b"Q-ONE" * 40)
    add_file("q2.jpg", b"Q-TWO" * 40)
    r = S.mgmt_delete(lib, ["q1.jpg", "q2.jpg"])
    qs = S.TRASH.items_of_batch(r["batchId"])
    q1 = [x for x in qs if x["orig_rel"] == "q1.jpg"][0]
    q2 = [x for x in qs if x["orig_rel"] == "q2.jpg"][0]
    os.remove(S._abs_trash(q1["trash_rel"]))            # q1 已删除，状态还没更新
    pbid = trash_db.make_id("pur")
    S.TRASH.create_batch(pbid, "lib_t", "purge", 2)
    S.TRASH.link(pbid, [q1["item_id"], q2["item_id"]])
    S.TRASH.set_item(q1["item_id"], status="moving")
    S.TRASH.set_item(q2["item_id"], status="pending")
    audit_before = len(S.TRASH.audit_tail(1000))
    others_before = {x["item_id"]: x["trash_rel"]
                     for x in S.TRASH.list_items("lib_t", status="trashed")}
    S.recover_trash()
    g1 = S.TRASH.item(q1["item_id"])
    g2 = S.TRASH.item(q2["item_id"])
    chk("专项4a 已删的→purged / 未删的→failed（可重试），二者都不丢",
        g1["status"] == "purged" and g2["status"] == "failed"
        and os.path.isfile(S._abs_trash(g2["trash_rel"])),
        f"q1={g1['status']} q2={g2['status']}")
    chk("专项4b 其它回收站条目未被误删",
        all(os.path.isfile(S._abs_trash(tr))
            for i2, tr in others_before.items()
            if S.TRASH.item(i2) and S.TRASH.item(i2)["status"] == "trashed"))
    chk("专项4c 审计记录不丢（恢复过程新增审计）",
        len(S.TRASH.audit_tail(1000)) > audit_before)
    chk("专项4d 批次收尾为 partial（1 成功 1 失败）",
        S.TRASH.batch(pbid)["status"] == "partial",
        str(S.TRASH.batch(pbid)["status"]))
    rg = S.mgmt_purge(lib, item_ids=[q2["item_id"]])
    chk("专项4e 失败项可安全重试", rg["purged"] == 1 and rg["failed"] == 0)

    # ---------- 阶段五：越界防护 + 清空回收站 ----------
    print("\n[阶段五 · 越界防护 / 清空回收站]")
    add_file("v1.jpg", b"VVVV" * 60)
    r = S.mgmt_delete(lib, ["v1.jpg"])
    vit = S.TRASH.items_of_batch(r["batchId"])[0]
    good_rel = vit["trash_rel"]
    S.TRASH.set_item(vit["item_id"], trash_rel="../../escape.txt")
    res = S.mgmt_purge(lib, item_ids=[vit["item_id"]])
    chk("E1 拒绝越界的 trash_rel（不删除任何东西）",
        res["purged"] == 0 and res["failed"] == 1, f"purged={res['purged']}")
    S.TRASH.set_item(vit["item_id"], trash_rel=good_rel, status="trashed")
    S.mgmt_purge(lib, item_ids=[vit["item_id"]])
    chk("E2 修正后正常永久删除", S.TRASH.item(vit["item_id"])["status"] == "purged")

    remaining = S.TRASH.list_items("lib_t", status="trashed")
    before_n = len(remaining)
    src_before = snap(SRC)
    res = S.mgmt_purge(lib, item_ids=[x["item_id"] for x in remaining])
    chk("E3 清空：全部 trashed 条目被永久删除",
        res["purged"] == before_n
        and len(S.TRASH.list_items("lib_t", status="trashed")) == 0,
        f"purged={res['purged']}/{before_n}")
    left = []
    for d, _s, fs in os.walk(TRASH):
        left += fs
    chk("E4 回收站文件区已清空（无残留文件）", len(left) == 0, f"{len(left)} 个")
    chk("E5 永久删除后源目录未受影响", snap(SRC) == src_before)
    chk("E5b 清空 ≠ 缓存回收：cache 目录未被本路径触碰",
        os.path.isdir(S.CACHE_DIR))

    # ---------- 真实环境隔离 ----------
    print("\n[真实环境隔离]")
    real_trash_now = _real_trash_listing()
    chk("真实 data/trash 未被本套件改动（与开始时逐项一致）",
        real_trash_now == real_trash_before,
        f"前 {len(real_trash_before)} 个 / 后 {len(real_trash_now)} 个")

    print("\n" + "=" * 70)
    print(f"  结果：{PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
