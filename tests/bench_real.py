# -*- coding: utf-8 -*-
"""验收计时：真实 libraries.json + 独立临时索引库，不触碰 data/index.db
用法： python tests/bench_real.py
"""
import os
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import server as S          # noqa: E402

BENCH = os.path.join(APP, "tmp", "bench")
os.makedirs(BENCH, exist_ok=True)
DB = os.path.join(BENCH, "bench.db")
for f in (DB, DB + "-wal", DB + "-shm"):
    if os.path.exists(f):
        os.remove(f)

S.INDEX_DB = DB
S.INDEX = None


def snapshot(tag, t0, t1):
    rows = []
    tot_mov = 0
    for lid in S.META["lib_order"]:
        e = S.META["libs"][lid]
        tot_mov += e.get("movParsed", 0)
        rows.append(f"    {e['name']:<10} {e['count']:>6,} 项 / Live {e['live']:>2} "
                    f"/ {e['scan_ms']:>5} ms / MOV重解析 {e.get('movParsed', 0)}"
                    f" (+{e.get('added',0)} ~{e.get('changed',0)} -{e.get('removed',0)})")
    print(f"  [{tag}] {t1 - t0:.3f}s ｜ MOV 重解析合计 {tot_mov} 个")
    print("\n".join(rows))
    return t1 - t0, tot_mov


print("=" * 70)
print("  真实图库 索引计时验收")
print("=" * 70)

t0 = time.time(); S.load_libraries(full=True); t1 = time.time()
full_s, full_mov = snapshot("① 首次全量扫描", t0, t1)
print(f"    索引库：{S.INDEX.stats()}")

t0 = time.time(); S.load_libraries(full=False); t1 = time.time()
warm_s, warm_mov = snapshot("② 第二次启动（无变化）", t0, t1)

t0 = time.time(); S.load_libraries(full=False); t1 = time.time()
inc_s, inc_mov = snapshot("③ 增量核对（再跑一次）", t0, t1)

print("-" * 70)
print(f"  首次全量 {full_s:.3f}s ｜ 二次启动 {warm_s:.3f}s ｜ 增量核对 {inc_s:.3f}s")
print(f"  首次重解析 MOV {full_mov} 个 ｜ 之后重解析 MOV {warm_mov} 个 / {inc_mov} 个")
if warm_s > 0:
    print(f"  二次启动相对首次全量提速 ~{full_s / warm_s:.1f}×")
print("=" * 70)
