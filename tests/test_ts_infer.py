# -*- coding: utf-8 -*-
"""
时间推算（IMG 编号）专项测试 —— 完全合成，不需要任何真实照片素材。
用法： python tests/test_ts_infer.py

背景
----
取值链的最后一环是「文件 mtime」，而 mtime 对手机相册来说只是「拷贝进硬盘的
那天」。新加一个图库后，几百张 EXIF 被导出工具洗掉的老照片会整批排到「今天」，
时间轴顶部就冒出一个毫无意义的当月分组。

本测试验证 infer_library_ts() 的行为边界：
  · 只在同目录锚点之间插值 / 取最近锚点，**绝不外推**
  · 锚点不足 3 个 → 不猜
  · 没有 IMG 编号 → 退到同目录锚点中位数
  · **已有真实时间的记录一律不动**
  · 视频不当锚点（mvhd 不可信）
  · 明显损坏的时间（早于 2000 年）会被重新推算
  · 幂等；可用 scan.inferTime=false 整体关闭

构造约定（和真实库一致）：
  锚点   = ts 是真实拍摄时间，mtime 是「拷贝进硬盘的那天」→ ts != mtime
  兜底值 = ts 就等于 mtime（pick_ts 取不到拍摄时间时的最后兜底）
"""
import os
import sys
import subprocess
import shutil

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server as S          # noqa: E402
import index_db             # noqa: E402

BASE = os.path.join(APP, "tmp", "ts_infer_test")
DB = os.path.join(BASE, "index.db")
LIB = "t"

PASS, FAIL = [], []
DAY = 86400

T2023 = 1672531200          # 2023-01-01 00:00 UTC，当作真实拍摄时间基准
COPY = 1791700000           # 「拷贝进硬盘的那天」，明显晚于所有拍摄时间


def chk(name, ok, info=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  →  {info}" if info else ""))


def fresh_db():
    if os.path.isdir(BASE):
        subprocess.run(["cmd", "/c", "rmdir", "/S", "/Q", BASE], capture_output=True)
        shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    S.INDEX = index_db.Index(DB)
    S.USE_TS_INFER = True
    S.USE_EXIF_TIME = True
    S.META["scan"] = dict(S.DEFAULT_SCAN)


def _row(rel, ts, mtime, kind="img"):
    name = os.path.basename(rel)
    return {"lib_id": LIB, "rel": rel, "name": name, "dir": os.path.dirname(rel),
            "stem": S.clean_stem(name), "ext": os.path.splitext(name)[1].lower(),
            "kind": kind, "size": 1234, "mtime": float(mtime), "ts": int(ts)}


def anchor(rel, real_ts, kind="img"):
    """有真实拍摄时间的记录（mtime = 拷贝日，所以 ts != mtime）"""
    return _row(rel, real_ts, COPY, kind)


def fallback(rel, kind="img"):
    """掉到 mtime 兜底的记录"""
    return _row(rel, COPY, COPY, kind)


def ts_of(rel):
    r = S.INDEX.file_row(LIB, rel)
    return r["ts"] if r else None


def run_infer():
    return S.infer_library_ts({"id": LIB})


def main():
    print("=" * 72)
    print("  时间推算（IMG 编号）专项测试 · 全合成，不碰真实图库")
    print("=" * 72)
    fresh_db()

    # ---------------- 1) 同目录编号插值 ----------------
    print("\n1) 同目录内按 IMG 编号线性插值")
    S.INDEX.upsert_files([
        anchor("a/IMG_0100.JPG", T2023),
        anchor("a/IMG_0200.JPG", T2023 + 100 * DAY),
        anchor("a/IMG_0300.JPG", T2023 + 200 * DAY),
        fallback("a/IMG_0150.JPG"),
    ])
    n, anchors = run_infer()
    v = ts_of("a/IMG_0150.JPG")
    chk("兜底文件被推算（计数 1）", n == 1, f"n={n} anchors={anchors}")
    chk("插值落在两个锚点之间",
        T2023 <= v <= T2023 + 100 * DAY, f"ts={v}")
    chk("插值接近中点（50 天处）",
        abs(v - (T2023 + 50 * DAY)) <= 2 * DAY,
        f"偏移 {abs(v - (T2023 + 50 * DAY)) / DAY:.1f} 天")
    chk("原锚点未被改动",
        ts_of("a/IMG_0100.JPG") == T2023
        and ts_of("a/IMG_0300.JPG") == T2023 + 200 * DAY)

    # ---------------- 2) 不外推 ----------------
    print("\n2) 编号超出锚点范围 → 夹到最近锚点，不外推")
    S.INDEX.upsert_files([fallback("a/IMG_9999.JPG")])
    run_infer()
    v = ts_of("a/IMG_9999.JPG")
    limit = T2023 + 200 * DAY + 30 * DAY        # 锚点上限 + 10% 余量
    chk("没有推到锚点范围之外", v <= limit, f"ts={v} 上限≈{limit}")

    # ---------------- 3) 锚点不足 → 不猜 ----------------
    print("\n3) 同目录锚点少于 3 个（且顶层也少）→ 保持原样")
    S.INDEX.upsert_files([
        anchor("thin/IMG_0001.JPG", T2023),
        anchor("thin/IMG_0002.JPG", T2023 + 10 * DAY),
        fallback("thin/IMG_0003.JPG"),
    ])
    run_infer()
    chk("锚点不足时不动（仍是 mtime）", ts_of("thin/IMG_0003.JPG") == COPY,
        f"ts={ts_of('thin/IMG_0003.JPG')}")

    # ---------------- 4) 无编号 → 同目录中位数 ----------------
    print("\n4) 没有 IMG 编号 → 取同目录锚点中位数")
    S.INDEX.upsert_files([
        anchor("b/IMG_0100.JPG", T2023),
        anchor("b/IMG_0200.JPG", T2023 + 100 * DAY),
        anchor("b/IMG_0300.JPG", T2023 + 200 * DAY),
        fallback("b/shot_ab12cd.png"),
    ])
    run_infer()
    v = ts_of("b/shot_ab12cd.png")
    chk("无编号文件取到了中位数", v == T2023 + 100 * DAY, f"ts={v}")

    # ---------------- 5) 已有真实时间的绝不动 ----------------
    print("\n5) 已有真实时间（ts != mtime）的记录一律不动")
    real = T2023 + 33 * DAY
    S.INDEX.upsert_files([anchor("a/IMG_0155.JPG", real)])
    run_infer()
    chk("真实时间未被覆盖", ts_of("a/IMG_0155.JPG") == real,
        f"ts={ts_of('a/IMG_0155.JPG')}")

    # ---------------- 6) 视频不当锚点 ----------------
    print("\n6) 视频不参与建模（mvhd 不可信，不算锚点）")
    # c 目录只有 2 个**图片**锚点；若把视频也算进去就成了 3 个 → 会误推。
    S.INDEX.upsert_files([
        anchor("c/IMG_0100.JPG", T2023),
        anchor("c/IMG_0200.JPG", T2023 + 10 * DAY),
        anchor("c/IMG_0150.MOV", T2023 + 5 * DAY, kind="mov"),
        fallback("c/IMG_9999.JPG"),
    ])
    run_infer()
    chk("视频未被当成锚点（仍只有 2 个图片锚点 → 不动）",
        ts_of("c/IMG_9999.JPG") == COPY, f"ts={ts_of('c/IMG_9999.JPG')}")

    # ---------------- 7) 损坏时间（早于 2000 年）会被救回 ----------------
    print("\n7) 明显损坏的时间（1991 年）也被重新推算")
    S.INDEX.upsert_files([
        _row("d/IMG_9000.MOV", 673000000, COPY, kind="mov"),   # 1991 年，损坏的 mvhd
        anchor("d/IMG_0100.JPG", T2023),
        anchor("d/IMG_0200.JPG", T2023 + 100 * DAY),
        anchor("d/IMG_0300.JPG", T2023 + 200 * DAY),
    ])
    run_infer()
    v = ts_of("d/IMG_9000.MOV")
    chk("1991 年的坏值被改到合理范围", v >= S.TS_ANCHOR_FLOOR, f"ts={v}")

    # ---------------- 8) 幂等 ----------------
    print("\n8) 幂等：再跑一次不应再改动")
    n2, _ = run_infer()
    chk("第二次运行 0 改动", n2 == 0, f"n={n2}")

    # ---------------- 9) 开关 ----------------
    print("\n9) scan.inferTime = false → 整体关闭")
    S.INDEX.upsert_files([
        anchor("e/IMG_0100.JPG", T2023),
        anchor("e/IMG_0200.JPG", T2023 + 100 * DAY),
        anchor("e/IMG_0300.JPG", T2023 + 200 * DAY),
        fallback("e/IMG_0150.JPG"),
    ])
    S.USE_TS_INFER = False
    n3, a3 = run_infer()
    chk("关闭后不推算", n3 == 0 and a3 == 0, f"n={n3}")
    chk("关闭时保持 mtime", ts_of("e/IMG_0150.JPG") == COPY)
    S.USE_TS_INFER = True
    n4, _ = run_infer()
    chk("重新打开后能补上", n4 == 1, f"n={n4}")

    # ---------------- 10) 不碰任何源文件 ----------------
    print("\n10) 只改索引，不碰源文件")
    probe = os.path.join(APP, "server.py")
    st0 = os.stat(probe).st_mtime
    run_infer()
    chk("源文件 mtime 未被改动", os.stat(probe).st_mtime == st0)

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
