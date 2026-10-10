"""
photo_time_repair.py — 为「没有拍摄时间」的照片推算大致时间（v2）

时间来源优先级（实测得出，见 --cv 交叉验证）
============================================
  1. 图片 EXIF DateTimeOriginal(36867) / DateTime(306)   ← 最可信
  2. 视频：若同目录下有同名的静图（Live Photo），继承静图时间
     —— 实测 914 组配对中 37% 的 MOV 的 mvhd 时间是被改写过的，
        直接信 mvhd 会污染锚点，所以配对优先
  3. 视频：自身 mvhd creation_time
  4. 文件名里的 13/10 位时间戳
  5. 编号推算：只在**同一目录**的图片锚点里做
     · 夹在两锚点之间且两端时间递增 -> 线性插值
     · 否则 -> 取编号最近的那个锚点（不做全局外推）
     · 结果再夹到该目录锚点的时间范围内
  6. 无编号：取同目录锚点时间的中位数
  7. 都没有 -> 不动（保持原样）

模式
====
  report      只读侦察 + 抽样
  cv          交叉验证：留一法实测编号推算的误差
  overrides   生成 ts_overrides.json（rel -> ts），不动原文件
  apply       回写 mtime（需 --yes，先备份）
  restore     从备份恢复 mtime
"""

import os
import re
import csv
import sys
import json
import time
import bisect
import argparse
import statistics
from collections import defaultdict
from datetime import datetime

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

DEFAULT_ROOT = r"E:\photo back up\iphone"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

IMG_RE = re.compile(r"^IMG_E?(\d+)", re.IGNORECASE)
TS_RE = re.compile(r"(?:^|[_\-\s])(\d{13}|\d{10})(?=[_\-\s.]|$)")

IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif",
           ".webp", ".gif", ".bmp", ".tif", ".tiff", ".mpo"}
VID_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}

TAG_DTO, TAG_DT = 36867, 306
QT_EPOCH_OFFSET = 2082844800
TS_MIN = int(datetime(2000, 1, 1).timestamp())
TS_MAX = int(time.time()) + 86400

DAY = 86400


# ================================================================ 时间提取

def _parse_dt(s):
    if not s:
        return None
    s = str(s).strip().replace("\x00", "")
    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
    if not m:
        return None
    try:
        y, mo, d, h, mi, se = (int(x) for x in m.groups())
        if y < 1970:
            return None
        ts = int(datetime(y, mo, d, h, mi, se).timestamp())
    except ValueError:
        return None
    return ts if TS_MIN <= ts <= TS_MAX else None


def img_exif_time(path):
    if Image is None:
        return None
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            if not ex:
                return None
            for tag in (TAG_DTO, TAG_DT):
                ts = _parse_dt(ex.get(tag))
                if ts:
                    return ts
            try:
                ifd = ex.get_ifd(0x8769)
                for tag in (TAG_DTO, TAG_DT):
                    ts = _parse_dt(ifd.get(tag))
                    if ts:
                        return ts
            except Exception:
                pass
    except Exception:
        return None
    return None


def mp4_creation_time(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    i = data.find(b"mvhd")
    if i < 0 or i + 12 > len(data):
        return None
    try:
        raw = (int.from_bytes(data[i + 8:i + 16], "big")
               if data[i + 4] == 1 else int.from_bytes(data[i + 8:i + 12], "big"))
    except Exception:
        return None
    if raw <= 0:
        return None
    ts = raw - QT_EPOCH_OFFSET
    return ts if TS_MIN <= ts <= TS_MAX else None


def name_ts(name):
    for m in TS_RE.finditer(name):
        v = int(m.group(1))
        if len(m.group(1)) == 13:
            v //= 1000
        if TS_MIN <= v <= TS_MAX:
            return v
    return None


# ================================================================ 扫描 / 建模

def walk(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace("\\", "/")
            out.append((rel, fn, st.st_size, st.st_mtime))
    return out


def collect(root, limit=None, verbose=True):
    files = sorted(walk(root))
    if limit:
        files = files[:limit]
    recs = []
    for i, (rel, name, size, mtime) in enumerate(files, 1):
        ext = os.path.splitext(name)[1].lower()
        m = IMG_RE.match(os.path.splitext(name)[0])
        r = {"rel": rel, "name": name, "ext": ext,
             "dir": os.path.dirname(rel),
             "num": int(m.group(1)) if m else None,
             "size": size, "mtime": mtime,
             "real": None, "suggest": None, "method": "none"}
        if ext in IMG_EXT:
            r["real"] = img_exif_time(os.path.join(root, rel.replace("/", os.sep)))
        elif ext in VID_EXT:
            r["real"] = mp4_creation_time(os.path.join(root, rel.replace("/", os.sep)))
        if r["real"]:
            r["method"] = "exif" if ext in IMG_EXT else "mvhd"
        recs.append(r)
        if verbose and i % 500 == 0:
            print(f"    扫描 {i}/{len(files)} ...", flush=True)
    return recs


def resolve_videos(recs):
    """视频优先继承同目录同名静图的时间（Live Photo 的 mvhd 常常是被改写的）"""
    still = {}
    for r in recs:
        if r["ext"] in IMG_EXT and r["real"]:
            still[(r["dir"], os.path.splitext(r["name"])[0])] = r["real"]
    n = 0
    for r in recs:
        if r["ext"] not in VID_EXT:
            continue
        key = (r["dir"], os.path.splitext(r["name"])[0])
        if key in still:
            r["real"] = still[key]
            r["method"] = "pair"
            n += 1
    return n


class Model:
    """按目录分域的编号 -> 时间模型（只用图片锚点，避免视频污染）"""

    def __init__(self, recs):
        self.by_dir = defaultdict(dict)      # dir -> {num: ts}
        self.by_top = defaultdict(dict)      # 顶层目录 -> {num: ts}
        self.glob = {}                       # 全局兜底
        for r in recs:
            if r["ext"] not in IMG_EXT or not r["real"] or r["num"] is None:
                continue
            self.by_dir[r["dir"]].setdefault(r["num"], r["real"])
            top = r["dir"].split("/")[0]
            self.by_top[top].setdefault(r["num"], r["real"])
            self.glob.setdefault(r["num"], r["real"])

    @staticmethod
    def _nums(d):
        return sorted(d)

    def _predict_in(self, d, num):
        nums = self._nums(d)
        if not nums:
            return None, None
        i = bisect.bisect_left(nums, num)
        if i < len(nums) and nums[i] == num:
            return d[nums[i]], "exact"
        lo = nums[i - 1] if i > 0 else None
        hi = nums[i] if i < len(nums) else None
        if lo is not None and hi is not None and d[hi] > d[lo]:
            frac = (num - lo) / (hi - lo)
            return int(d[lo] + (d[hi] - d[lo]) * frac), "interp"
        # 两端不递增（编号非拍摄序）或单侧越界 -> 取编号最近的锚点，不外推
        cand = None
        if lo is not None and hi is not None:
            cand = lo if (num - lo) <= (hi - num) else hi
        else:
            cand = lo if lo is not None else hi
        return d[cand], "nearest"

    def predict(self, rec):
        """返回 (ts, method, scope)"""
        num = rec["num"]
        if num is None:
            return None, None, None
        for scope, d in (("dir", self.by_dir.get(rec["dir"], {})),
                         ("top", self.by_top.get(rec["dir"].split("/")[0], {})),
                         ("global", self.glob)):
            if len(d) < 3:
                continue
            ts, how = self._predict_in(d, num)
            if ts is None:
                continue
            # 夹到该域已知时间范围内，避免任何离群
            lo_t, hi_t = min(d.values()), max(d.values())
            span = max(hi_t - lo_t, 7 * DAY)
            ts = int(min(max(ts, lo_t - span * 0.1), hi_t + span * 0.1))
            if scope != "dir":
                how += f"({scope})"
            return ts, how, scope
        return None, None, None

    def folder_median(self, d):
        vals = list(d.values())
        return int(statistics.median(vals)) if len(vals) >= 3 else None


def plan(recs):
    model = Model(recs)
    for r in recs:
        if r["real"]:
            r["suggest"], r["method"] = r["real"], r["method"]
            continue
        nts = name_ts(r["name"])
        if nts:
            r["suggest"], r["method"] = nts, "name-ts"
            continue
        ts, how, _ = model.predict(r)
        if ts:
            r["suggest"], r["method"] = ts, how
            continue
        med = model.folder_median(model.by_dir.get(r["dir"], {}))
        if med is None:
            med = model.folder_median(model.by_top.get(r["dir"].split("/")[0], {}))
        if med:
            r["suggest"], r["method"] = med, "folder-median"
    return model


# ================================================================ 交叉验证

def cross_validate(root, recs, model):
    """留一法：把每个图片锚点藏起来，用模型的其余锚点预测它，统计误差"""
    anchors = []
    for r in recs:
        if r["ext"] in IMG_EXT and r["real"] and r["num"] is not None:
            anchors.append(r)
    errs, missing = [], 0
    for r in anchors:
        d = model.by_dir.get(r["dir"], {})
        d2 = {k: v for k, v in d.items() if k != r["num"]}
        if len(d2) < 3:
            d2 = {k: v for k, v in model.by_top.get(r["dir"].split("/")[0], {}).items()
                  if k != r["num"]}
        if len(d2) < 3:
            missing += 1
            continue
        ts, how = model._predict_in(d2, r["num"])
        if ts is None:
            missing += 1
            continue
        errs.append(abs(ts - r["real"]))
    if not errs:
        print("样本不足，无法交叉验证")
        return
    errs.sort()

    def pct(x):
        return bisect.bisect_right(errs, x) / len(errs)

    print("\n" + "=" * 66)
    print("交叉验证（留一法，仅用编号推算，不含 EXIF 本身）")
    print("=" * 66)
    print(f"参与评估的锚点: {len(errs):,} 个（{missing} 个样本邻居不足被跳过）")
    print(f"  中位误差      : {errs[len(errs)//2]/3600:,.1f} 小时"
          f"  ({errs[len(errs)//2]/DAY:.2f} 天)")
    print(f"  平均误差      : {statistics.mean(errs)/DAY:,.2f} 天")
    for label, lim in (("≤1 小时", 3600), ("≤6 小时", 6 * 3600),
                       ("≤1 天", DAY), ("≤3 天", 3 * DAY), ("≤7 天", 7 * DAY)):
        print(f"  误差 {label:<8}: {pct(lim):>6.1%}")
    print(f"  误差 >30 天   : {1-pct(30*DAY):>6.1%}")


# ================================================================ 输出

def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def report(recs, root):
    n = len(recs)
    cnt = defaultdict(int)
    for r in recs:
        cnt[r["method"]] += 1
    print("\n" + "=" * 66)
    print("侦察报告")
    print("=" * 66)
    print(f"图库根目录        : {root}")
    print(f"文件总数          : {n:,}")
    print(f"有真实拍摄时间    : "
          f"{cnt['exif'] + cnt['mvhd'] + cnt['pair'] + cnt['name-ts']:,}"
          f"  (图片EXIF {cnt['exif']:,} / 继承配对 {cnt['pair']:,}"
          f" / 视频mvhd {cnt['mvhd']:,} / 文件名 {cnt['name-ts']:,})")
    infer = sum(v for k, v in cnt.items()
                if k.startswith(("interp", "nearest")))
    print(f"靠编号推算        : {infer:,}")
    print(f"靠目录中位数推算  : {cnt['folder-median']:,}")
    print(f"完全无法确定      : {cnt['none']:,}")
    print("\n-- 按方法 --")
    for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
        if v:
            print(f"    {k:<22} {v:>6,}")

    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, "time_report.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["rel", "name", "img_num", "mtime", "suggest_ts",
                    "suggest_time", "method"])
        for r in recs:
            w.writerow([r["rel"], r["name"], r["num"] or "", int(r["mtime"]),
                        r["suggest"] or "", fmt(r["suggest"]), r["method"]])
    print(f"\n明细: {csv_path}")


# 服务端自己能算出来的来源（EXIF / 配对继承 / mvhd / 文件名时间戳）——
# 这些不必进覆盖表，写了也会被服务端的原生链路优先覆盖。
NATIVE_METHODS = {"exif", "pair", "mvhd", "name-ts"}


def write_overrides(recs, path):
    """
    只写「服务端算不出来」的那部分（编号插值 / 目录中位数），
    服务端把它作为取值链的第 5 环兜底。
    """
    d = {}
    for r in recs:
        ts = r["suggest"]
        if not ts or r["method"] == "none" or r["method"] in NATIVE_METHODS:
            continue
        if abs(ts - r["mtime"]) < DAY:
            continue
        d[r["rel"]] = int(ts)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"_note": "tools/photo_time_repair.py 生成：rel -> 推算拍摄时间(epoch)。"
                            "仅含编号插值/目录中位数推算；EXIF 等原生来源由服务端自行读取。",
                   "overrides": d}, f, ensure_ascii=False, indent=1)
    print(f"覆盖表: {path}（{len(d):,} 条，仅推算部分）")


def apply_mtime(root, recs, backup_path, yes=False):
    todo = [r for r in recs if r["suggest"] and r["method"] != "none"
            and abs(r["suggest"] - r["mtime"]) >= DAY]
    print(f"\n待回写 mtime: {len(todo):,} 个")
    if not yes:
        for r in todo[:10]:
            print(f"   {fmt(r['mtime'])} -> {fmt(r['suggest'])}  {r['rel']}")
        print("（未加 --yes，未改动任何文件）")
        return
    os.makedirs(os.path.dirname(os.path.abspath(backup_path)), exist_ok=True)
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump({r["rel"]: r["mtime"] for r in todo}, f, ensure_ascii=False, indent=1)
    print(f"原始 mtime 已备份: {backup_path}")
    ok = fail = 0
    for r in todo:
        try:
            os.utime(os.path.join(root, r["rel"].replace("/", os.sep)),
                     (r["suggest"], r["suggest"]))
            ok += 1
        except OSError as e:
            fail += 1
            if fail <= 5:
                print("   失败:", r["rel"], e)
    print(f"完成: 成功 {ok:,} / 失败 {fail:,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["report", "cv", "overrides", "apply", "restore"])
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--backup", default=os.path.join(OUT_DIR, "mtime_backup.json"))
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        print("目录不存在:", args.root)
        sys.exit(1)

    if args.mode == "restore":
        with open(args.backup, encoding="utf-8") as f:
            d = json.load(f)
        print(f"恢复 {len(d):,} 个文件的 mtime")
        if not args.yes:
            print("（未加 --yes，未改动）")
            return
        ok = sum(1 for rel, mt in d.items()
                 if _try_utime(os.path.join(args.root, rel.replace("/", os.sep)), mt))
        print(f"恢复完成: {ok:,}/{len(d):,}")
        return

    print(f"扫描 {args.root} ...")
    t0 = time.time()
    recs = collect(args.root, args.limit)
    print(f"共 {len(recs):,} 个文件，用时 {time.time()-t0:.1f}s")
    npair = resolve_videos(recs)
    print(f"其中 {npair:,} 个视频从配对的静图继承了时间")
    model = plan(recs)

    if args.mode == "report":
        report(recs, args.root)
    elif args.mode == "cv":
        cross_validate(args.root, recs, model)
    elif args.mode == "overrides":
        report(recs, args.root)
        write_overrides(recs, args.out or os.path.join(OUT_DIR, "ts_overrides.json"))
    elif args.mode == "apply":
        report(recs, args.root)
        apply_mtime(args.root, recs, args.backup, args.yes)


def _try_utime(p, ts):
    try:
        os.utime(p, (ts, ts))
        return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
