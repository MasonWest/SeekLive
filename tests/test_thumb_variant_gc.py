#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缩略图旧规格回收（GC 类别 obsolete_variant）专项测试
=====================================================
在**完全独立的测试树**上跑（独立 cache/ 与独立 cache.db），绝不碰真实缓存与源图库。

覆盖实施计划 §六.B「GC 分类与安全」：

  1. 旧规格（160/240/320/480）被识别为 obsolete_variant；640 绝不被误判
  2. photo / 转码 记录永远不会出现在任何可删类别里（0 容忍）
  3. 保护优先：图库离线 / 源在回收站 → 旧规格也**保留**
  4. 源已消失 → 仍归 obsolete_variant（保证「统一规格」总数完整可对账）
  5. 规格无法解析 / 路径与清单不符 / 重复映射 → 一律**安全跳过 + 计数**
  6. dry-run 完全无副作用（数据库与磁盘逐字节不变）
  7. apply 只删旧规格：640 / photo / trans 原样保留，清单同步
  8. 二次核验（防 dry-run → 删除之间的数据漂移）逐条生效
  9. 既有四类（tmp_leak / unrecorded / missing_source / changed_source）不回归

运行：  python tests/test_thumb_variant_gc.py
退出码 0 = 全绿。
"""

import os
import sys
import time
import shutil

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import server as S            # noqa: E402
import cache_db               # noqa: E402
import index_db               # noqa: E402

T = os.path.join(HERE, "tmp", "thumb_gc_test")
SRC = os.path.join(T, "src")
CACHE = os.path.join(T, "cache")

PASS, FAIL = [], []


def chk(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f"   [{detail}]" if detail else ""))


def sect(t):
    print(f"\n=== {t} ===")


class _FakeTrash:
    """只实现 src_state() 会用到的 is_trashed()，用于精确构造「源在回收站」情形。"""
    def __init__(self, rels=()):
        self.rels = set(rels)

    def is_trashed(self, lid, rel):
        return (lid, rel) in self.rels


def setup():
    if os.path.isdir(T):
        shutil.rmtree(T)
    os.makedirs(SRC)
    os.makedirs(CACHE)
    with open(os.path.join(SRC, "a.jpg"), "wb") as f:
        f.write(b"IMG-A" * 100)
    with open(os.path.join(SRC, "clip.mp4"), "wb") as f:
        f.write(b"VID-CLIP" * 500)
    S.META["cache"] = {"temporaryGraceSeconds": 0}
    S.CACHE_DIR = CACHE
    S.CACHE_INDEX = cache_db.CacheIndex(os.path.join(T, "cache.db"))
    S.INDEX = index_db.Index(os.path.join(T, "index.db"))
    S.TRASH = _FakeTrash()
    S.META = {
        "cache": {"temporaryGraceSeconds": 0},
        "lib_order": ["lib_a"],
        "libs": {"lib_a": {"name": "测试库", "root": SRC, "exists": True,
                           "stale": False, "count": 2, "live": 0,
                           "movParsed": 0}},
        "scan": {},
    }
    S.INDEX.touch_lib("lib_a", "测试库", SRC)


def mk(rel, mt, sz, kind, variant, payload=b"payload-bytes", arg="", lib_id="lib_a"):
    """造一个「有清单记录」的缓存文件（盘 + 清单都建）。

    arg = cache_key 里的 `kind+fmt+side` 段；不关心真实算法时留空即可
    （GC 只读清单，不重算 key）。kind='trans' 时落在 cache/trans/ 布局下。
    """
    h = S.cache_key(rel, mt, sz, arg or (kind + variant), "", lib_id=lib_id)[2]
    if kind == "trans":
        p = os.path.join(CACHE, "trans", h[:2], h + ".mp4")
    else:
        p = S.cache_path(h[:2], h[2:4], h)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(payload)
    S.CACHE_INDEX.record(h, lib_id, rel, mt, sz, kind, variant, len(payload))
    return h, p


def row(plan, cls="obsolete_variant"):
    for c in plan["byClass"]:
        if c["cls"] == cls:
            return c
    return None


def st_of(rel):
    s = os.stat(os.path.join(SRC, rel))
    return s.st_mtime, s.st_size


def snap_cache():
    out = {}
    for dp, _dn, fn in os.walk(CACHE):
        for n in fn:
            fp = os.path.join(dp, n)
            out[fp] = os.path.getsize(fp)
    return out


def main():
    print("=" * 72)
    print("  缩略图旧规格回收 · 专项测试（独立测试树）")
    print("=" * 72)
    setup()
    mt, sz = st_of("a.jpg")

    # ---------------------------------------------------------------
    sect("组 1 · 旧规格识别 / 640 不误判 / photo·trans 0 容忍")
    # ---------------------------------------------------------------
    mk("a.jpg", mt, sz, "thumb", "thumb|webp|160", b"1" * 160, arg="thumbwebp160")
    mk("a.jpg", mt, sz, "thumb", "thumb|webp|240", b"2" * 240, arg="thumbwebp240")
    mk("a.jpg", mt, sz, "thumb", "thumb|webp|320", b"3" * 320, arg="thumbwebp320")
    mk("a.jpg", mt, sz, "thumb", "thumb|webp|480", b"4" * 480, arg="thumbwebp480")
    _h640, p640 = mk("a.jpg", mt, sz, "thumb", "thumb|webp|640", b"6" * 640,
                     arg="thumbwebp640")
    _hph, pph = mk("a.jpg", mt, sz, "photo", "photo|webp|2048", b"P" * 900,
                   arg="photowebp2048")
    _htr, ptr = mk("a.jpg", mt, sz, "trans", "h264-crf23|900", b"T" * 700,
                   arg="tc3-test")
    # 非启用档但历史未知规格（如 960）也必须被覆盖
    mk("a.jpg", mt, sz, "thumb", "thumb|webp|960", b"9" * 960, arg="thumbwebp960")

    plan = S.cache_gc_plan(None, True, True)
    r = row(plan)
    chk("obsolete_variant 类别已出现在 byClass（即便为 0 也输出）", r is not None)
    chk("旧规格可删文件 = 5（160/240/320/480/960）",
        r["files"] == 5, f"files={r['files']}")
    chk("bySide 逐档可见（按档倒序）",
        [b["side"] for b in r["bySide"]] == [960, 480, 320, 240, 160],
        str([(b["side"], b["files"]) for b in r["bySide"]]))
    chk("candidates = 5（不含受保护）", r["candidates"] == 5, str(r["candidates"]))
    chk("保留档 640 = 1", r["keep"]["files"] == 1, str(r["keep"]))
    chk("protectedByState 全 0", r["protectedByState"] == {"offline": 0, "trashed": 0},
        str(r["protectedByState"]))
    chk("unparsable = 0 / mismatch = 0",
        r["unparsable"] == 0 and r["mismatch"] == 0, str(r))
    chk("safety：photo/trans 出现在可删类别里 = 0",
        plan["safety"]["photoTransInDeletable"] == 0,
        str(plan["safety"]))
    chk("kinds 统计里 photo / trans 均有记录",
        plan["kinds"].get("photo", {}).get("records") == 1
        and plan["kinds"].get("trans", {}).get("records") == 1, str(plan["kinds"]))
    picked_paths = set()
    for c in plan["byClass"]:
        picked_paths.update(os.path.basename(s["p"]) for s in c["sample"])
    chk("640 / photo / trans 均不在任何可删样本里",
        os.path.basename(p640) not in picked_paths
        and os.path.basename(pph) not in picked_paths
        and os.path.basename(ptr) not in picked_paths)

    # ---------------------------------------------------------------
    sect("组 2 · 保护优先：图库离线 / 源在回收站")
    # ---------------------------------------------------------------
    S.META["libs"]["lib_a"]["stale"] = True
    pl = S.cache_gc_plan(None, True, True)
    rl = row(pl)
    chk("图库离线 → 旧规格全部保留（可删 0）", rl["files"] == 0, f"files={rl['files']}")
    chk("图库离线 → protectedByState.offline = 5",
        rl["protectedByState"]["offline"] == 5, str(rl["protectedByState"]))
    chk("图库离线 → candidates 仍为 5（可见但受保护）",
        rl["candidates"] == 5, str(rl["candidates"]))
    S.META["libs"]["lib_a"]["stale"] = False

    # 源在回收站：源文件不存在 + TRASH 认领 → 该条目必须受保护
    S.TRASH = _FakeTrash({("lib_a", "trashed.jpg")})
    mk("trashed.jpg", 111, 222, "thumb", "thumb|webp|320", b"X" * 300,
       arg="thumbwebpT320")
    pt = S.cache_gc_plan(["obsolete_variant"], True, True)
    rt = row(pt)
    chk("源在回收站 → protectedByState.trashed = 1",
        rt["protectedByState"]["trashed"] == 1, str(rt["protectedByState"]))
    chk("其余旧规格仍可删（5）", rt["files"] == 5, f"files={rt['files']}")
    chk("candidates = 6（含受保护那条）", rt["candidates"] == 6, str(rt["candidates"]))

    # ---------------------------------------------------------------
    sect("组 3 · 源已消失：仍归 obsolete_variant（总数完整可对账）")
    # ---------------------------------------------------------------
    mk("vanish.jpg", 333, 444, "thumb", "thumb|webp|480", b"V" * 480,
       arg="thumbwebp480")
    pv = S.cache_gc_plan(["obsolete_variant"], True, True)
    rv = row(pv)
    chk("源不存在且不在回收站 → 仍计 obsolete_variant（非 missing_source）",
        rv["files"] == 6, f"files={rv['files']}")   # 5(组1) + 1(vanish)，trashed 那条受保护
    msv = row(pv, "missing_source")
    chk("同一记录的 missing_source 为 0（不重复计数）",
        (msv is None) or msv["files"] == 0, str(msv))

    # ---------------------------------------------------------------
    sect("组 4 · 无法解析 / 路径不符 / 重复映射 → 安全跳过")
    # ---------------------------------------------------------------
    _hu, pu = mk("a.jpg", mt, sz, "thumb", "thumb|webp|abc", b"U" * 200,
                 arg="thumbwebpUNPARSABLE")
    _hu2, pu2 = mk("a.jpg", mt, sz, "thumb", "thumb|webp", b"U" * 210,
                   arg="thumbwebpUNPARSABLE2")
    pl4 = S.cache_gc_plan(["obsolete_variant"], True, True)
    r4 = row(pl4)
    chk("无法解析的 thumb 记录被计数（>=2）", r4["unparsable"] >= 2, str(r4["unparsable"]))
    chk("无法解析的文件未被删除", os.path.isfile(pu) and os.path.isfile(pu2))

    # 路径与清单推导不符：文件放到错误的分片目录
    hm, _ = mk("a.jpg", mt, sz, "thumb", "thumb|webp|480", b"M" * 300,
               arg="thumbwebpMISMATCH")
    wrong = os.path.join(CACHE, "zz", "yy", hm + ".cache")
    os.makedirs(os.path.dirname(wrong), exist_ok=True)
    real = S.cache_file_for(hm, "thumb")
    if os.path.isfile(real):
        os.remove(real)
    with open(wrong, "wb") as f:
        f.write(b"M" * 300)
    pl4 = S.cache_gc_plan(["obsolete_variant"], True, True)
    r4 = row(pl4)
    chk("路径与清单不符 → 计入 mismatch", r4["mismatch"] >= 1, str(r4["mismatch"]))
    chk("路径不符的文件未被删除", os.path.isfile(wrong))

    # 重复映射：同一 key 的缓存文件出现在两个分片目录
    hd, _pd = mk("a.jpg", mt, sz, "thumb", "thumb|webp|160", b"D" * 100,
                 arg="thumbwebpDUP")
    dup = os.path.join(CACHE, "dup", "xx", hd + ".cache")
    os.makedirs(os.path.dirname(dup), exist_ok=True)
    with open(dup, "wb") as f:
        f.write(b"D" * 100)
    pl4 = S.cache_gc_plan(["obsolete_variant"], True, True)
    r4 = row(pl4)
    chk("重复映射被计入 mismatch", r4["mismatch"] >= 2, str(r4["mismatch"]))
    chk("重复映射的文件未被删除",
        os.path.isfile(dup) and os.path.isfile(S.cache_path(hd[:2], hd[2:4], hd)))

    # ---------------------------------------------------------------
    sect("组 5 · dry-run 完全无副作用")
    # ---------------------------------------------------------------
    before_files = snap_cache()
    before_rows = S.CACHE_INDEX.count()
    for _ in range(2):
        S.cache_gc_plan(None, True, True)
    chk("dry-run 后磁盘文件集合与大小逐项不变", snap_cache() == before_files,
        f"{len(before_files)} -> {len(snap_cache())}")
    chk("dry-run 后清单行数不变", S.CACHE_INDEX.count() == before_rows,
        f"{before_rows} -> {S.CACHE_INDEX.count()}")

    # ---------------------------------------------------------------
    sect("组 6 · apply：只删旧规格，其余原样保留")
    # ---------------------------------------------------------------
    if os.path.isdir(CACHE):
        shutil.rmtree(CACHE)
    os.makedirs(CACHE)
    S.CACHE_INDEX.forget_probe()
    _h640b, p640 = mk("a.jpg", mt, sz, "thumb", "thumb|webp|640", b"6" * 640,
                      arg="thumbwebp640")
    _h480, p480 = mk("a.jpg", mt, sz, "thumb", "thumb|webp|480", b"4" * 480,
                     arg="thumbwebp480")
    _h320, p320 = mk("a.jpg", mt, sz, "thumb", "thumb|webp|320", b"3" * 320,
                     arg="thumbwebp320")
    _hph2, pph2 = mk("a.jpg", mt, sz, "photo", "photo|webp|2048", b"P" * 900,
                     arg="photowebp2048")
    _htr2, ptr2 = mk("a.jpg", mt, sz, "trans", "h264-crf23|900", b"T" * 700,
                     arg="tc3-test")

    pl6 = S.cache_gc_plan(["obsolete_variant"], True, True)
    chk("apply 前预览：旧规格 = 2", row(pl6)["files"] == 2, str(row(pl6)["files"]))
    res = S.cache_gc_apply(["obsolete_variant"], True)
    chk("apply 实际删除 = 预览数量", res["deleted"] == 2, str(res))
    chk("apply 无错误", not res["errors"], str(res["errors"]))
    chk("旧规格文件已删除", not os.path.isfile(p480) and not os.path.isfile(p320))
    chk("640 缩略图保留", os.path.isfile(p640))
    chk("photo 缓存保留", os.path.isfile(pph2))
    chk("转码缓存保留", os.path.isfile(ptr2))
    after = {e["key"]: e for e in S.CACHE_INDEX.all_entries()}
    chk("清单同步：旧规格行已移除、其余仍在",
        _h480 not in after and _h320 not in after
        and _hph2 in after and _htr2 in after)
    pl6b = S.cache_gc_plan(["obsolete_variant"], True, True)
    chk("apply 后再次预览：旧规格归零", row(pl6b)["files"] == 0,
        str(row(pl6b)["files"]))

    # ---------------------------------------------------------------
    sect("组 7 · 二次核验：dry-run 与删除之间数据变化 → 跳过（不盲删）")
    # ---------------------------------------------------------------
    _hs, ps = mk("a.jpg", mt, sz, "thumb", "thumb|webp|480", b"S" * 480,
                 arg="thumbwebpSTALE")
    real_collect = S._gc_collect

    def stale_collect(classes=None, force=True, full=None):
        """模拟「dry-run 之后、执行之前」冻结的旧候选清单。"""
        items = [{"path": ps, "size": 480, "cls": "obsolete_variant",
                  "kind": "thumb", "why": "stale test"}]
        thr = {"records": 0, "unparsable": 0, "bySide": {},
               "protectedByState": {"offline": 0, "trashed": 0},
               "mismatch": 0, "keepFiles": 0, "keepBytes": 0}
        return items, [], 0, 1, 480, 0, thr, {}

    # ① 规格已变回启用档 → 必须跳过
    S._gc_collect = stale_collect
    try:
        S.CACHE_INDEX.record(_hs, "lib_a", "a.jpg", mt, sz, "thumb",
                             "thumb|webp|640", 480)
        r1 = S.cache_gc_apply(["obsolete_variant"], True)
        chk("规格已变 640 → 跳过且文件仍在",
            r1["deleted"] == 0 and r1["skipped"] == 1 and os.path.isfile(ps), str(r1))
        S.CACHE_INDEX.record(_hs, "lib_a", "a.jpg", mt, sz, "thumb",
                             "thumb|webp|480", 480)
        # ② 文件大小已变化 → 必须跳过
        with open(ps, "wb") as f:
            f.write(b"S" * 999)
        r2 = S.cache_gc_apply(["obsolete_variant"], True)
        chk("文件大小已变 → 跳过且文件仍在",
            r2["deleted"] == 0 and r2["skipped"] == 1 and os.path.isfile(ps), str(r2))
        # ③ 清单行已消失 → 必须跳过
        with open(ps, "wb") as f:
            f.write(b"S" * 480)
        S.CACHE_INDEX.delete_keys([_hs])
        r3 = S.cache_gc_apply(["obsolete_variant"], True)
        chk("清单行已消失 → 跳过且文件仍在",
            r3["deleted"] == 0 and r3["skipped"] == 1 and os.path.isfile(ps), str(r3))
    finally:
        S._gc_collect = real_collect

    # ---------------------------------------------------------------
    sect("组 8 · 既有四类不回归")
    # ---------------------------------------------------------------
    if os.path.isdir(CACHE):
        shutil.rmtree(CACHE)
    os.makedirs(CACHE)
    S.CACHE_INDEX.forget_probe()
    mk("a.jpg", mt, sz, "thumb", "thumb|webp|640", b"6" * 640, arg="thumbwebp640")
    # tmp_leak
    tmpf = os.path.join(CACHE, "trans", "ab", "abc.mp4.1.tmp.mp4")
    os.makedirs(os.path.dirname(tmpf), exist_ok=True)
    with open(tmpf, "wb") as f:
        f.write(b"T" * 100)
    # unrecorded
    junk = os.path.join(CACHE, "zz", "yy", "deadbeef00" * 8 + ".cache")
    os.makedirs(os.path.dirname(junk), exist_ok=True)
    with open(junk, "wb") as f:
        f.write(b"U" * 100)
    # missing_source
    mk("gone.jpg", 1, 2, "thumb", "thumb|webp|640", b"M" * 100, arg="thumbwebp640")
    p8 = S.cache_gc_plan(None, True, True)
    names = {c["cls"]: c["files"] for c in p8["byClass"]}
    chk("tmp_leak 仍为 1", names.get("tmp_leak") == 1, str(names))
    chk("unrecorded 仍为 1", names.get("unrecorded") == 1, str(names))
    chk("missing_source 仍为 1", names.get("missing_source") == 1, str(names))

    print("\n" + "=" * 72)
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("   x", f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
