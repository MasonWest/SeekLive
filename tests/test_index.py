# -*- coding: utf-8 -*-
"""
索引 / 增量扫描 / 目录语义 专项测试（在独立副本 + 独立测试数据库上运行，不碰真实库）
用法： python tests/test_index.py

测试素材（HEIC / MOV / JPEG 原件）来源按以下顺序解析，**代码里不写任何私人路径**：
  1) 环境变量 SEEKLIVE_TEST_SRC
  2) 同目录 libraries.json 里 id 为 live 的图库 root
两处都没有时，本脚本打印「跳过」并退出（0），不视为失败。
"""
import os
import sys
import json
import shutil
import subprocess
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)


def _resolve_src():
    """解析测试素材目录；找不到返回空串（调用方据此跳过）。"""
    p = os.environ.get("SEEKLIVE_TEST_SRC")
    if p and os.path.isdir(p):
        return p
    try:
        with open(os.path.join(APP, "libraries.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        for lib in cfg.get("libraries") or []:
            root = lib.get("root") or ""
            if lib.get("id") == "live" and os.path.isdir(root):
                return root
    except Exception:
        pass
    return ""


SRC = _resolve_src()

import server as S          # noqa: E402
import index_db             # noqa: E402

BASE = os.path.join(APP, "tmp", "idx_test")
ROOT = os.path.join(BASE, "root")
DB = os.path.join(BASE, "index.db")

PASS, FAIL = [], []


def chk(name, ok, info=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  →  {info}" if info else ""))


def build_tree():
    if os.path.isdir(BASE):
        subprocess.run(["cmd", "/c", "rmdir", "/S", "/Q", BASE],
                       capture_output=True)
    if os.path.isdir(BASE):
        shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)

    def mk(*p):
        d = os.path.join(ROOT, *p)
        os.makedirs(d, exist_ok=True)
        return d

    # 嵌套 年/月 + 两组 Live Photo（HEVC / H264）+ 同名不同扩展名
    d = mk("2025", "08")
    for f in ("IMG_3708.HEIC", "IMG_3708.MOV", "IMG_4815.JPEG", "IMG_4815.MOV"):
        shutil.copy(os.path.join(SRC, f), d)
    shutil.copy(os.path.join(SRC, "IMG_3708.HEIC"), os.path.join(d, "IMG_3708.JPG"))
    # 深层嵌套
    d = mk("deep", "a", "b", "c")
    shutil.copy(os.path.join(SRC, "IMG_4823.JPG"), d)
    # 独立视频（无同名图）
    d = mk("videos")
    shutil.copy(os.path.join(SRC, "IMG_3708.MOV"), os.path.join(d, "clip_hevc.MOV"))
    # 隐藏目录 / 系统目录（都应跳过）
    for junk in (".hidden", ".git", "__MACOSX", "@eaDir"):
        d = mk(junk)
        shutil.copy(os.path.join(SRC, "IMG_4819.JPEG"), d)
    # 同名 a.png / a.jpg（无视频）
    d = mk("same_stem")
    shutil.copy(os.path.join(SRC, "IMG_4819.JPEG"), os.path.join(d, "a.jpg"))
    shutil.copy(os.path.join(SRC, "IMG_4815.JPEG"), os.path.join(d, "a.png"))
    # junction（应跳过）
    subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(ROOT, "linked_2025"),
                    os.path.join(ROOT, "2025")], capture_output=True)


def fresh_index():
    try:
        os.remove(DB)
    except OSError:
        pass
    for suf in ("-wal", "-shm"):
        try:
            os.remove(DB + suf)
        except OSError:
            pass
    S.INDEX = index_db.Index(DB)
    S.META["scan"] = dict(S.DEFAULT_SCAN)


def scan(full=False):
    lib = {"id": "t", "name": "测试库", "root": ROOT, "ignore": [],
           "_ignoreHidden": True}
    t0 = time.perf_counter()
    S.scan_library(lib, full=full)
    return lib, (time.perf_counter() - t0) * 1000


def main():
    """备份真实 libraries.json → 跑测试 → 无论成败都还原，避免污染真实配置。"""
    cfg_bak = None
    if os.path.isfile(S.CONFIG_PATH):
        with open(S.CONFIG_PATH, "rb") as f:
            cfg_bak = f.read()
    try:
        return _run()
    finally:
        if cfg_bak is not None:
            with open(S.CONFIG_PATH, "wb") as f:
                f.write(cfg_bak)
            print("  [已还原] libraries.json 恢复为运行前内容")
        else:
            try:
                os.remove(S.CONFIG_PATH)
            except OSError:
                pass


def _run():
    if not SRC:
        print("=" * 72)
        print("  跳过：未找到测试素材目录。")
        print("  请设置环境变量 SEEKLIVE_TEST_SRC 指向含 HEIC/MOV/JPEG 素材的目录，")
        print("  或在 libraries.json 里配置一个 id 为 live 的图库。")
        print("=" * 72)
        return 0
    print("=" * 72)
    print("  构建独立测试树")
    print("=" * 72)
    build_tree()
    for dp, dn, fn in os.walk(ROOT):
        rel = os.path.relpath(dp, ROOT)
        print(f"  {rel + '/':34s} {len(fn)} 文件")

    fresh_index()

    print("\n" + "=" * 72)
    print("1) 目录语义：是否跳过隐藏 / 系统 / junction")
    print("=" * 72)
    listed = S.walk_files(ROOT, ignore=S.IGNORE_DIRS, ignore_hidden=True)
    rels = [r for r, _n, _s, _m in listed]
    for bad, label in (("linked_2025", "junction"), (".hidden", "隐藏目录"),
                       (".git", "版本库"), ("__MACOSX", "macOS 垃圾"),
                       ("@eaDir", "群晖缩略图")):
        chk(f"跳过 {label}（{bad}）", not any(bad in r for r in rels))
    chk("递归进入普通子目录（deep/a/b/c）",
        any(r.startswith("deep/a/b/c/") for r in rels))
    chk("普通文件未被误伤", len(rels) >= 8, f"枚举 {len(rels)} 个文件")

    print("\n" + "=" * 72)
    print("2) 首次全量扫描")
    print("=" * 72)
    lib, ms = scan(full=True)
    chk("图片条目数正确", lib["count"] == 7, f"count={lib['count']}")
    chk("Live Photo 配对正确", lib["live"] == 2, f"live={lib['live']}")
    chk("独立视频被索引为 vid 条目",
        any(i["k"] == "vid" and i["p"] == "videos/clip_hevc.MOV" for i in lib["items"]))
    chk("同名不同扩展名各自独立保留",
        any(i["p"] == "same_stem/a.jpg" for i in lib["items"]) and
        any(i["p"] == "same_stem/a.png" for i in lib["items"]))
    chk("首次解析 MOV 2 个（两组 Live Photo）", lib["movParsed"] == 2,
        f"movParsed={lib['movParsed']}")
    print(f"    首次全量：{ms:.0f} ms  新增 {lib['added']} 项")

    print("\n" + "=" * 72)
    print("3) 第二次启动（无任何变化）")
    print("=" * 72)
    lib2, ms2 = scan(full=False)
    chk("重复启动不再重新解析任何 MOV", lib2["movParsed"] == 0,
        f"movParsed={lib2['movParsed']}")
    chk("无新增 / 变更 / 删除",
        lib2["added"] == 0 and lib2["changed"] == 0 and lib2["removed"] == 0,
        f"+{lib2['added']} ~{lib2['changed']} -{lib2['removed']}")
    chk("条目数保持一致", lib2["count"] == 7, f"count={lib2['count']}")
    print(f"    二次启动：{ms2:.0f} ms")

    print("\n" + "=" * 72)
    print("4) 增量：新增一组 Live Photo")
    print("=" * 72)
    d = os.path.join(ROOT, "2025", "09")
    os.makedirs(d, exist_ok=True)
    shutil.copy(os.path.join(SRC, "IMG_4877.JPEG"), d)
    shutil.copy(os.path.join(SRC, "IMG_4877.MOV"), d)
    lib3, _ = scan()
    chk("只解析新增的 1 个 MOV", lib3["movParsed"] == 1, f"movParsed={lib3['movParsed']}")
    chk("新增 2 项", lib3["added"] == 2, f"added={lib3['added']}")
    chk("旧文件未被重新解析（Live 总数 3）", lib3["live"] == 3, f"live={lib3['live']}")
    chk("count 增至 8（新静图 1 条，其 MOV 已被认领）", lib3["count"] == 8,
        f"count={lib3['count']}")

    print("\n" + "=" * 72)
    print("5) 增量：修改一个 MOV（模拟替换）")
    print("=" * 72)
    p = os.path.join(d, "IMG_4877.MOV")
    with open(p, "ab") as f:
        f.write(b"\x00" * 4096)
    lib4, _ = scan()
    chk("检测到 1 项变更", lib4["changed"] == 1, f"changed={lib4['changed']}")
    chk("只重新解析该 MOV（1 个）", lib4["movParsed"] == 1,
        f"movParsed={lib4['movParsed']}")

    print("\n" + "=" * 72)
    print("6) 增量：删除一个文件")
    print("=" * 72)
    os.remove(os.path.join(ROOT, "same_stem", "a.png"))
    lib5, _ = scan()
    chk("检测到 1 项删除", lib5["removed"] == 1, f"removed={lib5['removed']}")
    chk("a.png 已从索引移除",
        not any(i["p"] == "same_stem/a.png" for i in lib5["items"]))
    chk("a.jpg 仍在", any(i["p"] == "same_stem/a.jpg" for i in lib5["items"]))
    chk("删除不影响 MOV 解析", lib5["movParsed"] == 0,
        f"movParsed={lib5['movParsed']}")

    print("\n" + "=" * 72)
    print("7) 根目录离线：不得清空索引")
    print("=" * 72)
    before = lib5["count"]
    ghost = os.path.join(BASE, "not_there")
    lib6 = {"id": "t", "name": "测试库", "root": ghost, "ignore": [],
            "_ignoreHidden": True}
    prev = S.INDEX.lib_meta("t")
    keep = {"count": prev["count"], "live": prev["live"]}
    S.sync_memory(lib6)                    # 模拟「离线 → 从库里恢复」
    chk("离线后仍能从索引恢复条目", lib6["count"] == before,
        f"{before} → {lib6['count']}")
    chk("索引行未被删除", S.INDEX.lib_meta("t")["count"] == keep["count"])
    # 走完整 load_libraries 分支
    import json
    cfg = {"scan": S.DEFAULT_SCAN,
           "libraries": [{"id": "t", "name": "测试库", "root": ghost}]}
    with open(S.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    S.load_libraries()
    e = S.META["libs"]["t"]
    chk("load_libraries 下目录离线 → stale 且保留索引",
        e["stale"] is True and e["count"] == before and e["exists"] is False,
        f"count={e['count']} stale={e['stale']} exists={e['exists']}")

    print("\n" + "=" * 72)
    print(f"  通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("  失败：")
        for f in FAIL:
            print("   -", f)
    print("=" * 72)
    S.INDEX.close()
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
