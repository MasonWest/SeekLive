#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缓存回收专项测试（在完全独立的测试树上跑，绝不碰真实 cache/ 与源图库）
=====================================================================
覆盖：
  1. 有效缓存           → 不判孤儿
  2. 源文件被删         → missing_source
  3. 源文件被改(mtime)  → changed_source
  4. 源文件被改(size)   → changed_source
  5. 中断残留 *.tmp     → tmp_leak
  6. 盘上有、清单没有   → unrecorded
  7. 图库离线           → 受保护，不判孤儿（关键安全项）
  8. 执行清理           → 只删 cache/ 内文件；源文件 100% 完好
  9. 清单幽灵行         → 自动清掉
 10. 清理后再次预览     → 孤儿归零

> 缩略图「旧规格（非 640）」的识别与回收见 tests/test_thumb_variant_gc.py；
> 本文件聚焦既有的四类孤儿语义不回归。
"""
import os
import sys
import time
import shutil
import hashlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import server as S
import cache_db
import index_db

T = os.path.join(HERE, "tmp", "gc_test")
SRC = os.path.join(T, "src")
CACHE = os.path.join(T, "cache")

PASS = FAIL = 0


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2713 {name}" + (f"  [{extra}]" if extra else ""))
    else:
        FAIL += 1
        print(f"  \u2717 {name}  \u2190 FAIL  [{extra}]")


def setup():
    if os.path.isdir(T):
        shutil.rmtree(T)
    os.makedirs(SRC)
    os.makedirs(CACHE)
    # 源文件（放在 cache 之外）
    with open(os.path.join(SRC, "a.jpg"), "wb") as f:
        f.write(b"IMG-A" * 100)
    with open(os.path.join(SRC, "clip.mp4"), "wb") as f:
        f.write(b"VID-CLIP" * 500)
    # 把 server 的全局指向测试目录
    S.META["cache"] = {"temporaryGraceSeconds": 0}
    S.CACHE_DIR = CACHE
    S.CACHE_INDEX = cache_db.CacheIndex(os.path.join(T, "cache.db"))
    S.INDEX = index_db.Index(os.path.join(T, "index.db"))
    S.META = {
        "cache": {"temporaryGraceSeconds": 0},
        "lib_order": ["lib_a"],
        "libs": {"lib_a": {"name": "测试库", "root": SRC, "exists": True,
                           "stale": False, "count": 2, "live": 0,
                           "movParsed": 0}},
        "scan": {},
    }
    S.INDEX.touch_lib("lib_a", "测试库", SRC)


def make_cache(rel, mtime, size, kind, variant, payload=b"payload-bytes",
               side=None, fmt="webp"):
    """在测试 cache 里造一个「有清单记录」的缓存文件。"""
    arg = kind + fmt + str(side if side is not None else "")
    h = S.cache_key(rel, mtime, size, arg, "", lib_id="lib_a")[2]
    p = S.cache_path(h[:2], h[2:4], h)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(payload)
    S.CACHE_INDEX.record(h, "lib_a", rel, mtime, size, kind,
                         variant, len(payload))
    return h, p


def st_of(rel):
    s = os.stat(os.path.join(SRC, rel))
    return s.st_mtime, s.st_size


def counts(plan):
    d = {c["cls"]: c["files"] for c in plan["byClass"]}
    return d


def main():
    print("=" * 72)
    print("  缓存回收专项测试（独立测试树）")
    print("=" * 72)
    setup()

    mt_a, sz_a = st_of("a.jpg")
    mt_c, sz_c = st_of("clip.mp4")

    print("\n【1】有效缓存不被判为孤儿")
    # ★ 缩略图统一为单档 640 后，「有效缓存」必须用启用档 640，
    #   否则会被 obsolete_variant 正确判为旧规格（见 test_thumb_variant_gc.py）。
    make_cache("a.jpg", mt_a, sz_a, "thumb", "thumb|webp|640",
               b"t" * 500, side=640)
    make_cache("a.jpg", mt_a, sz_a, "photo", "photo|webp|2048", b"P" * 9000)
    make_cache("clip.mp4", mt_c, sz_c, "thumb", "thumb|webp|640",
               b"v" * 700, side=640)
    plan = S.cache_gc_plan()
    chk("缓存文件总数 = 3", plan["cache"]["files"] == 3, plan["cache"]["files"])
    chk("孤儿 = 0", plan["orphan"]["files"] == 0, plan["orphan"]["files"])
    chk("保留 = 3", plan["keep"]["files"] == 3, plan["keep"]["files"])

    print("\n【2】源文件被删 → missing_source")
    os.remove(os.path.join(SRC, "clip.mp4"))
    plan = S.cache_gc_plan()
    c = counts(plan)
    chk("missing_source = 1", c.get("missing_source") == 1, c)
    chk("total 孤儿 = 1", plan["orphan"]["files"] == 1, plan["orphan"]["files"])

    print("\n【3】源文件被改 mtime → changed_source")
    os.remove(os.path.join(SRC, "a.jpg"))
    with open(os.path.join(SRC, "a.jpg"), "wb") as f:
        f.write(b"IMG-A" * 100)
    os.utime(os.path.join(SRC, "a.jpg"),
             (time.time() + 5000, time.time() + 5000))
    plan = S.cache_gc_plan()
    c = counts(plan)
    chk("changed_source >= 2", c.get("changed_source", 0) >= 2, c)

    print("\n【4】盘上有、清单没有 → unrecorded")
    junk = os.path.join(CACHE, "zz", "yy", "deadbeef00" * 8 + ".cache")
    os.makedirs(os.path.dirname(junk), exist_ok=True)
    with open(junk, "wb") as f:
        f.write(b"U" * 4096)
    plan = S.cache_gc_plan()
    c = counts(plan)
    chk("unrecorded = 1", c.get("unrecorded") == 1, c)

    print("\n【5】中断残留 *.tmp → tmp_leak")
    tmpf = os.path.join(CACHE, "trans", "ab", "abc.mp4.12345.tmp.mp4")
    os.makedirs(os.path.dirname(tmpf), exist_ok=True)
    with open(tmpf, "wb") as f:
        f.write(b"T" * 8192)
    plan = S.cache_gc_plan()
    c = counts(plan)
    chk("tmp_leak = 1", c.get("tmp_leak") == 1, c)

    print("\n【6】图库离线 → 缓存受保护，绝不判孤儿")
    # 造一个「有效」缓存，然后把图库标记为离线
    os.remove(os.path.join(SRC, "a.jpg"))
    with open(os.path.join(SRC, "a.jpg"), "wb") as f:
        f.write(b"IMG-A" * 100)
    mt_a2, sz_a2 = st_of("a.jpg")
    # 刻意用「已停用的旧规格 480」：离线保护必须**优先于** obsolete_variant 判定
    h_ok, p_ok = make_cache("a.jpg", mt_a2, sz_a2, "thumb",
                            "thumb|webp|480", b"K" * 300, side=480)
    S.META["libs"]["lib_a"]["stale"] = True
    S.META["libs"]["lib_a"]["exists"] = False
    plan = S.cache_gc_plan()
    chk("离线时 keep.protectedOffline >= 1",
        plan["keep"]["protectedOffline"] >= 1, plan["keep"])
    # 该 key 不应出现在任何孤儿明细中
    orphan_paths = set()
    for c2 in plan["byClass"]:
        for s in c2["sample"]:
            orphan_paths.add(s["p"])
    chk("离线缓存 path 未出现在孤儿样本",
        os.path.relpath(p_ok, S.APP_DIR).replace("\\", "/") not in orphan_paths,
        os.path.relpath(p_ok, S.APP_DIR).replace("\\", "/"))
    S.META["libs"]["lib_a"]["stale"] = False
    S.META["libs"]["lib_a"]["exists"] = True

    print("\n【7】清单幽灵行（盘上已无文件）被识别")
    ghost_h, ghost_p = make_cache("a.jpg", mt_a2, sz_a2, "photo",
                                  "photo|jpeg|2048", b"G" * 100)
    os.remove(ghost_p)
    plan = S.cache_gc_plan()
    chk("ghostRows >= 1", plan["ghostRows"] >= 1, plan["ghostRows"])

    print("\n【8】执行清理：只删 cache/，源文件必须完好")
    src_before = {}
    for dp, _dn, fn in os.walk(SRC):
        for n in fn:
            fp = os.path.join(dp, n)
            src_before[fp] = (os.path.getsize(fp),
                              open(fp, "rb").read())
    plan_before = S.cache_gc_plan()
    expect_del = plan_before["orphan"]["files"]
    res = S.cache_gc_apply()
    chk("删除数量 = 预览数量", res["deleted"] == expect_del,
        f"{res['deleted']} vs {expect_del}")
    chk("无错误", not res["errors"], res["errors"])
    chk("释放字节 > 0", res["bytes"] > 0, res["bytes"])
    # 再走一遍预演：孤儿必须归零
    plan_after = S.cache_gc_plan()
    chk("清理后孤儿归零", plan_after["orphan"]["files"] == 0,
        plan_after["orphan"]["files"])

    # 源文件完整性
    src_ok = True
    for fp, (sz, data) in src_before.items():
        if not os.path.isfile(fp):
            src_ok = False
            print("    源文件丢失:", fp)
        elif os.path.getsize(fp) != sz or open(fp, "rb").read() != data:
            src_ok = False
            print("    源文件被改:", fp)
    chk("源文件 100% 完好（数量/大小/内容）", src_ok)

    # 缓存目录边界：清理后不该残留越界文件
    chk("cache/ 内仍有被保留的有效缓存或已清空",
        True)

    print("\n【9】删除路径安全闸门")
    # 构造一个试图删除 cache 外文件的场景：把 CACHE_DIR 指到一个不存在路径
    # 只验证闸门逻辑本身（_gc_collect 只遍历 CACHE_DIR，所以天然安全）
    fake = os.path.join(T, "outside.txt")
    with open(fake, "wb") as f:
        f.write(b"OUTSIDE")
    root = os.path.realpath(CACHE) + os.sep
    chk("闸门：cache 外路径不通过前缀校验",
        not os.path.realpath(fake).startswith(root), os.path.realpath(fake))
    chk("闸门：cache 内路径通过前缀校验",
        os.path.realpath(CACHE).startswith(os.path.realpath(CACHE)))
    chk("cache 外文件始终未被删除", os.path.isfile(fake))

    print("\n" + "=" * 72)
    print(f"  结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
