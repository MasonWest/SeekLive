#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缩略图单档 640 · 规格统一与旧参数吸附自测
==========================================
覆盖实施计划 §六.A「规格统一」：

  A. 纯函数层 —— 常量收敛、吸附函数、variant 解析
  B. 端到端层 —— 真 Handler + 真 /thumb 请求：任意历史参数最终只落 640 一档，
     磁盘/清单里不出现 160/240/320/480/960，/photo 的 2048 行为不变

全部在**临时夹具**上跑（复用 test_access.setup_fixture），不碰真实图库与真实缓存。

运行：  python tests/test_thumb_spec.py
退出码 0 = 全绿。
"""

import os
import sys
import shutil
import tempfile
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
for _p in (APP, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import server as S            # noqa: E402
import test_access as fx      # noqa: E402

PASS, FAIL = [], []


def chk(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f"   [{detail}]" if detail else ""))


def sect(t):
    print(f"\n=== {t} ===")


# ---------------- A. 纯函数层 ----------------
def pure_checks():
    sect("A · 档位常量与吸附函数")

    chk("THUMB_KEEP_SIDES 收敛为 (640,)",
        tuple(S.THUMB_KEEP_SIDES) == (640,), str(S.THUMB_KEEP_SIDES))
    chk("THUMB_ALLOWED 引用启用集合（= (640,)）",
        tuple(S.THUMB_ALLOWED) == (640,), str(S.THUMB_ALLOWED))
    chk("THUMB_DEFAULT = 640（否则解析失败会生成旧档）",
        int(S.THUMB_DEFAULT) == 640, str(S.THUMB_DEFAULT))
    chk("thumb_enabled_sides() == {640}",
        S.thumb_enabled_sides() == {640}, str(S.thumb_enabled_sides()))
    chk("后台预热 _WARM_THUMB_SIDES 同步为 (640,)",
        tuple(S._WARM_THUMB_SIDES) == (640,), str(S._WARM_THUMB_SIDES))

    reqs = [None, "", "abc", "0", "-5", "1", "120", "160", "240", "320", "480",
            "640", "960", "999999", "319.9", " 640 "]
    got = [(r, S.pick_thumb(r)) for r in reqs]
    chk("pick_thumb 对所有输入（含畸形/边界/未知）恒返回 640",
        all(v == 640 for _, v in got), str(got))

    cases = [
        ("thumb|webp|640", 640),
        ("thumb|webp|480", 480),
        ("thumb|webp|320", 320),
        ("thumb|webp|240|trash:it-abc", 240),      # extra 段不影响解析
        ("thumb|webp|640|trash:it-abc", 640),
        ("thumb|webp| 640 ", 640),
        ("thumb|webp|abc", None),                  # 无法解析 → 必须保留
        ("thumb|webp", None),
        ("thumb", None),
        ("photo|webp|2048", None),                 # 非缩略图 → 不参与旧规格判定
        ("trans|h264-crf23|123", None),
        ("", None),
        (None, None),
    ]
    bad = [(v, S.thumb_side_of(v), e) for v, e in cases if S.thumb_side_of(v) != e]
    chk("thumb_side_of 解析正确（含 extra / 畸形 / 非缩略图）", not bad, str(bad))


# ---------------- B. 端到端层 ----------------
def endpoint_checks(base):
    sect("B · /thumb 端到端：旧参数一律吸附到 640")

    R = "2024-09/IMG_0001.JPG"
    q = "/thumb?lib=t&p=" + urllib.parse.quote(R)
    olds = ["160", "240", "320", "480", "960", "120", "0", "-5", "abc", ""]
    for s in olds:
        st, _, _ = fx.req(base, q + ("&s=" + s if s else ""))
        chk(f"/thumb?s={s or '(缺省)'} → 200（吸附，不报错）", st == 200, f"HTTP {st}")

    # 清单层：同一源图只应有 640 一档
    thumb_rows = [e for e in S.CACHE_INDEX.all_entries()
                  if e["kind"] == "thumb" and e["rel"] == R]
    sides = sorted({S.thumb_side_of(e["variant"]) for e in thumb_rows})
    chk("清单里该源图的缩略图只有 640 一档", sides == [640], f"sides={sides}")

    # 全局：清单里不允许存在任何非 640 的 thumb 记录
    all_sides = sorted({S.thumb_side_of(e["variant"])
                        for e in S.CACHE_INDEX.all_entries()
                        if e["kind"] == "thumb"})
    chk("全局清单不含任何旧规格 thumb 记录", all_sides == [640], f"sides={all_sides}")

    # 磁盘层：640 缓存文件确实落盘，且路径由清单正向推导可得
    ok_disk = True
    detail = []
    for e in thumb_rows:
        p = S.cache_file_for(e["key"], "thumb")
        if not os.path.isfile(p):
            ok_disk = False
            detail.append(p)
    chk("640 缩略图确实落盘（cache_file_for 正向推导命中）", ok_disk, str(detail[:2]))

    # /photo 不变：仍是 2048
    st, _, _ = fx.req(base, "/photo?lib=t&p=" + urllib.parse.quote(R) + "&fmt=jpeg")
    chk("/photo → 200", st == 200, f"HTTP {st}")
    ph = [e for e in S.CACHE_INDEX.all_entries()
          if e["kind"] == "photo" and e["rel"] == R]
    chk("/photo 记录仍为 2048（未随缩略图一起收敛）",
        bool(ph) and all(e["variant"].endswith("|2048") for e in ph),
        str([e["variant"] for e in ph]))

    # 磁盘反查：盘上每个缓存文件都能在清单里找到，且 thumb 记录均为 640
    files = S._scan_cache(force=True, full=True)[0]
    rows = {e["key"]: e for e in S.CACHE_INDEX.all_entries()}
    unrecorded, bad_side = 0, []
    for p in files:
        name = os.path.basename(p)
        if ".tmp" in name.lower():
            continue
        e = rows.get(S._key_of_cache_name(name))
        if e is None:
            unrecorded += 1
        elif e["kind"] == "thumb" and S.thumb_side_of(e["variant"]) != 640:
            bad_side.append(e["variant"])
    chk("盘上无清单外的缓存文件（未产生旧档遗留）", unrecorded == 0, f"unrecorded={unrecorded}")
    chk("盘上 thumb 记录全部为 640", not bad_side, str(bad_side[:3]))


def main():
    tmp = tempfile.mkdtemp(prefix="seeklive_thumbspec_")
    print("=" * 66)
    print("  SeekLive · 缩略图单档 640 / 规格统一自测（hermetic）")
    print("=" * 66)
    fx.setup_fixture(tmp)
    S.ACCESS_PROTECT = False          # 本测试只关心规格，不重复覆盖权限矩阵

    pure_checks()

    httpd, base = fx.start_server()
    try:
        endpoint_checks(base)
    finally:
        httpd.shutdown()
        for store in (S.INDEX, S.CACHE_INDEX):
            try:
                if store:
                    store.close()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("   x", f)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
