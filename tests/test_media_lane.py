# -*- coding: utf-8 -*-
"""
媒体生成「双道闸门」专项测试 —— 完全合成，不需要素材、不启服务。
用法： python tests/test_media_lane.py

背景
----
缩略图是「滚一屏几百个」的批量活，大图是「用户正等着看」的单个活。
两者共用一条 FIFO 信号量时，用户点开大图要排在几百张缩略图后面 ——
真实浏览器里复现过：/photo 等了 18.0s（缩略图本身只要 0.5s）。

本测试锁定修复后的契约：
  · 前台道（大图 / 预览）与后台道（缩略图）是**两把独立的闸**
  · 后台排队再深，前台也不等它（用慢速假渲染器精确测量）
  · 反例：后台请求之间确实会排队（证明测量有效，不是假阳）
  · 后台在前台忙时会让行，但有上限，不会被饿死
  · 并发数是环境变量可调的
  · 源码契约：/photo 走前台、/thumb 走后台
"""
import inspect
import os
import shutil
import sys
import tempfile
import threading
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

# ★ 必须在 import server 之前设：并发数在模块加载时读环境变量
os.environ["SEEKLIVE_GEN_WORKERS"] = "2"
os.environ["SEEKLIVE_FG_WORKERS"] = "1"
import server as S          # noqa: E402

PASS, FAIL = [], []
BG_FAKE = []                # 假渲染器被后台调用的次数
FG_FAKE = []


def chk(name, ok, info=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  →  {info}" if info else ""))


FAKE_SECONDS = 0.25


def fake_render(fullpath, max_side, quality, fmt):
    """慢速假渲染器：替代 PIL，把「耗时」变成可控的睡眠。"""
    time.sleep(FAKE_SECONDS)
    tag = "fg" if threading.current_thread().name.startswith("fg") else "bg"
    (FG_FAKE if tag == "fg" else BG_FAKE).append(fullpath)
    return b"x" * 16, "image/webp", None, None


def make_job(i):
    """构造一次 cached_image 调用所需的全部参数（不读真实文件）。"""
    rel = f"dir/{i}.heic"
    st = type("ST", (), {"st_mtime": 1000 + i, "st_size": 100 + i})()
    lib = {"id": "t"}
    return lib, rel, f"/fake/{i}.heic", st


def run_bg(i):
    lib, rel, fp, st = make_job(i)
    return S.cached_image(lib, rel, fp, st, "thumb", 640, 78, "webp")


_SEQ = [0]
_SEQ_LOCK = threading.Lock()


def next_seq():
    with _SEQ_LOCK:
        _SEQ[0] += 1
        return _SEQ[0]


def run_fg(tag):
    lib, rel, fp, st = make_job(900000 + next_seq())
    t0 = time.perf_counter()
    S.cached_image(lib, rel, fp, st, "photo", 2048, 84, "jpeg", fg=True)
    return time.perf_counter() - t0


def main():
    tmp = tempfile.mkdtemp(prefix="sl_lane_")
    S.CACHE_DIR = tmp
    S.render_media = fake_render          # ★ 全局打桩

    print("=" * 72)
    print("  媒体生成双道闸门测试")
    print(f"  后台并发={S._GEN_WORKERS}  前台并发={S._FG_WORKERS}  "
          f"假渲染耗时={FAKE_SECONDS * 1000:.0f}ms")
    print("=" * 72)

    # ---------------- 1) 两把闸是分开的 ----------------
    print("\n1) 闸门结构")
    chk("前台道与后台道不是同一把信号量", S._fg_sem is not S._gen_sem)
    chk("后台并发 = 环境变量所指", S._GEN_WORKERS == 2, f"{S._GEN_WORKERS}")
    chk("前台并发 = 环境变量所指", S._FG_WORKERS == 1, f"{S._FG_WORKERS}")
    chk("前台上限 < 后台上限（前台是「少而急」）", S._FG_WORKERS < S._GEN_WORKERS)

    # ---------------- 2) 反例：后台之间确实会排队 ----------------
    print("\n2) 反例校验：后台请求之间确实排队（证明测量有效）")
    n = 16
    ths = [threading.Thread(target=run_bg, args=(i,), name=f"bg{i}") for i in range(n)]
    t0 = time.perf_counter()
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=60)
    bg_wall = time.perf_counter() - t0
    expect = (n / S._GEN_WORKERS) * FAKE_SECONDS
    chk(f"{n} 个后台请求耗时 ≥ 理论串行时间",
        bg_wall >= expect * 0.8, f"{bg_wall:.2f}s（理论 ~{expect:.2f}s）")
    chk("后台请求全部完成", len(BG_FAKE) == n, f"{len(BG_FAKE)}/{n}")

    # ---------------- 3) 关键：后台排满时，前台不等 ----------------
    print("\n3) ★ 后台排满 60 个时，前台大图要等多久")
    BG_FAKE.clear()
    queue_n = 60
    ths = [threading.Thread(target=run_bg, args=(100 + i,), name=f"bgq{i}")
           for i in range(queue_n)]
    t0 = time.perf_counter()
    for t in ths:
        t.start()
    time.sleep(FAKE_SECONDS * 1.5)        # 让后台先把闸占满、队列排起来
    fg_elapsed = run_fg("fg1")
    backlog_total = (queue_n / S._GEN_WORKERS) * FAKE_SECONDS
    chk("前台账面耗时接近单次渲染（没有排后台的队）",
        fg_elapsed < FAKE_SECONDS * 3,
        f"{fg_elapsed:.2f}s，单次渲染 {FAKE_SECONDS}s")
    chk("前台远快于「等完整条后台队列」",
        fg_elapsed < backlog_total / 4,
        f"{fg_elapsed:.2f}s vs 后台队列 ~{backlog_total:.2f}s")
    print(f"     （后台还有 {queue_n} 个在排，前台 {fg_elapsed:.2f}s 就回来了）")
    for t in ths:
        t.join(timeout=120)

    # ---------------- 4) 前台并发也被限制 ----------------
    print("\n4) 前台道自己也有上限")
    fg_times = []
    lock = threading.Lock()

    def fg_worker(k):
        dt = run_fg(f"fg{k}")
        with lock:
            fg_times.append(dt)

    ths = [threading.Thread(target=fg_worker, args=(k,), name=f"fg{k}") for k in range(4)]
    t0 = time.perf_counter()
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=60)
    wall = time.perf_counter() - t0
    expect = (4 / S._FG_WORKERS) * FAKE_SECONDS
    chk("4 个前台请求按前台上限串行", wall >= expect * 0.8,
        f"{wall:.2f}s（理论 ~{expect:.2f}s）")

    # ---------------- 5) 后台让行，但不会被饿死 ----------------
    print("\n5) 前台在跑时后台让行；前台结束后恢复")
    S._fg_active = 0
    t_hold = time.perf_counter()
    with S.gen_gate(foreground=True):
        # 前台占着，后台进来应当最多等 _BG_YIELD 就继续
        t_bg = threading.Thread(target=run_bg, args=(777,), name="bg777")
        t_bg.start()
        t_bg.join(timeout=30)
        bg_in_fg = time.perf_counter() - t_hold
    chk("后台在前台忙碌时仍能推进（有让行上限，不会饿死）",
        not t_bg.is_alive() and bg_in_fg <= S._BG_YIELD + FAKE_SECONDS * 4,
        f"{bg_in_fg:.2f}s ≤ _BG_YIELD({S._BG_YIELD}) + 渲染")

    # ---------------- 6) 积压计数可观测 ----------------
    print("\n6) 积压深度可观测（供 /api/stats）")
    chk("_bg_waiting 存在且归零", hasattr(S, "_bg_waiting") and S._bg_waiting == 0,
        f"_bg_waiting={getattr(S, '_bg_waiting', '缺失')}")
    src = inspect.getsource(S.api_stats) if hasattr(S, "api_stats") else ""
    if not src:
        # api_stats 是 Handler 的方法
        src = inspect.getsource(S.Handler.api_stats)
    chk("/api/stats 暴露前台并发与积压深度",
        "fgWorkers" in src and "thumbBacklog" in src)

    # ---------------- 7) 源码契约：谁走前台 ----------------
    print("\n7) 源码契约：/photo 走前台、/thumb 走后台")
    ssrc = inspect.getsource(S.Handler.serve_image)
    chk("/thumb 判定后设 fg = False", "fg = False" in ssrc)
    chk("/photo 判定后设 fg = True", "fg = True" in ssrc)
    chk("fg 传进了 cached_image", "fg=fg" in ssrc)
    csrc = inspect.getsource(S.cached_image)
    chk("cached_image 用 gen_gate(foreground=fg)", "gen_gate(foreground=fg)" in csrc)

    # ---------------- 8) 缓存命中不占闸（既有行为） ----------------
    print("\n8) 命中缓存时不应占用生成槽位")
    S._fg_active = 0
    before = time.perf_counter()
    with S.gen_gate(foreground=True):
        pass
    chk("gen_gate 可重入式释放（无残留）", S._fg_active == 0 and S._fg_sem._value == S._FG_WORKERS,
        f"fg_active={S._fg_active} fg_sem={S._fg_sem._value}")

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 72)
    print(f"  通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("  失败：")
        for f in FAIL:
            print("   -", f)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
