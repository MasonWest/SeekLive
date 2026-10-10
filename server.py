#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · 时光轴（正式版 Demo）· 服务端
==========================================
把「企微图片时间轴 v6」的浏览体验，与本项目已验证的 Live Photo 播放能力合并成
一个可以直接跑、手机端体验友好的正式版服务。

核心能力
--------
  1. 多图库      一个图库配一个根目录，**默认递归扫描其下所有普通子目录**
                 （跳过隐藏目录 / 系统目录 / 符号链接与 junction）
  2. 持久化索引  SQLite 落盘（data/index.db）：首次全量、日常只做增量核对
  3. 增量识别    只有新增/修改的文件才重新解析 MOV、提取 Content Identifier
  4. 离线安全    根目录暂时不可访问时保留原索引，不当作空图库清空
  5. 分页 API    列表按「月 / 日 / 页」下发，手机端不下载大 JSON（流式按需）
  6. 服务端缓存  缩略图 / 大图 / 转码产物 / 视频抽帧 全部落盘，命中 2~9ms
  7. 流式视频    MOV/MP4 完整 Range（206）+ 分块发送，不整读入内存
  8. 编码兼容    HEVC(H.265) 自动转 H.264；Live Photo / 独立视频 能力路由
  9. 现代输出    WebP 缩略图 + EXIF 自动旋转 + JPEG 渐进式回退
 10. 安全边界    多根目录隔离，拒绝 .. / 盘符 / 符号链接穿越

用法
----
    python server.py                        # 使用 libraries.json 中配置的图库
    python server.py --lan                  # 允许局域网访问（手机用）
    python server.py --port 9000            # 指定端口
    python server.py --admin-token 你的口令   # 开启「源文件管理」授权（浏览始终免登录）
    python server.py --token 你的口令         # --admin-token 的兼容别名
                                          # 也可写同目录 admin.json（不进版本库）
    python server.py --recheck 300          # 后台定期核对间隔（秒，0=关闭）
    python server.py --rebuild-index        # 丢弃索引重建后退出
    python server.py --stop                 # 停止后台服务

数据目录：data/index.db（索引） ｜ cache/（缩略图/大图/转码） ｜ logs/（运行日志）
原始照片目录始终只读，不会被写入、修改或删除。

依赖：Pillow / pillow-heif / ffmpeg（HEVC 转码与视频抽帧，可选）
"""

import os
import sys
import io
import re
import json
import math
import time
import shutil
import socket
import hmac
import hashlib
import secrets
import threading
import mimetypes
import subprocess
import urllib.parse
import collections
import logging

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import index_db
import cache_db
import trash_db

# ---------- 编码健壮性（Windows 控制台） ----------
try:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
except Exception:
    pass

# =========================================================
#  常量与路径
# =========================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
CACHE_DIR = os.path.join(APP_DIR, "cache")
TMP_DIR = os.path.join(APP_DIR, "tmp")
LOG_DIR = os.path.join(APP_DIR, "logs")
DATA_DIR = os.path.join(APP_DIR, "data")          # 索引等数据（与照片目录完全分离）
INDEX_DB = os.path.join(DATA_DIR, "index.db")
CACHE_DB_MANIFEST = os.path.join(DATA_DIR, "cache.db")   # 缓存清单（回收缓存用）
TRASH_DB = os.path.join(DATA_DIR, "trash.db")            # 回收站账本
TRASH_DIR = os.path.join(DATA_DIR, "trash")              # 回收站文件区（必须在所有图库根目录之外）
CONFIG_PATH = os.path.join(APP_DIR, "libraries.json")

for _d in (CACHE_DIR, TMP_DIR, LOG_DIR, DATA_DIR, TRASH_DIR, WEB_DIR):
    os.makedirs(_d, exist_ok=True)

CACHE_VERSION = "v4"          # 处理算法/缓存键变更时 +1 → 旧缓存自然失效
CODEC_PROBE_VERSION = 2       # 码流探测算法变更时 +1 → 已缓存的 codec 重新探测

THUMB_DEFAULT = 320
THUMB_ALLOWED = (120, 160, 240, 320, 480, 640, 960)
THUMB_QUALITY = 78
PHOTO_MAX = 2048
PHOTO_QUALITY = 84
VIDEO_CHUNK = 256 * 1024
MAX_TRANSCODE_MB = 512        # 超过此体积不做「整文件转码」（避免首字节等几分钟）

IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
           ".heic", ".heif", ".tif", ".tiff", ".avif"}
# 视频容器：单 MOV / MP4 也作为一个条目展示在时间轴里
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".webm", ".3gp", ".mts", ".m2ts"}
MOV_EXT = VIDEO_EXT          # 兼容旧内部命名（历史代码里叫 MOV_EXT）

# 目录遍历时默认跳过的「非普通子目录」——这些目录里不会有用户照片
IGNORE_DIRS = {
    # 版本控制 / 依赖
    ".git", ".svn", ".hg", "node_modules", "bower_components",
    # 系统与回收站
    "$RECYCLE.BIN", "System Volume Information", "$WinREAgent",
    ".Trash", ".Trashes", "#recycle", ".recycle",
    # 相册 / 同步软件产生的缩略图与缓存
    "@eaDir", "#snapshot", ".thumbnails", ".cache", ".tmp",
    # macOS
    "__MACOSX", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100",
    ".TemporaryItems", ".AppleDouble", ".DS_Store",
    # 本项目自身的数据目录（避免把索引/缓存当照片扫进来）
    "cache", "data", "logs",
}

# 索引 / 扫描相关默认配置（可被 libraries.json 覆盖）
DEFAULT_SCAN = {
    "ignoreHidden": True,     # 跳过以 . 开头的目录
    "ignore": [],             # 追加跳过的目录名
    "recheckSeconds": 300,    # 后台定期核对间隔（秒），0 = 关闭
    "autoRescan": True,       # 启动后是否允许后台核对
}

# 回收站默认配置（可被 libraries.json 的 "trash" 段覆盖）
DEFAULT_TRASH = {
    "retentionDays": 30,          # 保留天数，到期后才允许永久删除
    "purgeIntervalSeconds": 3600,  # 后台过期检查间隔（秒），0 = 只靠启动时检查
}

# 默认图库（首次运行会写进 libraries.json，可直接编辑）
DEFAULT_LIBS = [
    {"id": "photos", "name": "我的照片",
     "root": r"D:\Photos\我的照片"},
    {"id": "live", "name": "Live Photo",
     "root": r"D:\Photos\LivePhoto"},
]

# ---------- 日志 ----------
log = logging.getLogger("seeklive")
log.setLevel(logging.INFO)
_fh = logging.FileHandler(os.path.join(LOG_DIR, "server.log"), encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_fh)

# ---------- MIME ----------
mimetypes.init()
for _e, _t in ((".heic", "image/heic"), (".heics", "image/heic"), (".heif", "image/heif"),
               (".avif", "image/avif"), (".mov", "video/quicktime"), (".m4v", "video/x-m4v"),
               (".webp", "image/webp"), (".ts", "video/mp2t")):
    mimetypes.add_type(_t, _e)

# =========================================================
#  全局状态
# =========================================================
META = {"libs": {}, "lib_order": [],
        "scan": dict(DEFAULT_SCAN), "trash": dict(DEFAULT_TRASH)}
PID_FILE = os.path.join(APP_DIR, "server.pid")
PORT_FILE = os.path.join(APP_DIR, "server.port")

INDEX = None                       # index_db.Index 实例（启动时打开）
INDEX_LOCK = threading.RLock()     # 串行化「扫描 → 交换内存索引」
CACHE_INDEX = None                 # cache_db.CacheIndex：缓存清单（记录谁生成了哪个缓存）
TRASH = None                       # trash_db.Trash：回收站账本
MGMT_LOCK = threading.RLock()      # 串行化「删除 / 恢复」等破坏性操作（防并发重复执行）
_inflight_batches = set()          # 正在执行的批次（同批次禁止并发重复执行）
_inflight_guard = threading.Lock()
_recheck_stop = threading.Event()
_purge_stop = threading.Event()     # 回收站到期检查线程的停止信号

# =========================================================
#  管理认证（独立于 libraries.json 的独立配置文件 admin.json）
# ---------------------------------------------------------
#  作用域：**只**保护源文件管理（删除 / 恢复 / 永久删除 / 清空回收站）。
#  浏览（页面 / 静态资源 / 照片 / 视频 / Live Photo / 普通浏览 API）**完全匿名**，
#  认证代码一行都不会加到那些路由上 —— 这是「浏览免登录」的硬保证。
#  口令本体只存在于内存，不落盘、不回显、不进日志、不进 URL。
# =========================================================
ADMIN_CFG_PATH = os.path.join(APP_DIR, "admin.json")
ADMIN_CFG = {"sessionHours": 12.0, "sessionCookieMaxAge": 0}
ADMIN_PW = None                     # 管理口令本体（仅内存）
ADMIN_SOURCE = ""                   # 口令来源："命令行" / "admin.json"
SESSION_COOKIE = "sl_admin"
ADMIN_FAIL_MAX = 5                  # 连续失败达到此数开始指数退避
_admin_sessions = {}                # sid -> 过期时间戳（0 = 不过期）
_admin_lock = threading.Lock()
_admin_fails = {}                   # ip -> {"n", "until", "at"}
_admin_fail_lock = threading.Lock()

# =========================================================
#  索引进度（轻量：一个 dict + 一把锁，**不引入任何任务系统**）
# ---------------------------------------------------------
#  唯一约定：total 为 None 表示「文件总数**尚未统计出来**」。
#  此时前端**不得**显示百分比，只显示「正在扫描图库，统计文件数量……」；
#  等 walk 完成、拿到真实总数后才开始展示进度条。
# =========================================================
IDX_PROGRESS = {
    "running": False,
    "phase": "idle",        # idle | counting | scanning | finalizing | done | error
    "mode": "",             # startup | full | incremental | recheck
    "stage": "",            # 中文阶段描述（直接展示给用户）
    "lib": "", "libName": "",
    "libIndex": 0, "libTotal": 0,
    "found": 0,             # counting 阶段已发现的文件数（无总数、无百分比）
    "total": None,          # scanning 阶段的总数；None = 尚未统计出来
    "processed": 0,
    "added": 0, "changed": 0, "removed": 0,          # 本次累计（跨图库）
    "libScanned": 0, "libAdded": 0, "libChanged": 0, "libRemoved": 0,  # 当前图库
    "errors": 0,            # 本次累计错误数（图库读不到 / 单库扫描异常）
    "offline": [],          # [{"id","name"}] 目录离线、保留旧索引的图库
    "startedAt": 0, "finishedAt": 0,
    "lastError": "",
}
IDX_LOCK = threading.Lock()
IDX_TICK = 200              # counting 阶段每发现多少个文件才落一次进度（降低加锁频率）
# 启动索引是否已完成。端口在索引起步前就监听，所以必须有一个显式的「就绪」信号，
# 客户端（含前端与测试）据此区分「还在建立索引」与「真的没有照片」。
BOOTSTRAP_DONE = threading.Event()


def idx_reset(mode):
    with IDX_LOCK:
        IDX_PROGRESS.update({
            "running": True, "phase": "counting", "mode": mode,
            "stage": "准备扫描…", "lib": "", "libName": "",
            "libIndex": 0, "libTotal": 0, "found": 0, "total": None,
            "processed": 0, "added": 0, "changed": 0, "removed": 0,
            "libScanned": 0, "libAdded": 0, "libChanged": 0, "libRemoved": 0,
            "errors": 0, "offline": [], "startedAt": int(time.time()),
            "finishedAt": 0, "lastError": "",
        })


def idx_set(**kw):
    with IDX_LOCK:
        # 只在索引周期内写进度：否则会把面板留在「scanning / running=False」这种半截状态
        if not IDX_PROGRESS["running"]:
            return
        IDX_PROGRESS.update(kw)


def idx_get():
    with IDX_LOCK:
        return dict(IDX_PROGRESS)


def idx_counting(lib, lib_index, lib_total):
    """进入「统计文件数量」阶段 —— 此时**没有总数**，前端不显示百分比。"""
    idx_set(phase="counting", lib=lib["id"], libName=lib["name"],
            libIndex=lib_index, libTotal=lib_total, found=0, total=None,
            processed=0,
            stage=f"正在扫描图库，统计文件数量……（{lib['name']}）")


def idx_counting_tick(n):
    """counting 阶段低频上报（纯计数，不产生百分比）。"""
    if n % IDX_TICK == 0:
        idx_set(found=n)


def idx_scanning(lib, total):
    """已拿到真实总数 → 从这里开始才允许出现百分比。"""
    idx_set(phase="scanning", lib=lib["id"], libName=lib["name"],
            total=total, processed=0, found=total,
            stage=f"正在建立索引：{lib['name']}")


def idx_lib_done(lib, scanned, added, changed, removed, errors=0):
    """单个图库扫完：累计全局增量统计，并刷新当前图库的增量数字。"""
    with IDX_LOCK:
        if not IDX_PROGRESS["running"]:
            return
        IDX_PROGRESS["added"] += added
        IDX_PROGRESS["changed"] += changed
        IDX_PROGRESS["removed"] += removed
        IDX_PROGRESS["errors"] += errors
        IDX_PROGRESS["processed"] = scanned
        IDX_PROGRESS["libScanned"] = scanned
        IDX_PROGRESS["libAdded"] = added
        IDX_PROGRESS["libChanged"] = changed
        IDX_PROGRESS["libRemoved"] = removed
        IDX_PROGRESS["phase"] = "finalizing"


def idx_offline(lib):
    """目录离线（保留旧索引）的图库登记，供面板提示「是否有图库离线」。"""
    with IDX_LOCK:
        if not IDX_PROGRESS["running"]:
            return
        IDX_PROGRESS["offline"].append({"id": lib["id"], "name": lib["name"]})


def idx_error(lib, msg="", count=1):
    with IDX_LOCK:
        if not IDX_PROGRESS["running"]:
            return
        IDX_PROGRESS["errors"] += count
        if msg:
            IDX_PROGRESS["lastError"] = msg


def idx_finish(summary=None, error=""):
    with IDX_LOCK:
        IDX_PROGRESS["running"] = False
        IDX_PROGRESS["finishedAt"] = int(time.time())
        if error:
            IDX_PROGRESS["phase"] = "error"
            IDX_PROGRESS["stage"] = "索引失败"
            IDX_PROGRESS["lastError"] = error
        else:
            IDX_PROGRESS["phase"] = "done"
            IDX_PROGRESS["stage"] = "索引完成"
        if summary:
            IDX_PROGRESS["summary"] = summary


def idx_summary():
    """完成后的总览：照片数 / 视频数 / Live Photo 组数（便宜查询）。"""
    photos = videos = live = total = 0
    for lid in list(META["lib_order"]):
        lib = META["libs"].get(lid)
        if not lib:
            continue
        v = 0
        if INDEX:
            try:
                v = INDEX.count_unclaimed_movies(lid)
            except Exception:
                v = 0
        total += int(lib.get("count") or 0)
        live += int(lib.get("live") or 0)
        videos += v
    photos = max(0, total - videos)
    return {"photos": photos, "videos": videos, "live": live, "total": total}

_inflight = {}
_inflight_guard = threading.Lock()
_stats = {"hit": 0, "miss": 0, "gen": 0, "singleflight_wait": 0, "transcode": 0}
_stats_lock = threading.Lock()

# 图片生成并发闸门：避免几十个请求同时解码打满 CPU
_GEN_WORKERS = max(2, min(6, (os.cpu_count() or 4)))
_gen_sem = threading.BoundedSemaphore(_GEN_WORKERS)


def bump(k, n=1):
    with _stats_lock:
        _stats[k] = _stats.get(k, 0) + n


# =========================================================
#  缓存清单：记录「哪个缓存文件是谁生成的」
#  —— 有了它，缓存才可被安全回收（见 cache_gc_plan / cache_gc_apply）
# =========================================================
_recorded_memo = set()
_recorded_memo_lock = threading.Lock()


def cache_record(key, lib_id, rel, st, kind, variant, bytes_):
    """把一个缓存文件的来源写进清单。带内存去重，避免每次命中都写库。"""
    if CACHE_INDEX is None or not key:
        return
    with _recorded_memo_lock:
        if key in _recorded_memo:
            return
    try:
        CACHE_INDEX.record(key, lib_id, rel,
                           getattr(st, "st_mtime", 0) or 0,
                           getattr(st, "st_size", 0) or 0,
                           kind, variant, bytes_)
    except Exception as e:
        log.debug("cache manifest record failed: %s", e)
        return
    with _recorded_memo_lock:
        if len(_recorded_memo) > 400000:
            _recorded_memo.clear()
        _recorded_memo.add(key)


def cache_file_for(key, kind):
    """由缓存 key + 类型反推缓存文件路径（与生成时用的是同一个布局）。
    ★ 纯路径拼接，不创建目录 —— 预览阶段绝不能有文件系统副作用。"""
    if kind == "trans":
        return os.path.join(CACHE_DIR, "trans", key[:2], key + ".mp4")
    return os.path.join(CACHE_DIR, key[:2], key[2:4], key + ".cache")


def cache_name_for(key, kind):
    """只取文件名（用于和磁盘上已有文件比对，零系统调用）。"""
    return key + (".mp4" if kind == "trans" else ".cache")


# =========================================================
#  ffmpeg 探测
# =========================================================
FFMPEG_CANDIDATES = [
    os.environ.get("FFMPEG_BIN", ""),
    os.path.join(APP_DIR, "ffmpeg.exe"),
    r"E:\jellyfin-ffmpeg_8.1.2-2\ffmpeg.exe",
    r"G:\Program Files\ffmpeg_gui\ffmpeg.exe",
    r"C:\Users\Administrator\Videos\ffmpeg.exe",
    r"C:\Program Files (x86)\BiliDownloader\ffmpeg.exe",
    "ffmpeg", "ffmpeg.exe",
]
FFMPEG = None


def detect_ffmpeg():
    import shutil as _sh
    for c in FFMPEG_CANDIDATES:
        if not c:
            continue
        if os.path.isfile(c):
            return c
        if os.sep not in c:
            exe = _sh.which(c)
            if exe:
                return exe
    return None


# =========================================================
#  进程管理（防重复启动 / --stop）
# =========================================================
def _pid_alive(pid):
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259
    except Exception:
        return False


def check_already_running(port):
    if os.path.isfile(PID_FILE):
        try:
            with open(PID_FILE, encoding="utf-8") as f:
                info = json.load(f)
            old_pid = int(info.get("pid", 0))
            old_port = int(info.get("port", 0))
            if old_pid and _pid_alive(old_pid):
                return old_pid, old_port
        except Exception:
            pass
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
    if port:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return -1, port
    return None, None


def write_pid(port):
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "port": port,
                       "start": time.strftime("%Y-%m-%d %H:%M:%S")}, f,
                      ensure_ascii=False)
        with open(PORT_FILE, "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        pass


def clear_pid():
    for p in (PID_FILE, PORT_FILE):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def stop_server():
    print("=" * 62)
    print("  停止 SeekLive 时光轴服务")
    print("=" * 62)
    pid = None
    if os.path.isfile(PID_FILE):
        try:
            with open(PID_FILE, encoding="utf-8") as f:
                pid = int(json.load(f).get("pid", 0))
        except Exception:
            pass
    if not pid or not _pid_alive(pid):
        print("  未发现运行中的服务实例。")
        clear_pid()
        return 0
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x0001, False, pid)
        if not h:
            print(f"  [失败] 无法打开进程 {pid}")
            return 1
        ok = k.TerminateProcess(h, 0)
        k.CloseHandle(h)
        print(f"  [完成] 已停止进程 PID={pid}" if ok else f"  [失败] PID={pid}")
        return 0 if ok else 1
    except Exception as e:
        print(f"  [异常] {e}")
        return 1
    finally:
        clear_pid()


# =========================================================
#  路径安全（多根目录隔离）
# =========================================================
class SecurityError(Exception):
    pass


def safe_resolve(root, rel):
    """把相对路径解析为绝对路径，并确保仍在 root 内。"""
    if not rel:
        raise SecurityError("empty path")
    rel = rel.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", rel):
        raise SecurityError("absolute path rejected")
    rel = rel.lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise SecurityError("parent traversal rejected")
    if any("~" in p for p in parts):
        raise SecurityError("short-name rejected")
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, *parts))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise SecurityError("escaped root")
    if not os.path.isfile(target):
        raise FileNotFoundError(rel)
    return target


# =========================================================
#  索引扫描
# =========================================================
TS_RE = re.compile(r"(?:^|[_\-\s])(\d{13}|\d{10})(?=[_\-\s.]|$)")
TS_MIN, TS_MAX = 1420070400, 2082758400        # 2015-01-01 ~ 2035-12-31


def parse_ts(name):
    """
    从文件名里提取时间戳（如 'xxx_1699999999999.jpg'）。
    要求：数字串必须被 _ - 空格 或字符串边界【完整包住】，
    且落在 2015–2035 区间内 —— 避免把 UUID / 哈希里的零散数字误当时间。
    取不到时返回 None，由调用方回退到文件 mtime。
    """
    for m in TS_RE.finditer(name):
        run = m.group(1)
        v = int(run)
        if len(run) == 13:
            v //= 1000
        if TS_MIN <= v <= TS_MAX:
            return v
    return None
UUID_RE = re.compile(rb"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                     rb"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
CID_KEY = b"com.apple.quicktime.content.identifier"
LIVE_MARKERS = (b"com.apple.quicktime.live-photo",
                b"com.apple.quicktime.still-image-time")


def clean_ext(name):
    """扩展名提取：容错 'xxx.jpeg; charset=UTF-8' 这类脏文件名"""
    e = os.path.splitext(name)[1].lower()
    for sep in (";", " ", "\t"):
        e = e.split(sep)[0]
    return e


def clean_stem(name):
    """主名（用于图片/视频配对）：去掉 '; charset=UTF-8' 这类脏后缀"""
    return re.split(r"[;\s]", os.path.splitext(name)[0])[0]


def sniff_ext(path):
    """没有/未知扩展名时，用文件头魔数嗅探类型"""
    try:
        with open(path, "rb") as f:
            b = f.read(16)
    except OSError:
        return None
    if b[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return ".webp"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBM":
        return ".webm"
    if b[:4] == b"\x1aE\xdf\xa3":            # EBML → Matroska / WebM
        return ".mkv"
    if b[4:8] == b"ftyp":
        brand = b[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis"):
            return ".heic"
        if brand == b"qt  ":
            return ".mov"
        return ".mp4"
    return None


# 配对待选图片的扩展名优先级（HEIC/JPEG 优先当 Live Photo 静图）
EXT_PRIORITY = {".heic": 0, ".heif": 0, ".avif": 1, ".jpeg": 2, ".jpg": 2,
                ".png": 3, ".webp": 3, ".gif": 4, ".bmp": 4, ".tif": 5, ".tiff": 5}


def read_head(path, n=4 * 1024 * 1024):
    with open(path, "rb") as f:
        return f.read(n)


def mov_content_id(path):
    try:
        data = read_head(path)
    except OSError:
        return None
    i = data.find(CID_KEY)
    if i < 0:
        return None
    m = UUID_RE.search(data[i:i + 4096])
    return m.group(0).decode().upper() if m else None


def image_live_ids(path):
    try:
        data = read_head(path)
    except OSError:
        return set()
    return {m.group(0).decode().upper() for m in UUID_RE.finditer(data)}


def mov_has_live_markers(path):
    try:
        data = read_head(path, 2 * 1024 * 1024)
    except OSError:
        return False
    return any(k in data for k in LIVE_MARKERS)


def _is_link_like(path):
    """符号链接 或 Windows junction —— 两者都必须跳过，否则可能成环无限递归。"""
    try:
        if os.path.islink(path):
            return True
    except OSError:
        pass
    isj = getattr(os.path, "isjunction", None)
    if isj:
        try:
            if isj(path):
                return True
        except OSError:
            pass
    return False


def walk_files(root, ignore=None, ignore_hidden=True, on_progress=None):
    """
    递归列举 (relpath, name, size, mtime)，只 stat 不读内容。

    遍历规则
    --------
      · 递归进入**所有普通子目录**（不限层级，用户无需逐个添加年份/月份目录）
      · 跳过隐藏目录（以 . 开头）—— 可用 ignoreHidden:false 关闭
      · 跳过系统/工具目录（.git / node_modules / @eaDir / $RECYCLE.BIN …）
      · 跳过符号链接与 junction（含指向祖先目录的链接，避免无限递归）
      · 无权限目录 / 单个条目 stat 失败 → 静默跳过该条目，不中断整次扫描
      · 根目录本身不可读 → 返回空列表（由上层决定是「空图库」还是「保留旧索引」）

    on_progress(n)：低频回调（仅用于索引进度面板的「已发现 N 个文件」，
    纯计数、不参与百分比计算）。默认 None → 行为与改造前完全一致。
    """
    out = []
    ignore = set(ignore or ())
    root = os.path.abspath(root)
    root_real = os.path.realpath(root)
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if _is_link_like(e.path):
                                continue
                            name = e.name
                            if ignore_hidden and name.startswith("."):
                                continue
                            if name in ignore:
                                continue
                            # 防御：不要把本项目自己的数据目录当成照片库
                            try:
                                if os.path.realpath(e.path) == root_real:
                                    continue
                            except OSError:
                                pass
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat()
                            rel = os.path.relpath(e.path, root).replace("\\", "/")
                            out.append((rel, e.name, st.st_size, st.st_mtime))
                            if on_progress is not None:
                                on_progress(len(out))
                    except OSError:
                        continue
        except OSError:
            # 无权限 / 目录消失 / 设备暂时不可用：跳过该目录，继续其余部分
            continue
    return out


def classify(rel, name, root):
    """判定文件类型 → (ext, kind)；kind ∈ {'img','mov',None}"""
    ext = clean_ext(name)
    if ext not in IMG_EXT and ext not in MOV_EXT:
        ext = sniff_ext(os.path.join(root, rel.replace("/", os.sep))) or ""
    if ext in MOV_EXT:
        return ext, "mov"
    if ext in IMG_EXT:
        return ext, "img"
    return None, None


def _mov_codec_boxes(path):
    """
    不打 ffmpeg、按 MP4/MOV box 结构直接读 moov → stsd 的 fourcc。
    只读 box 头与 moov（通常 <1MB），不读 mdat，因此大文件也很快。
    """
    try:
        fsize = os.path.getsize(path)
    except OSError:
        return None
    VIDEO_FOURCC = {
        b"avc1": "h264", b"avc3": "h264", b"hvc1": "hevc", b"hev1": "hevc",
        b"av01": "av1", b"vp09": "vp9", b"mp4v": "mpeg4", b"ap4h": "prores",
        b"jpeg": "mjpeg", b"dvh1": "dolby", b"dvhe": "dolby",
    }
    try:
        with open(path, "rb") as f:
            # 逐层定位 moov（可能出现在文件头，也可能像 iPhone MOV 那样在尾部）
            def box_at(off, end):
                if off + 8 > end:
                    return None
                f.seek(off)
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None
                size = int.from_bytes(hdr[:4], "big")
                typ = hdr[4:8]
                if size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        return None
                    size = int.from_bytes(ext, "big")
                elif size == 0:
                    size = end - off
                if size < 8:
                    return None
                return typ, size, off

            def find(off, end, want):
                p = off
                while p < end:
                    b = box_at(p, end)
                    if not b:
                        return None
                    typ, size, bpos = b
                    if typ == want:
                        return bpos, size
                    nxt = bpos + size
                    if nxt <= p:
                        return None
                    p = nxt
                return None

            mv = find(0, fsize, b"moov")
            if not mv:
                return None
            mpos, msize = mv
            f.seek(mpos)
            moov = f.read(min(msize, 32 * 1024 * 1024))
    except OSError:
        return None

    i = moov.find(b"stsd")
    while i >= 0:
        fourcc = moov[i + 16:i + 20]
        if fourcc in VIDEO_FOURCC:
            return VIDEO_FOURCC[fourcc]
        i = moov.find(b"stsd", i + 4)
    return None


def _mkv_codec(path, probe_bytes=2 * 1024 * 1024):
    """
    Matroska / WebM 是 EBML，不是 ISO-BMFF，box 解析用不上。
    但 CodecID 字符串就写在文件前部的 Tracks 元素里，直接嗅探即可（无需 ffmpeg）。
    """
    MKV_CODEC = {
        b"V_MPEG4/ISO/AVC": "h264", b"V_MPEGH/ISO/HEVC": "hevc",
        b"V_MPEG4/ISO/ASP": "mpeg4", b"V_MPEG4/ISO/SP": "mpeg4",
        b"V_MPEG2": "mpeg2", b"V_VP9": "vp9", b"V_VP8": "vp8",
        b"V_AV1": "av1", b"V_THEORA": "theora",
    }
    try:
        with open(path, "rb") as f:
            data = f.read(probe_bytes)
    except OSError:
        return None
    for key, codec in MKV_CODEC.items():
        if key in data:
            return codec
    return None


def probe_video_codec(path):
    """三级探测：ISO-BMFF box → Matroska CodecID → ffmpeg（最后兜底）。"""
    ext = clean_ext(os.path.basename(path))
    if ext in (".mkv", ".webm"):
        c = _mkv_codec(path)
        if c:
            return c
    else:
        c = _mov_codec_boxes(path)
        if c:
            return c
    if FFMPEG:
        return probe_codec(path)
    return None


# =========================================================
#  索引扫描（SQLite 持久化 + 增量）
# =========================================================
def scan_library(lib, full=False, lib_index=0, lib_total=0, report=True):
    """
    对一个图库做全量(full=True)或增量扫描，并刷新其内存视图。

    增量语义
    --------
      · 比对 (size, mtime)：只把新增 / 变化 的文件写库
      · 删除：库里有、盘上没有的条目 → 删除
      · Live Photo：只有「受影响分组」内的 MOV 才可能被重新解析；
        MOV 的 CID 与静图的 UUID 都带 checked 标志缓存，未变化绝不重读
      · 全库重建时 full=True → 清空该库后重新来过

    report=True 时把两阶段进度写给索引进度面板（阶段一不产生百分比）。
    """
    idx = INDEX
    lid = lib["id"]
    root = lib["root"]
    t0 = time.perf_counter()

    ignore = set(IGNORE_DIRS) | set(lib.get("ignore") or [])
    ignore_hidden = lib.get("_ignoreHidden", META["scan"].get("ignoreHidden", True))

    if full:
        idx.reset_lib(lid)

    if report:
        idx_counting(lib, lib_index, lib_total)
    listed = walk_files(root, ignore=ignore, ignore_hidden=ignore_hidden,
                        on_progress=(idx_counting_tick if report else None))
    if report:
        idx_scanning(lib, len(listed))
    idx.touch_lib(lid, lib["name"], root)

    existing = idx.all_files(lid)
    to_upsert, seen = [], set()
    added = changed = 0

    for i, (rel, name, size, mtime) in enumerate(listed, 1):
        ext, kind = classify(rel, name, root)
        if not kind:
            if report and i % IDX_TICK == 0:
                idx_set(processed=i)
            continue
        seen.add(rel)
        old = existing.get(rel)
        if old is None:
            added += 1
        elif old["size"] == size and abs(old["mtime"] - mtime) < 1e-6:
            if report and i % IDX_TICK == 0:
                idx_set(processed=i)
            continue                       # 未变化 → 完全跳过（不重读、不重解析）
        else:
            changed += 1
        to_upsert.append({
            "lib_id": lid, "rel": rel, "name": name,
            "dir": os.path.dirname(rel), "stem": clean_stem(name),
            "ext": ext, "kind": kind, "size": size, "mtime": mtime,
            "ts": parse_ts(name) or int(mtime),
            # 文件新增/变化 → 旧的派生缓存一律作废
            "mov_checked": 0, "cid": None, "live_markers": 0, "codec": None,
            "img_checked": 0, "live_ids": None,
        })
        if report and i % IDX_TICK == 0:
            idx_set(processed=i)

    removed = [rel for rel in existing if rel not in seen]

    # 受影响分组 = 变化/删除文件所在的 (目录, 主名)
    groups = set()
    for r in to_upsert:
        groups.add((r["dir"], r["stem"]))
    for rel in removed:
        o = existing[rel]
        groups.add((o["dir"], o["stem"]))

    mov_parsed = 0

    def do_write():
        nonlocal mov_parsed
        idx.delete_files(lid, removed)
        idx.drop_pairs_for_movies(
            lid, [r for r in removed if existing[r]["kind"] == "mov"])
        idx.upsert_files(to_upsert)
        mov_parsed = _repair_groups(lid, root, groups)

    with INDEX_LOCK:
        do_write()

        # 独立视频（未被任何静图认领的 MOV/MP4）：懒探测码流，供前端能力路由
        need = idx.movies_missing_codec(lid, unclaimed_only=True)
        for rel in need[:500]:                     # 单次上限，避免首次启动卡太久
            fp = os.path.join(root, rel.replace("/", os.sep))
            try:
                codec = probe_video_codec(fp) or ""
            except Exception:
                codec = ""
            idx.set_codec(lid, rel, codec)

        sync_memory(lib, idx)
        idx.save_lib_stats(
            lid, count=lib["count"], live=lib["live"], scanned=len(seen),
            added=added, changed=changed, removed=len(removed),
            mov_parsed=mov_parsed,
            last_scan_ms=int((time.perf_counter() - t0) * 1000),
            last_scan_at=int(time.time()),
            last_full_at=int(time.time()) if full else (idx.lib_meta(lid) or {}).get(
                "last_full_at", 0),
            root_exists=1, stale=0)

    lib["scan_ms"] = int((time.perf_counter() - t0) * 1000)
    lib["movParsed"] = mov_parsed
    lib["added"] = added
    lib["changed"] = changed
    lib["removed"] = len(removed)
    lib["stale"] = False
    lib["exists"] = True
    if report:
        idx_lib_done(lib, len(listed), added, changed, len(removed))
    return lib


def _repair_groups(lid, root, groups):
    """
    只对「受影响分组」重算 Live Photo 配对。
    返回本次实际重新解析的 MOV 数量。
    """
    idx = INDEX
    mov_parsed = 0
    for (d, stem) in groups:
        rows = idx.group(lid, d, stem)
        imgs = sorted([r for r in rows if r["kind"] == "img"],
                      key=lambda r: (EXT_PRIORITY.get(r["ext"] or "", 9), r["rel"]))
        movs = sorted([r for r in rows if r["kind"] == "mov"],
                      key=lambda r: ({"mov": 0, "mp4": 1, "m4v": 2}.get(
                          (r["ext"] or "").lstrip("."), 9), r["rel"]))
        img_rels = [r["rel"] for r in imgs]
        if not imgs or not movs:
            if img_rels:
                idx.replace_group_pairs(lid, img_rels, [])
            continue

        mv = movs[0]
        claimer = imgs[0]
        if not mv["mov_checked"]:
            mp = os.path.join(root, mv["rel"].replace("/", os.sep))
            cid = None
            try:
                cid = mov_content_id(mp)
                markers = mov_has_live_markers(mp)
            except OSError:
                markers = False
            mov_parsed += 1
            idx.set_mov_meta(lid, mv["rel"], cid, markers)
            mv = dict(mv); mv["cid"] = cid; mv["live_markers"] = 1 if markers else 0

        ids = set()
        if not claimer["img_checked"]:
            ip = os.path.join(root, claimer["rel"].replace("/", os.sep))
            try:
                ids = image_live_ids(ip)
            except OSError:
                ids = set()
            idx.set_img_meta(lid, claimer["rel"], ids)
        else:
            ids = set(x for x in (claimer.get("live_ids") or "").split(",") if x)

        live, method = 0, "name-unverified"
        if mv.get("cid") and mv["cid"] in ids:
            live, method = 1, "cid"
        elif mv.get("live_markers"):
            live, method = 1, "marker"

        idx.replace_group_pairs(lid, img_rels, [{
            "img_rel": claimer["rel"], "mov_rel": mv["rel"],
            "live": live, "method": method,
        }])
    return mov_parsed


def sync_memory(lib, idx=None):
    """从 SQLite 重建该图库的内存视图（结构与原实现一致）。"""
    idx = idx or INDEX
    items, months, day_order, by_day, n_live = idx.build_index(lib["id"])
    lib["items"] = items
    lib["months"] = months
    lib["day_order"] = day_order
    lib["by_day"] = by_day
    lib["count"] = len(items)
    lib["live"] = n_live
    return lib


def _empty_entry(entry, stale=False):
    entry.update({"count": 0, "live": 0, "items": [], "by_day": {},
                  "day_order": [], "months": [], "scan_ms": 0,
                  "stale": stale})
    return entry


def _open_stores():
    """打开索引 / 缓存清单 / 回收站账本（幂等）。放在监听端口之前同步完成，
    这样后台索引期间到来的请求也能正常读到索引，不会 500。"""
    global INDEX, CACHE_INDEX, TRASH
    if INDEX is None:
        INDEX = index_db.Index(INDEX_DB)
    if CACHE_INDEX is None:
        CACHE_INDEX = cache_db.CacheIndex(CACHE_DB_MANIFEST)
    if TRASH is None:
        TRASH = trash_db.Trash(TRASH_DB)


def load_libraries(full=False, mode="startup"):
    """
    读取（或首次生成）libraries.json，打开索引并逐库核对。

    根目录不可访问时：**保留原有索引**（标记 stale），绝不当作空图库清空。
    全程把两阶段进度写给 /api/index/progress（阶段一没有总数、不产生百分比）。
    """
    global INDEX, CACHE_INDEX, TRASH
    _open_stores()
    idx_reset("full" if full else mode)
    if INDEX.sync_probe_version(CODEC_PROBE_VERSION):
        log.info("码流探测算法已升级，codec 缓存作废，将在本次扫描重新探测")

    if not os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"scan": DEFAULT_SCAN, "libraries": DEFAULT_LIBS}, f,
                      ensure_ascii=False, indent=2)
        log.info("已生成默认 libraries.json")

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    libs = cfg.get("libraries") or DEFAULT_LIBS

    scan_cfg = dict(DEFAULT_SCAN)
    scan_cfg.update(cfg.get("scan") or {})
    META["scan"] = scan_cfg
    trash_cfg = dict(DEFAULT_TRASH)
    trash_cfg.update(cfg.get("trash") or {})
    META["trash"] = trash_cfg
    if os.environ.get("SEEKLIVE_RECHECK"):
        try:
            scan_cfg["recheckSeconds"] = int(os.environ["SEEKLIVE_RECHECK"])
        except ValueError:
            pass

    libs_order, seen_ids = [], set()
    META["libs"] = {}
    META["lib_order"] = []
    libs_total = len(libs)

    for n, lib in enumerate(libs, 1):
        root = os.path.abspath(os.path.expanduser(lib.get("root", "")))
        lid = lib.get("id") or hashlib.md5(root.encode()).hexdigest()[:8]
        if lid in seen_ids:                       # 多图库身份必须唯一
            lid = lid + "_" + hashlib.md5(root.encode()).hexdigest()[:4]
        seen_ids.add(lid)

        entry = {"id": lid, "name": lib.get("name") or lid, "root": root,
                 "cfg": lib, "stale": False,
                 "ignore": lib.get("ignore") or [],
                 "_ignoreHidden": lib.get("ignoreHidden",
                                          scan_cfg.get("ignoreHidden", True)),
                 # ── 源文件管理权限（缺省 = 禁止；旧配置升级后自动保持禁止）──
                 "readonly": bool(lib.get("readonly")),
                 "deletion_enabled": bool(lib.get("deletion_enabled")),
                 "exists": os.path.isdir(root)}
        libs_order.append(lid)

        if entry["exists"]:
            print(f"[扫描] {entry['name']}  ({root})")
            try:
                scan_library(entry, full=full, lib_index=n, lib_total=libs_total)
            except Exception as e:
                log.exception("扫描 %s 失败", lid)
                idx_error(entry, f"{entry['name']} 扫描失败：{e}")
                _empty_entry(entry)
                print(f"       [错误] 扫描失败：{e}")
            else:
                flag = "" if not entry.get("movParsed") else f" ｜ 重解析 MOV {entry['movParsed']} 个"
                print(f"       {entry['count']:,} 项 / Live {entry['live']} 组 "
                      f"/ {entry['scan_ms']} ms{flag}")
        else:
            # ★ 关键：目录离线 → 保留既有索引，不清空
            meta = INDEX.lib_meta(lid)
            if meta and meta.get("count"):
                sync_memory(entry)
                entry["stale"] = True
                idx_offline(entry)
                print(f"[离线] {entry['name']} 目录不可访问 → 保留索引 "
                      f"{entry['count']:,} 项（不删除）")
            else:
                _empty_entry(entry)
                idx_offline(entry)
                print(f"[跳过] {entry['name']} 目录不存在: {root}")

        META["libs"][lid] = entry
        META["lib_order"].append(lid)

    # 回收站目录必须与所有图库根目录互不包含（双向校验）；不满足则禁用全部管理操作
    ok, why = _trash_safe()
    if not ok:
        log.error("回收站安全检查未通过：%s → 源文件管理功能已禁用", why)

    # 崩溃恢复：把「未收尾批次」按文件实际所在位置对账回来（不静默丢文件/记录）
    try:
        rec = recover_trash()
        if rec.get("batches"):
            log.warning("回收站崩溃恢复：收尾 %s 个未完成批次", rec["batches"])
    except Exception as e:
        log.error("回收站崩溃恢复失败：%s", e)

    idx_finish(summary=idx_summary())
    return META["libs"]


def refresh_library(lid, full=False):
    """手动刷新单个图库（/api/refresh）。

    ★ report=False：调用的响应体本身就带回本次增量统计，不需要占用进度面板；
      进度面板只由「启动索引 / 后台核对 / 手动索引」三个入口驱动，
      避免把面板留在半截状态。
    """
    entry = META["libs"].get(lid)
    if not entry:
        return None
    root = entry["root"]
    with INDEX_LOCK:
        if not os.path.isdir(root):
            entry["exists"] = False
            entry["stale"] = True
            return lib_public(entry)
        return lib_public(scan_library(entry, full=full, report=False))


def recheck_all(mode="recheck"):
    """后台 / 手动增量核对：逐库做增量扫描（根目录离线则跳过并保留索引）。

    ★ 刻意**不重建 META["libs"]**：增量期间 /api/libs、/api/items 仍然可用，
      浏览不会被索引打断。
    """
    if idx_get().get("running"):
        return False                          # 上一轮还没结束 → 本轮跳过，不排队
    idx_reset(mode)
    try:
        ids = list(META["lib_order"])
        for n, lid in enumerate(ids, 1):
            entry = META["libs"].get(lid)
            if not entry:
                continue
            if not os.path.isdir(entry["root"]):
                idx_error(entry, "目录不可访问，保留旧索引")
                idx_offline(entry)
                continue
            try:
                with INDEX_LOCK:
                    scan_library(entry, full=False, lib_index=n, lib_total=len(ids))
            except Exception as e:
                log.error("recheck %s failed: %s", lid, e)
                idx_error(entry, f"{entry['name']} 核对失败：{e}")
        idx_finish(summary=idx_summary())
        return True
    except Exception as e:
        idx_finish(error=str(e))
        return False


def recheck_loop():
    interval = int(META["scan"].get("recheckSeconds") or 0)
    if interval <= 0 or not META["scan"].get("autoRescan", True):
        return
    log.info("后台定期核对已启动：每 %s 秒", interval)
    while not _recheck_stop.wait(interval):
        recheck_all()


def lib_public(lib):
    allowed, reason = manage_state(lib)
    return {"id": lib["id"], "name": lib["name"], "count": lib["count"],
            "live": lib["live"], "exists": lib["exists"], "scanMs": lib["scan_ms"],
            "stale": bool(lib.get("stale")),
            "movParsed": lib.get("movParsed", 0),
            "added": lib.get("added", 0), "changed": lib.get("changed", 0),
            "removed": lib.get("removed", 0),
            # ── 源文件管理权限（前端据此置灰；判定始终以服务端为准）──
            "readonly": bool((lib.get("cfg") or {}).get("readonly")),
            "deletionEnabled": bool((lib.get("cfg") or {}).get("deletion_enabled")),
            "canManage": allowed, "manageReason": reason}


def get_lib(qs):
    lid = (qs.get("lib") or [""])[0]
    if not lid:
        lid = META["lib_order"][0] if META["lib_order"] else ""
    return META["libs"].get(lid)


# =========================================================
#  源文件管理：权限闸门 + 删除（移入回收站）/ 恢复 / 崩溃对账
#  ★ 与「缓存回收」是完全独立的两条路径：
#    这里动的是源照片（且只做"移动"，绝不改写字节），
#    缓存回收只读 cache/。二者共用不到任何删除代码。
# =========================================================
# 回收站里「仍未处理完」的条目状态：都要可见、可恢复、可永久删除。
#   trashed  正常待恢复
#   failed   操作失败（文件仍在原处或仍在回收站）
#   conflict 恢复冲突（目标已存在，未覆盖）
#   missing  两处都找不到（异常，需人工关注，绝不能变成看不见的幽灵记录）
TRASH_LIVE_STATES = ("trashed", "failed", "conflict", "missing")


def _trash_safe():
    """回收站目录必须与所有图库根目录互不包含（双向）。返回 (ok, reason)。"""
    try:
        tr = os.path.realpath(TRASH_DIR)
    except OSError as e:
        return False, f"回收站路径不可用：{e}"
    if not os.path.isdir(tr):
        return False, "回收站目录不存在"
    for lid in (META.get("lib_order") or []):
        lib = (META.get("libs") or {}).get(lid) or {}
        root = lib.get("root")
        if not root:
            continue
        try:
            rr = os.path.realpath(root)
        except OSError:
            continue
        if tr == rr or tr.startswith(rr + os.sep) or rr.startswith(tr + os.sep):
            return False, f"回收站与图库「{lib.get('name') or lid}」根目录重叠"
    return True, ""


# =========================================================
#  管理认证：口令来源 / 会话 / 失败退避
# =========================================================
def load_admin_cfg():
    """读取独立配置文件 admin.json（与 libraries.json 完全分离）。"""
    if not os.path.isfile(ADMIN_CFG_PATH):
        return {}
    try:
        with open(ADMIN_CFG_PATH, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception as e:
        log.error("admin.json 解析失败（已忽略）：%s", e)
        return {}


def set_admin_config(cli_token=None):
    """
    确定管理口令（优先级：命令行 --admin-token / --token > admin.json）。

    ★ 必须把结果写回模块级 ADMIN_PW。上一轮出现过的 P0 就是「参数被打印出来、
      但没写进闸门读的那个变量」，导致权限判定永远不生效 —— 这里刻意只留一个
      真源（ADMIN_PW），并由测试断言「启动参数确实进入了认证状态」。
    口令本体不回显、不写日志。
    """
    global ADMIN_PW, ADMIN_SOURCE
    cfg = load_admin_cfg()
    try:
        hours = float(cfg.get("sessionHours", 12) or 0)
    except (TypeError, ValueError):
        hours = 12.0
    try:
        maxage = int(cfg.get("sessionCookieMaxAge", 0) or 0)
    except (TypeError, ValueError):
        maxage = 0
    ADMIN_CFG["sessionHours"] = hours
    ADMIN_CFG["sessionCookieMaxAge"] = max(0, maxage)

    tok = str(cli_token or "").strip()
    src = "命令行"
    if not tok:
        tok = str(cfg.get("adminToken") or "").strip()
        src = "admin.json"
    ADMIN_PW = tok or None
    ADMIN_SOURCE = src if ADMIN_PW else ""
    return ADMIN_PW


def admin_required():
    """是否配置了管理口令（未配置 = 管理功能整体关闭，浏览不受影响）。"""
    return bool(ADMIN_PW)


def admin_login_ok(token):
    """常量时间比对，避免时序侧信道。绝不记录输入值。"""
    if not ADMIN_PW:
        return False
    try:
        return hmac.compare_digest(str(token or ""), ADMIN_PW)
    except Exception:
        return False


def admin_session_create():
    """登录成功 → 签发新会话。每次登录都换新 sid，杜绝会话固定。"""
    sid = secrets.token_urlsafe(32)
    hours = float(ADMIN_CFG.get("sessionHours") or 0)
    exp = int(time.time() + hours * 3600) if hours > 0 else 0
    with _admin_lock:
        _gcs_admin_sessions()
        _admin_sessions[sid] = exp
    return sid, exp


def _gcs_admin_sessions(now=None):
    now = now or time.time()
    for sid in [s for s, exp in _admin_sessions.items() if exp and exp <= now]:
        _admin_sessions.pop(sid, None)


def admin_session_valid(sid):
    if not sid:
        return False
    with _admin_lock:
        exp = _admin_sessions.get(sid)
        if exp is None:
            return False
        if exp and exp <= time.time():
            _admin_sessions.pop(sid, None)          # 过期即作废
            return False
        return True


def admin_session_drop(sid):
    if not sid:
        return
    with _admin_lock:
        _admin_sessions.pop(sid, None)


def admin_session_expires(sid):
    with _admin_lock:
        return _admin_sessions.get(sid)


def admin_rate_check(ip):
    """登录前检查：返回 (是否放行, 还需等待秒数)。连续失败后指数退避，封顶 30 秒。"""
    now = time.time()
    with _admin_fail_lock:
        rec = _admin_fails.get(ip)
        if not rec:
            return True, 0
        if now - rec["at"] > 600:                   # 10 分钟无失败 → 计数清零
            _admin_fails.pop(ip, None)
            return True, 0
        if rec["until"] > now:
            return False, int(rec["until"] - now) + 1
        return True, 0


def admin_rate_fail(ip):
    """记一次失败；返回 (连续失败数, 本次退避秒数)。"""
    now = time.time()
    with _admin_fail_lock:
        rec = _admin_fails.get(ip) or {"n": 0, "until": 0.0, "at": 0.0}
        if now - rec["at"] > 600:
            rec["n"] = 0
        rec["n"] += 1
        rec["at"] = now
        delay = 0
        if rec["n"] >= ADMIN_FAIL_MAX:
            delay = int(min(30, 2 ** min(rec["n"] - ADMIN_FAIL_MAX, 5)))
            rec["until"] = now + delay
        _admin_fails[ip] = rec
        return rec["n"], delay


def admin_rate_reset(ip):
    with _admin_fail_lock:
        _admin_fails.pop(ip, None)


def manage_state(lib, need_online=True):
    """
    源文件管理是否可用（只读判定，无副作用）。返回 (allowed, reason)。

    ★ 这里只回答「这个图库允不允许被管理」——**与是否已授权无关**。
      「有没有配管理口令」「当前请求有没有有效会话」由 mgmt_guard() 前置判定，
      两者分开后，前端才能区分「能力不具备（置灰）」与「需要授权（弹框）」。

    need_online=False：仅用于「永久删除回收站文件」——那只动 data/trash/ 里的文件，
    图库目录是否在线无关，所以离线也应放行。
    """
    if not lib:
        return False, "图库不存在"
    cfg = lib.get("cfg") or {}
    if cfg.get("readonly"):
        return False, "只读图库，禁止管理源文件"
    if not cfg.get("deletion_enabled"):
        return False, "该图库未开启源文件管理（deletion_enabled=false）"
    if need_online and (not lib.get("exists", True) or lib.get("stale")):
        return False, "图库当前离线，暂不可操作"
    ok, why = _trash_safe()
    if not ok:
        return False, why
    return True, ""


def manage_info(lib):
    """manage_state 的 JSON 友好形态。"""
    allowed, reason = manage_state(lib)
    return {"allowed": allowed, "reason": reason}


def _trash_root_real():
    return os.path.realpath(TRASH_DIR) + os.sep


def _abs_trash(trash_rel):
    """回收站内相对路径 → 绝对路径（纯拼接，不判断存在性）。"""
    parts = [p for p in (trash_rel or "").replace("\\", "/").split("/") if p]
    return os.path.join(TRASH_DIR, *parts)


def _planned_trash_rel(lib_id, batch_id, rel):
    return f"{lib_id}/{batch_id}/{rel}"


def _unique_dst(dst):
    """找一个尚不存在的目标路径 —— **绝不覆盖既有文件**。
    ★ 后缀用 `_N` 而不是 `~N`：`safe_resolve()` 会拒绝含有 `~` 的路径段
      （防 Windows 8.3 短名），用 `~` 会让生成的文件事后无法被解析。"""
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    for i in range(1, 10000):
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(cand):
            return cand
    raise FileExistsError(f"无法为 {dst} 找到不冲突的名字")


def _sha256(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _move_file(src, dst):
    """
    把 src 移到 dst（**绝不覆盖已存在的目标**）。返回 (mode, actual_dst)。
    同盘 → os.replace（原子）；跨盘 → copy2 + 校验 + 再删源（校验不过就不删源）。
    失败抛异常，由调用方记录到条目里（绝不静默丢文件）。
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    dst = _unique_dst(dst)
    try:
        os.replace(src, dst)
        return "replace", dst
    except OSError:
        pass
    tmp = dst + ".part"
    try:
        shutil.copy2(src, tmp)
        if os.path.getsize(src) != os.path.getsize(tmp) or \
           _sha256(src) != _sha256(tmp):
            raise IOError("复制校验不一致，已保留源文件")
        os.replace(tmp, dst)
        os.remove(src)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return "copy", dst


def _verify_member(lib, rel, f):
    """操作前复核：文件在盘上、(size,mtime) 与索引一致、非链接。返回 (ok, why, (st,fp))。"""
    try:
        fp = safe_resolve(lib["root"], rel)
    except SecurityError as e:
        return False, f"路径被拒绝：{e}", None
    except FileNotFoundError:
        return False, "文件已不存在", None
    except OSError as e:
        return False, f"无法访问：{e}", None
    if _is_link_like(fp):
        return False, "拒绝符号链接 / junction", None
    try:
        st = os.stat(fp)
    except OSError as e:
        return False, f"stat 失败：{e}", None
    if int(st.st_size) != int(f["size"]) or abs(st.st_mtime - f["mtime"]) >= 1e-6:
        return False, "文件已变化（大小/修改时间不符），请刷新后重试", None
    return True, "", (st, fp)


def _orig_looks_same(lib, item):
    """
    原路径上的文件是否就是账本记录的那个文件（按 size + mtime 判定）。
    ★ 用于崩溃恢复时区分「原文件还在原位」与「原位被同名替代文件占用」——
      绝不因为"路径上有个同名文件"就认定它是原文件。
    """
    if not lib or not lib.get("root"):
        return False
    try:
        p = safe_resolve(lib["root"], item["orig_rel"])
    except Exception:
        return False
    if _is_link_like(p) or not os.path.isfile(p):
        return False
    try:
        st = os.stat(p)
    except OSError:
        return False
    if int(item["size"]) and int(st.st_size) != int(item["size"]):
        return False
    if float(item["mtime"]) and abs(st.st_mtime - float(item["mtime"])) > 1.0:
        return False
    return True


def _expand_groups(lib, rels):
    """
    把选中的条目展开为「逻辑项目组」。
    分组来源 = 索引 pairs 表（图片↔配对 MOV），**绝不用同名猜测**。
    返回 (groups, errors)。
    """
    lid = lib["id"]
    files = INDEX.all_files(lid)
    pairs = INDEX.pairs(lid)
    groups, errors, seen = [], [], set()
    for rel in rels:
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        f = files.get(rel)
        if not f:
            errors.append({"p": rel, "why": "索引中不存在该文件"})
            continue
        if f["kind"] == "mov":
            groups.append({"key": rel, "live": 0,
                           "members": [{"rel": rel, "kind": "mov", "role": "solo"}]})
            continue
        pr = pairs.get(rel) or {}
        members = [{"rel": rel, "kind": "img", "role": "photo"}]
        if pr.get("mov_rel") and pr["mov_rel"] in files:
            members.append({"rel": pr["mov_rel"], "kind": "mov", "role": "video"})
        groups.append({"key": rel, "live": 1 if pr.get("live") else 0,
                       "members": members})
    return groups, errors


def _group_key(lid, rel):
    return f"{lid}\0{os.path.dirname(rel)}\0{clean_stem(os.path.basename(rel))}"

def mgmt_delete(lib, rels, dry=False):
    """
    删除 = 移入回收站。dry=True 只预览（不建批次、不动任何文件）。
    返回统一结构；执行阶段先落批次/条目记录，再移动文件，最后收尾。
    """
    if TRASH is None:
        return {"ok": False, "error": "回收站账本未初始化"}
    lid = lib["id"]
    groups, errors = _expand_groups(lib, rels)
    files_map = INDEX.all_files(lid)

    plan, seen = [], set()
    for g in groups:
        gmem, gbad = [], []
        for m in g["members"]:
            if m["rel"] in seen:
                continue
            f = files_map.get(m["rel"])
            if not f:
                gbad.append((m["rel"], "索引中不存在该文件"))
                continue
            ok, why, got = _verify_member(lib, m["rel"], f)
            if not ok:
                gbad.append((m["rel"], why))
                continue
            gmem.append({"rel": m["rel"], "kind": m["kind"], "role": m["role"],
                         "size": int(f["size"]), "mtime": float(f["mtime"]),
                         "name": f.get("name") or os.path.basename(m["rel"])})
        if gbad:
            # ★ 组内任一成员不可用 → 整组跳过，绝不删出「残缺配对」
            multi = len(g["members"]) > 1
            for rel, why in gbad:
                errors.append({"p": rel, "why": why + ("（同组，整组跳过）" if multi else "")})
            continue
        if gmem:
            for mm in gmem:
                seen.add(mm["rel"])
            plan.append({"key": g["key"], "live": g["live"], "members": gmem,
                         "size": sum(mm["size"] for mm in gmem)})

    n_files = sum(len(g["members"]) for g in plan)
    n_videos = sum(1 for g in plan for m in g["members"] if m["kind"] == "mov")
    summary = {
        "ok": True, "dry": bool(dry), "lib": lib_public(lib),
        "groups": len(plan), "files": n_files, "videos": n_videos,
        "bytes": sum(g["size"] for g in plan),
        "items": [{"p": m["rel"], "n": m["name"], "k": m["kind"],
                   "role": m["role"], "z": m["size"]}
                  for g in plan for m in g["members"]],
        "errors": errors, "batchId": None, "moved": 0, "failed": 0,
    }
    if dry or not plan:
        return summary

    days = int((META.get("trash") or DEFAULT_TRASH).get("retentionDays") or 30)
    now = int(time.time())
    batch_id = trash_db.make_id("del")
    moved_rels, moved_movs, failed = [], [], 0

    with MGMT_LOCK:
        with _inflight_guard:
            if batch_id in _inflight_batches:
                return {"ok": False, "error": "同一批次正在执行"}
            _inflight_batches.add(batch_id)
        try:
            TRASH.create_batch(batch_id, lid, "delete", n_files)
            rows, id_of = [], {}
            for g in plan:
                for m in g["members"]:
                    iid = trash_db.make_id("it")
                    id_of[m["rel"]] = iid
                    rows.append({
                        "item_id": iid, "batch_id": batch_id, "lib_id": lid,
                        "orig_rel": m["rel"],
                        "trash_rel": _planned_trash_rel(lid, batch_id, m["rel"]),
                        "name": m["name"], "kind": m["kind"],
                        "is_video": 1 if m["kind"] == "mov" else 0,
                        "size": m["size"], "mtime": m["mtime"],
                        "group_key": _group_key(lid, m["rel"]),
                        "pair_role": m["role"], "live": g["live"],
                        "status": "pending",
                    })
            ids = TRASH.add_items(rows)
            TRASH.link(batch_id, ids)

            for g in plan:
                done_moves, gfail = [], None
                for m in g["members"]:
                    iid = id_of[m["rel"]]
                    TRASH.set_item(iid, status="moving")     # 动之前先标记
                    src = os.path.join(lib["root"], m["rel"].replace("/", os.sep))
                    dst = _abs_trash(_planned_trash_rel(lid, batch_id, m["rel"]))
                    try:
                        _mode, actual = _move_file(src, dst)
                        done_moves.append((m, src, actual))
                    except Exception as e:
                        gfail = (m, src, e)
                        break
                if gfail:
                    # ★ 整组回滚：任一成员失败 → 本组已移走的一并还原，绝不留下残缺配对
                    for (m, src, actual) in done_moves:
                        iid = id_of[m["rel"]]
                        try:
                            _mode2, back = _move_file(actual, src)
                            tra2 = os.path.relpath(back, TRASH_DIR).replace("\\", "/")
                            TRASH.set_item(iid, status="failed", trash_rel=tra2,
                                           error="整组回滚：同组成员移动失败，已还原")
                        except Exception as e2:
                            TRASH.set_item(iid, status="failed",
                                           error=f"整组回滚失败：{type(e2).__name__}: {e2}"
                                                 "（文件未丢失，请人工核对）")
                            log.error("删除回滚失败：%s（%s）", m["rel"], e2)
                        failed += 1
                    TRASH.set_item(id_of[gfail[0]["rel"]], status="failed",
                                   error=f"{type(gfail[2]).__name__}: {gfail[2]}")
                    failed += 1
                    log.error("移入回收站失败（整组回滚）：%s（%s）",
                              gfail[0]["rel"], gfail[2])
                    continue
                for (m, src, actual) in done_moves:
                    iid = id_of[m["rel"]]
                    tra = os.path.relpath(actual, TRASH_DIR).replace("\\", "/")
                    TRASH.set_item(iid, status="trashed", trash_rel=tra,
                                   deleted_at=now, expire_at=now + days * 86400,
                                   error="")
                    moved_rels.append(m["rel"])
                    if m["kind"] == "mov":
                        moved_movs.append(m["rel"])

            ok_n = len(moved_rels)
            status = "done" if failed == 0 else ("failed" if ok_n == 0 else "partial")
            TRASH.finish_batch(batch_id, ok_n, failed, status)
            if moved_rels:
                with INDEX_LOCK:
                    INDEX.delete_files(lid, moved_rels)
                    if moved_movs:
                        INDEX.drop_pairs_for_movies(lid, moved_movs)
                    sync_memory(lib)
                _scan_cache_invalidate()
            TRASH.audit("delete", lid, batch_id, ok_n, status,
                        f"groups={len(plan)} files={n_files} failed={failed}")
        finally:
            with _inflight_guard:
                _inflight_batches.discard(batch_id)

    summary.update({"batchId": batch_id, "moved": ok_n, "failed": failed})
    return summary


def _index_add_restored(lid, root, details):
    """把恢复成功的文件重新写回索引（按实际落点 rel）。"""
    rows = []
    for d in details:
        if d.get("result") != "restored":
            continue
        rel = d.get("newRel") or d.get("p")
        try:
            fp = safe_resolve(root, rel)
            st = os.stat(fp)
        except Exception:
            continue
        name = os.path.basename(rel)
        ext, kind = classify(rel, name, root)
        if not kind:
            continue
        rows.append({
            "lib_id": lid, "rel": rel, "name": name,
            "dir": os.path.dirname(rel), "stem": clean_stem(name),
            "ext": ext, "kind": kind, "size": st.st_size, "mtime": st.st_mtime,
            "ts": parse_ts(name) or int(st.st_mtime),
            "mov_checked": 0, "cid": None, "live_markers": 0, "codec": None,
            "img_checked": 0, "live_ids": None,
        })
    if rows:
        INDEX.upsert_files(rows)
    return len(rows)


def mgmt_restore(lib, items=None, batch=None, on_conflict="skip"):
    """
    从回收站恢复。items=item_id 列表；batch=按批次整批恢复。
    on_conflict: 'skip'（默认：不覆盖，整组跳过）| 'rename'（整组改名，保持配对）
    ★ Live 组内有任一冲突时，整组一起跳过或一起改名 —— 绝不恢复出错误的部分配对。
    """
    if TRASH is None:
        return {"ok": False, "error": "回收站账本未初始化"}
    if on_conflict not in ("skip", "rename"):
        on_conflict = "skip"
    lid = lib["id"]

    rows = TRASH.items_of_batch(batch) if batch else TRASH.items_by_ids(items or [])
    rows = [r for r in rows
            if r["lib_id"] == lid and r["status"] in ("trashed", "conflict", "failed")]
    if not rows:
        return {"ok": True, "lib": lib_public(lib), "restored": 0, "failed": 0,
                "skipped": 0, "groups": 0, "items": [], "errors": [],
                "batchId": None}

    by_group = {}
    for r in rows:
        by_group.setdefault(r["group_key"] or r["item_id"], []).append(r)

    batch_id = trash_db.make_id("res")
    now = int(time.time())
    restored = failed = skipped = 0
    details, errors = [], []
    affected = set()

    with MGMT_LOCK:
        with _inflight_guard:
            if batch_id in _inflight_batches:
                return {"ok": False, "error": "同一批次正在执行"}
            _inflight_batches.add(batch_id)
        try:
            TRASH.create_batch(batch_id, lid, "restore", len(rows))
            TRASH.link(batch_id, [r["item_id"] for r in rows])

            for gk, members in by_group.items():
                conflict = False
                for r in members:
                    tgt = os.path.join(lib["root"], r["orig_rel"].replace("/", os.sep))
                    if os.path.exists(tgt):
                        conflict = True
                        break

                if conflict and on_conflict == "skip":
                    skipped += len(members)
                    for r in members:
                        TRASH.set_item(r["item_id"], status="conflict",
                                       error="目标已存在，按策略跳过（不覆盖）")
                        details.append({"p": r["orig_rel"], "result": "conflict"})
                    continue

                suffix = None
                if conflict:                      # rename：整组用同一后缀，保持配对
                    for i in range(1, 10000):
                        ok_all = True
                        for r in members:
                            d = os.path.dirname(r["orig_rel"])
                            stem, ext = os.path.splitext(os.path.basename(r["orig_rel"]))
                            nm = f"{stem}_{i}{ext}"
                            rel = f"{d}/{nm}" if d else nm
                            if os.path.exists(os.path.join(
                                    lib["root"], rel.replace("/", os.sep))):
                                ok_all = False
                                break
                        if ok_all:
                            suffix = i
                            break
                    if suffix is None:
                        for r in members:
                            failed += 1
                            TRASH.set_item(r["item_id"], status="failed",
                                           error="无法找到不冲突的新文件名")
                        continue

                # 预检：整组的回收站源文件必须都在（Live 组绝不部分恢复）
                missing = [r for r in members
                           if not os.path.isfile(_abs_trash(r["trash_rel"]))
                           or _is_link_like(_abs_trash(r["trash_rel"]))]
                if missing:
                    for r in missing:
                        failed += 1
                        TRASH.set_item(r["item_id"], status="missing",
                                       error="回收站内文件缺失，整组不恢复")
                        errors.append({"p": r["orig_rel"], "why": "回收站内文件缺失"})
                    for r in members:
                        if r in missing:
                            continue
                        failed += 1
                        TRASH.set_item(r["item_id"], status="failed",
                                       error="同组其它成员缺失，整组不恢复（保持配对完整）")
                    continue

                done_moves, gfail = [], None
                for r in members:
                    iid = r["item_id"]
                    src = _abs_trash(r["trash_rel"])
                    if suffix is not None:
                        d = os.path.dirname(r["orig_rel"])
                        stem, ext = os.path.splitext(os.path.basename(r["orig_rel"]))
                        nm = f"{stem}_{suffix}{ext}"
                        newrel = f"{d}/{nm}" if d else nm
                    else:
                        newrel = r["orig_rel"]
                    tgt = os.path.join(lib["root"], newrel.replace("/", os.sep))
                    TRASH.set_item(iid, status="moving")
                    try:
                        _mode, actual = _move_file(src, tgt)
                        done_moves.append((r, src, actual))
                    except Exception as e:
                        gfail = (r, src, e)
                        break
                if gfail:
                    # ★ 整组回滚：任一成员恢复失败 → 本组已恢复的一并退回回收站
                    for (r, src, actual) in done_moves:
                        try:
                            _mode2, back = _move_file(actual, src)
                            tra2 = os.path.relpath(back, TRASH_DIR).replace("\\", "/")
                            TRASH.set_item(r["item_id"], status="failed", trash_rel=tra2,
                                           error="整组回滚：同组成员恢复失败，已退回回收站")
                        except Exception as e2:
                            TRASH.set_item(r["item_id"], status="failed",
                                           error=f"整组回滚失败：{type(e2).__name__}: {e2}"
                                                 "（文件未丢失，请人工核对）")
                            log.error("恢复回滚失败：%s（%s）", r["orig_rel"], e2)
                        failed += 1
                    TRASH.set_item(gfail[0]["item_id"], status="failed",
                                   error=f"{type(gfail[2]).__name__}: {gfail[2]}")
                    failed += 1
                    errors.append({"p": gfail[0]["orig_rel"], "why": str(gfail[2])})
                    continue
                for (r, src, actual) in done_moves:
                    real_rel = os.path.relpath(actual, lib["root"]).replace("\\", "/")
                    TRASH.set_item(r["item_id"], status="restored", restored_at=now,
                                   new_rel=real_rel, error="")
                    restored += 1
                    details.append({"p": r["orig_rel"], "result": "restored",
                                    "newRel": real_rel})
                    affected.add((os.path.dirname(real_rel),
                                  clean_stem(os.path.basename(real_rel))))

            status = "done" if failed == 0 else ("failed" if restored == 0 else "partial")
            TRASH.finish_batch(batch_id, restored, failed, status)
            if affected:
                with INDEX_LOCK:
                    _index_add_restored(lid, lib["root"], details)
                    _repair_groups(lid, lib["root"], affected)
                    sync_memory(lib)
                _scan_cache_invalidate()
            TRASH.audit("restore", lid, batch_id, restored, status,
                        f"skipped={skipped} failed={failed} on_conflict={on_conflict}")
        finally:
            with _inflight_guard:
                _inflight_batches.discard(batch_id)

    return {"ok": True, "lib": lib_public(lib), "restored": restored,
            "failed": failed, "skipped": skipped, "groups": len(by_group),
            "items": details, "errors": errors, "batchId": batch_id}


def recover_trash():
    """
    启动时对账：把 status='pending' 的未收尾批次，按「文件现在到底在哪」收尾。
    无论中断发生在哪一步，都保证文件与记录不静默丢失。
    ★ op 感知：delete / restore / purge 三种批次的对账规则各不相同。
    """
    out = {"batches": 0, "items": 0}
    if TRASH is None:
        return out
    pend = TRASH.pending_batches()
    touched = {}
    days = int((META.get("trash") or DEFAULT_TRASH).get("retentionDays") or 30)
    now = int(time.time())
    for b in pend:
        lid, op, bid = b["lib_id"], b["op"], b["batch_id"]
        lib = (META.get("libs") or {}).get(lid)
        items = TRASH.items_of_batch(bid)
        ok = failed = 0
        del_rel, del_mov = [], []
        for it in items:
            st = it["status"]
            if st not in ("pending", "moving"):
                # 已经落定结果的条目：按结果计入，绝不重复动文件
                if st == "trashed":
                    ok += 1
                    if op == "delete":
                        del_rel.append(it["orig_rel"])
                        if it["kind"] == "mov":
                            del_mov.append(it["orig_rel"])
                elif st in ("restored", "purged"):
                    ok += 1
                elif st in ("failed", "missing"):
                    failed += 1
                continue
            at_trash = bool(it["trash_rel"]) and os.path.isfile(_abs_trash(it["trash_rel"]))
            at_orig = False
            if lib and lib.get("root"):
                try:
                    at_orig = os.path.isfile(safe_resolve(lib["root"], it["orig_rel"]))
                except Exception:
                    at_orig = False
            if op == "delete":
                if at_trash:
                    TRASH.set_item(it["item_id"], status="trashed",
                                   deleted_at=it["deleted_at"] or now,
                                   expire_at=it["expire_at"] or now + days * 86400,
                                   error="")
                    ok += 1
                    del_rel.append(it["orig_rel"])
                    if it["kind"] == "mov":
                        del_mov.append(it["orig_rel"])
                elif at_orig:
                    failed += 1
                    msg = ("中断恢复：文件仍在原位（未成功移入回收站）"
                           if _orig_looks_same(lib, it) else
                           "中断恢复：原位是同名替代文件（与记录不符），未删除、未移入回收站")
                    TRASH.set_item(it["item_id"], status="failed", error=msg)
                else:
                    failed += 1
                    TRASH.set_item(it["item_id"], status="missing",
                                   error="中断恢复：文件在两处均不存在")
            elif op == "restore":
                same = at_orig and _orig_looks_same(lib, it)
                if at_orig and at_trash:
                    if same:
                        # 目标已就位且与记录一致 → 恢复已完成；清掉回收站里的冗余副本
                        try:
                            ok_p, red, _why = _trash_item_path(it)
                            if ok_p and os.path.isfile(red) and not _is_link_like(red):
                                os.remove(red)
                                _prune_empty_dirs(os.path.dirname(red), TRASH_DIR)
                        except OSError:
                            pass
                        ok += 1
                        TRASH.set_item(it["item_id"], status="restored",
                                       restored_at=it["restored_at"] or now,
                                       new_rel=it["new_rel"] or it["orig_rel"], error="")
                    else:
                        # 两处都有、内容不同 → 原位是同名替代文件，绝不覆盖
                        failed += 1
                        TRASH.set_item(it["item_id"], status="conflict",
                                       error="中断恢复：原路径已有同名文件（内容不同），未覆盖；"
                                             "可改用「改名」策略恢复")
                elif same:
                    ok += 1
                    TRASH.set_item(it["item_id"], status="restored",
                                   restored_at=it["restored_at"] or now,
                                   new_rel=it["new_rel"] or it["orig_rel"], error="")
                elif at_orig:
                    failed += 1
                    TRASH.set_item(it["item_id"], status="failed",
                                   error="中断恢复：原位存在同名但不同的文件，不视为已恢复")
                elif at_trash:
                    failed += 1
                    TRASH.set_item(it["item_id"], status="failed",
                                   error="中断恢复：文件仍在回收站")
                else:
                    failed += 1
                    TRASH.set_item(it["item_id"], status="missing",
                                   error="中断恢复：文件在两处均不存在")
            elif op == "purge":
                # 永久删除只看回收站：文件不在 = 目标已达成；仍在 = 失败可重试
                if at_trash:
                    failed += 1
                    TRASH.set_item(it["item_id"], status="failed",
                                   error="中断恢复：回收站文件仍在，未永久删除（可重试）")
                else:
                    ok += 1
                    TRASH.set_item(it["item_id"], status="purged",
                                   purged_at=it["purged_at"] or now,
                                   error="中断恢复：回收站文件已不在（视为已完成）")
        TRASH.finish_batch(bid, ok, failed,
                           "done" if failed == 0 else ("failed" if ok == 0 else "partial"),
                           note="崩溃恢复收尾")
        TRASH.audit("recover", lid, bid, ok, "recovered", f"op={op} failed={failed}")
        out["batches"] += 1
        out["items"] += len(items)
        if del_rel:
            touched.setdefault(lid, {"rel": [], "mov": []})
            touched[lid]["rel"].extend(del_rel)
            touched[lid]["mov"].extend(del_mov)
    for lid, acc in touched.items():
        lib = (META.get("libs") or {}).get(lid)
        if not lib:
            continue
        try:
            with INDEX_LOCK:
                INDEX.delete_files(lid, acc["rel"])
                if acc["mov"]:
                    INDEX.drop_pairs_for_movies(lid, acc["mov"])
                sync_memory(lib)
        except Exception as e:
            log.error("崩溃恢复后同步索引失败 %s: %s", lid, e)
    return out


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def _trash_item_path(item):
    """
    把账本条目映射到回收站内的绝对路径，并做 realpath 前缀校验。
    ★ 越界（symlink 逃逸 / 手工篡改 trash_rel）一律拒绝。
    返回 (ok, abs_path, why)。
    """
    tr = (item.get("trash_rel") or "").strip()
    if not tr:
        return False, None, "条目未记录回收站路径"
    p = _abs_trash(tr)
    try:
        rp = os.path.realpath(p)
    except OSError as e:
        return False, None, f"路径不可解析：{e}"
    if not rp.startswith(_trash_root_real()):
        return False, None, "路径越界：不在受控回收站目录内"
    return True, rp, ""


def _prune_empty_dirs(path, stop):
    """自下而上删除**空**目录，直到 stop（不含）。只删空目录，绝不动文件。"""
    stop = os.path.realpath(stop)
    cur = os.path.realpath(path)
    removed = 0
    while cur != stop and cur.startswith(stop + os.sep):
        try:
            os.rmdir(cur)
            removed += 1
        except OSError:
            break
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return removed


def _purge_items(lid, items, note=""):
    """
    真正执行永久删除：逐个 os.remove，删除前四重校验。
    先落批次/条目 → 逐个标记 moving → 删除 → 标记 purged；中断后由
    recover_trash() 按「回收站文件还在不在」对账。
    ★ 绝不按目录递归删除；与「缓存孤儿回收」不共用任何删除代码。
    """
    if not items:
        return {"ok": True, "purged": 0, "failed": 0, "bytes": 0,
                "errors": [], "batchId": None}
    batch_id = trash_db.make_id("pur")
    now = int(time.time())
    purged = failed = 0
    freed = 0
    errors = []
    with MGMT_LOCK:                      # 与删除/恢复串行；防并发重复调度
        with _inflight_guard:
            if batch_id in _inflight_batches:
                return {"ok": False, "error": "同一批次正在执行"}
            _inflight_batches.add(batch_id)
        try:
            TRASH.create_batch(batch_id, lid, "purge", len(items), note=note)
            TRASH.link(batch_id, [it["item_id"] for it in items])
            for it in items:
                iid = it["item_id"]
                cur = TRASH.item(iid)
                if not cur or cur["status"] not in TRASH_LIVE_STATES:   # 防重复调度/并发
                    continue
                ok, path, why = _trash_item_path(it)
                if not ok:
                    failed += 1
                    TRASH.set_item(iid, status="failed", error=why)
                    errors.append({"p": it["orig_rel"], "why": why})
                    continue
                if _is_link_like(path):
                    failed += 1
                    TRASH.set_item(iid, status="failed",
                                   error="拒绝删除：回收站内该项是链接/junction")
                    errors.append({"p": it["orig_rel"], "why": "链接/junction"})
                    continue
                if os.path.isdir(path) and not os.path.isfile(path):
                    failed += 1
                    TRASH.set_item(iid, status="failed",
                                   error="拒绝删除：目标是目录（绝不递归删除）")
                    errors.append({"p": it["orig_rel"], "why": "目标是目录"})
                    continue
                if not os.path.exists(path):
                    # 文件已不在：目标已达成，记为已永久删除（记录与审计都保留）
                    TRASH.set_item(iid, status="purged", purged_at=now,
                                   error="文件已不存在，记为已永久删除")
                    purged += 1
                    continue
                TRASH.set_item(iid, status="moving", error="")
                try:
                    sz = os.path.getsize(path)
                    os.remove(path)
                    TRASH.set_item(iid, status="purged", purged_at=now, error="")
                    purged += 1
                    freed += sz
                    _prune_empty_dirs(os.path.dirname(path), TRASH_DIR)
                except Exception as e:
                    failed += 1
                    TRASH.set_item(iid, status="failed",
                                   error=f"永久删除失败：{type(e).__name__}: {e}")
                    errors.append({"p": it["orig_rel"], "why": str(e)})
            status = "done" if failed == 0 else ("failed" if purged == 0 else "partial")
            TRASH.finish_batch(batch_id, purged, failed, status)
            TRASH.audit("purge", lid, batch_id, purged, status,
                        f"{note} freed={freed} failed={failed}".strip())
        finally:
            with _inflight_guard:
                _inflight_batches.discard(batch_id)
    if purged:
        _scan_cache_invalidate()          # 回收站缩略图缓存随之失效（由 GC 收集）
    return {"ok": True, "purged": purged, "failed": failed, "bytes": freed,
            "errors": errors, "batchId": batch_id}


def mgmt_purge(lib, item_ids=None, batch=None, note=""):
    """
    永久删除（阶段五）。**只处理账本中仍在回收站里的条目**（trashed / failed /
    conflict / missing）：
      - batch：整批永久删除
      - item_ids：指定条目永久删除（「立即永久删除」）
    ★ 已 restored / purged 的条目永不再动。
    """
    if TRASH is None:
        return {"ok": False, "error": "回收站账本未初始化"}
    lid = lib["id"]
    if batch:
        rows = [r for r in TRASH.items_of_batch(batch)
                if r["lib_id"] == lid and r["status"] in TRASH_LIVE_STATES]
    else:
        rows = [r for r in TRASH.items_by_ids(item_ids or [])
                if r["lib_id"] == lid and r["status"] in TRASH_LIVE_STATES]
    if not rows:
        return {"ok": True, "purged": 0, "failed": 0, "bytes": 0,
                "errors": [], "batchId": None, "candidates": 0}
    res = _purge_items(lid, rows, note=note)
    res["candidates"] = len(rows)
    return res


def purge_expired(now=None):
    """
    把所有图库中**已到期**的回收站条目永久删除（启动 + 定时 + 停机补做共用）。
    停机期间错过的时间点，会在下次启动时由 expire_at<=now 自然补上。
    """
    if TRASH is None:
        return {"ok": True, "purged": 0, "failed": 0, "batches": 0, "bytes": 0}
    rows = TRASH.expired(now)
    if not rows:
        return {"ok": True, "purged": 0, "failed": 0, "batches": 0, "bytes": 0}
    by_lib = {}
    for r in rows:
        by_lib.setdefault(r["lib_id"], []).append(r)
    tot = {"ok": True, "purged": 0, "failed": 0, "batches": 0, "bytes": 0}
    for lid, items in by_lib.items():
        res = _purge_items(lid, items, note="到期自动永久删除")
        tot["purged"] += res.get("purged", 0)
        tot["failed"] += res.get("failed", 0)
        tot["bytes"] += res.get("bytes", 0)
        tot["batches"] += 1
    return tot


def purge_loop():
    cfg = META.get("trash") or DEFAULT_TRASH
    interval = int(cfg.get("purgeIntervalSeconds") or 0)
    if interval <= 0 or not cfg.get("autoPurge", True):
        return
    log.info("回收站到期检查已启动：每 %s 秒", interval)
    while not _purge_stop.wait(interval):
        try:
            r = purge_expired()
            if r.get("purged") or r.get("failed"):
                log.info("回收站到期检查：永久删除 %s 项 / 失败 %s 项",
                         r["purged"], r["failed"])
        except Exception as e:
            log.error("回收站到期检查异常：%s", e)


# =========================================================
#  缓存（磁盘 + 单飞 + 并发闸门）
# =========================================================
def cache_key(rel, mtime, size, kind, extra="", lib_id=""):
    """
    缓存键 = sha256(lib_id | rel | mtime | size | kind | CACHE_VERSION | extra)
    ★ lib_id 参与哈希 → 不同图库下的同名文件互不串用（多图库身份隔离）。
    """
    raw = f"{lib_id}|{rel}|{int(mtime)}|{size}|{kind}|{CACHE_VERSION}|{extra}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return h[:2], h[2:4], h


def cache_path(shard1, shard2, h, ext=".cache"):
    """纯路径拼接，**不创建目录**。
    目录改由写入方在真正落盘前创建 —— 否则每次「只查询、未命中」都会凭空
    生成一个空目录，日积月累把 cache/ 撑成几万个空目录（实测 5.3 万个）。"""
    return os.path.join(CACHE_DIR, shard1, shard2, h + ext)


def single_flight(key, producer):
    with _inflight_guard:
        lock = _inflight.get(key)
        if lock is None:
            lock = threading.Lock()
            _inflight[key] = lock
            owner = True
        else:
            owner = False
    if owner:
        try:
            with lock:
                data = producer()
            return data, True
        finally:
            with _inflight_guard:
                _inflight.pop(key, None)
    else:
        bump("singleflight_wait")
        with lock:
            pass
        return None, False


# =========================================================
#  媒体处理
# =========================================================
def _register_heif():
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return True
    except Exception:
        return False


HEIF_OK = _register_heif()


def pick_thumb(req):
    try:
        s = int(req)
    except (TypeError, ValueError):
        return THUMB_DEFAULT
    return min(THUMB_ALLOWED, key=lambda x: abs(x - s))


def gen_video_frame(path, max_side, quality=THUMB_QUALITY, fmt="webp"):
    """
    用 ffmpeg 抽一帧（独立视频的缩略图 / 高清静帧）。
    先试 0.5s 处（避开开头的黑场/抖动），失败再退回第一帧。
    """
    if not FFMPEG:
        raise RuntimeError("未找到 ffmpeg，无法为视频生成画面")
    from PIL import Image
    frame = None
    for seek in ("0.5", "0"):
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error"]
        if seek != "0":
            cmd += ["-ss", seek]
        cmd += ["-i", path, "-frames:v", "1"]
        if max_side:
            cmd += ["-vf", f"scale='min({max_side},iw)':-2"]
        cmd += ["-f", "image2pipe", "-vcodec", "mjpeg", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            continue
        if r.returncode == 0 and r.stdout:
            frame = r.stdout
            break
    if not frame:
        raise RuntimeError("视频抽帧失败")
    im = Image.open(io.BytesIO(frame))
    im.load()
    buf = io.BytesIO()
    if fmt == "jpeg":
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        mime = "image/jpeg"
    else:
        im.save(buf, format="WEBP", quality=quality, method=4)
        mime = "image/webp"
    return buf.getvalue(), mime, im.width, im.height


def is_video(path):
    return clean_ext(os.path.basename(path)) in MOV_EXT


def gen_image(path, max_side, quality, fmt="webp"):
    """把任意图片（含 HEIC）转成 WebP / JPEG 字节流，自动按 EXIF 摆正。"""
    from PIL import Image, ImageOps
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)        # ★ 手机竖拍方向修正
    if max_side and (im.width > max_side or im.height > max_side):
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "jpeg":
        if im.mode in ("RGBA", "P", "LA", "PA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1] if im.mode == "RGBA" else None)
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        mime = "image/jpeg"
    else:
        if im.mode == "P":
            im = im.convert("RGBA")
        im.save(buf, format="WEBP", quality=quality, method=4)
        mime = "image/webp"
    return buf.getvalue(), mime, im.width, im.height


def render_media(fullpath, max_side, quality, fmt):
    """图片走 PIL；视频走 ffmpeg 抽帧。二者产物进入同一套磁盘缓存。"""
    if is_video(fullpath):
        return gen_video_frame(fullpath, max_side, quality, fmt)
    return gen_image(fullpath, max_side, quality, fmt)


def cached_image(lib, rel, fullpath, st, kind, max_side, quality, fmt, extra=""):
    """带磁盘缓存 + 单飞的媒体生成（图片转码 / 视频抽帧）。返回 (bytes, mime, hit)"""
    h = cache_key(rel, st.st_mtime, st.st_size, kind + fmt + str(max_side),
                  extra, lib_id=lib["id"])[2]
    cp = cache_path(h[:2], h[2:4], h)
    variant = f"{kind}|{fmt}|{max_side}" + (f"|{extra}" if extra else "")
    if os.path.isfile(cp):
        bump("hit")
        cache_record(h, lib["id"], rel, st, kind, variant,
                     os.path.getsize(cp))
        with open(cp, "rb") as f:
            data = f.read()
        return data, ("image/jpeg" if fmt == "jpeg" else "image/webp"), True
    bump("miss")

    def produce():
        with _gen_sem:
            data, mime, _, _ = render_media(fullpath, max_side, quality, fmt)
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        tmp = cp + f".{secrets.token_hex(4)}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cp)
        cache_record(h, lib["id"], rel, st, kind, variant, len(data))
        bump("gen")
        return data, mime

    res, is_new = single_flight(kind + h, produce)
    if res is None:                      # 等待者：读磁盘
        with open(cp, "rb") as f:
            data = f.read()
        res = (data, "image/jpeg" if fmt == "jpeg" else "image/webp")
    return res[0], res[1], False


# ---------- 视频转码 / faststart ----------
def build_transcode(src, lib_id="", rel=""):
    st = os.stat(src)
    key = hashlib.sha256(f"tc3|{src}|{int(st.st_mtime)}|{st.st_size}".encode()).hexdigest()
    out = os.path.join(CACHE_DIR, "trans", key[:2], key + ".mp4")
    if os.path.isfile(out):
        cache_record(key, lib_id, rel, st, "trans",
                     f"h264-crf23|{int(st.st_size)}", os.path.getsize(out))
        return out, True
    if not FFMPEG:
        return None, False
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp.mp4"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
           "-movflags", "+faststart", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not os.path.isfile(tmp):
            log.error("transcode failed: %s", (r.stderr or "")[:400])
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None, False
        os.replace(tmp, out)
        cache_record(key, lib_id, rel, st, "trans",
                     f"h264-crf23|{int(st.st_size)}", os.path.getsize(out))
        bump("transcode")
        return out, False
    except subprocess.TimeoutExpired:
        log.error("transcode timeout: %s", src)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None, False
    except Exception as e:
        log.error("transcode error: %s", e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None, False


# =========================================================
#  缓存回收：孤儿识别 → 预览 → 清理
#  ★ 与「删除源照片」是两条完全独立的路径：
#    本段落只读 cache/ 目录，删除时再做 realpath 前缀双校验，
#    任何情况下都不会写入 / 删除源照片、源视频。
# =========================================================
_GC_CLASSES = ("tmp_leak", "unrecorded", "missing_source", "changed_source")
_GC_DESC = {
    "tmp_leak": "中断残留的临时文件（生成/转码未完成）",
    "unrecorded": "清单无记录，且不可能被任何有效源命中",
    "missing_source": "来源文件已从图库中消失",
    "changed_source": "来源文件已改动（mtime/大小变化），旧缓存永久失效",
}


_scan_memo = {"at": 0.0, "files": {}, "empty": []}
_scan_lock = threading.Lock()
_SCAN_TTL = 10.0          # 秒：避免信息面板与预览按钮重复付遍历代价
_FULL_SCAN_KEY = "cache_full_scan_at"
_EMPTY_DIRS_KEY = "cache_empty_dirs"


def _list_dirents(path):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def _full_scan_done():
    if CACHE_INDEX is None:
        return False
    try:
        return bool(CACHE_INDEX.get_meta(_FULL_SCAN_KEY))
    except Exception:
        return False


def _cache_leaf_dirs():
    """清单里出现过的「叶子缓存目录」集合（绝对路径）。

    这是把扫描从 5 万目录降到几百个的关键：cache/ 两级分片历史上被旧版
    cache_path() 的「先建目录」攒下了 5.3 万个空目录，逐个 scandir 不划算。
    而我们自己写缓存时都会登记清单 → 有文件的目录一定在清单里，
    于是「清单没有的叶子目录」必然是空的，可以直接跳过。
    """
    out = set()
    if CACHE_INDEX is None:
        return out
    try:
        entries = CACHE_INDEX.all_entries()
    except Exception:
        return out
    for e in entries:
        k = e["key"]
        if e["kind"] == "trans":
            out.add(os.path.join(CACHE_DIR, "trans", k[:2]))
        else:
            out.add(os.path.join(CACHE_DIR, k[:2], k[2:4]))
    return out


def _scan_cache(force=False, full=None):
    """
    遍历 cache/，返回 (files{路径: 字节}, empty_dirs[路径])。

    full=True  全量：所有叶子目录都进去看（能发现清单外的遗留文件，慢）
    full=False 剪枝：只进清单已知有文件的目录（快 20 倍以上，日常用）
    full=None  自动：还没做过全量扫描就先做一次全量（保证不漏遗留文件），
               之后长期走剪枝。

    为什么不用 os.walk：它会把每个目录都当文件目录走一遍；这里用 scandir 直取
    DirEntry（Windows 上 size/mtime 由目录项直接给出，零额外系统调用）。
    """
    now = time.time()
    with _scan_lock:
        if (not force) and _scan_memo["files"] and \
           now - _scan_memo["at"] < _SCAN_TTL:
            return _scan_memo["files"], _scan_memo["empty"]

    if full is None:
        full = not _full_scan_done()
    known = None
    cutoff = 0.0
    if not full:
        known = _cache_leaf_dirs()
        try:
            cutoff = float(CACHE_INDEX.get_meta(_FULL_SCAN_KEY) or 0) - 120
        except Exception:
            cutoff = 0.0

    files, empty = {}, []
    for top in _list_dirents(CACHE_DIR):
        try:
            is_dir = top.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if not is_dir:
            try:
                files[top.path] = top.stat(follow_symlinks=False).st_size
            except OSError:
                pass
            continue
        is_trans = (top.name == "trans")
        for mid in _list_dirents(top.path):
            try:
                mid_dir = mid.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if not mid_dir:
                try:
                    files[mid.path] = mid.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
                continue
            # 剪枝：不在清单里、且自上轮全量扫描以来没被动过的叶子目录，
            # 必然是旧版遗留的空目录 —— 跳过。Windows 上 stat 由目录项直接给出，
            # 这次判断不产生额外系统调用。
            if known is not None and (not is_trans) and (mid.path not in known):
                try:
                    if mid.stat(follow_symlinks=False).st_mtime <= cutoff:
                        continue
                except OSError:
                    continue
            got_file = got_dir = False
            for f in _list_dirents(mid.path):
                try:
                    if f.is_dir(follow_symlinks=False):
                        got_dir = True
                    elif f.is_file(follow_symlinks=False):
                        files[f.path] = f.stat(follow_symlinks=False).st_size
                        got_file = True
                except OSError:
                    pass
            if not got_file and not got_dir:
                empty.append(mid.path)

    if full and CACHE_INDEX is not None:
        try:
            CACHE_INDEX.set_meta(_FULL_SCAN_KEY, int(now))
            CACHE_INDEX.set_meta(_EMPTY_DIRS_KEY, len(empty))
        except Exception:
            pass

    with _scan_lock:
        _scan_memo["at"] = now
        _scan_memo["files"] = files
        _scan_memo["empty"] = empty
    return files, empty


def _scan_cache_invalidate():
    with _scan_lock:
        _scan_memo["at"] = 0.0
        _scan_memo["files"] = {}
        _scan_memo["empty"] = []


def _empty_dir_count():
    """空目录数量：优先用最近一次全量扫描的记录（剪枝扫描看不到全部空目录）。"""
    if CACHE_INDEX is not None:
        try:
            v = CACHE_INDEX.get_meta(_EMPTY_DIRS_KEY)
            if v is not None:
                return int(v)
        except Exception:
            pass
    try:
        return len(_scan_cache()[1])
    except Exception:
        return 0


def _walk_cache_files():
    """cache/ 下所有普通文件 → {绝对路径: 字节数}。只读，不改动任何东西。"""
    return _scan_cache()[0]


def _cache_name_map(full=False):
    """{文件名: (绝对路径, 字节数)}，用于判断某个 key 对应的文件在不在盘上。

    full=True 时强制全量遍历 —— 收养必须用全量，否则会漏掉「目录尚不在清单里」
    的那些既有缓存，把它们误判成孤儿。
    """
    files = _scan_cache(force=True, full=True)[0] if full else _scan_cache()[0]
    m = {}
    for p, sz in files.items():
        m[os.path.basename(p)] = (p, sz)
    return m


def _key_of_cache_name(name):
    """把缓存文件名还原成 key（sha256）。"""
    if name.endswith(".cache"):
        return name[:-6]
    if name.endswith(".mp4"):
        return name[:-4]
    return name


def cache_adopt():
    """
    把「盘上已有、且仍然可达」的缓存登记进清单。

    做法不是反向破解哈希，而是**正向重算**：遍历索引里每个源文件 × 已知参数组合，
    算出 key，如果磁盘上恰好有同名文件，就认领它。这样既不用改缓存文件命名，
    又能把清单建立之前的既有缓存纳管，避免它们被误判为孤儿。

    返回新认领的条数。
    """
    if CACHE_INDEX is None or INDEX is None:
        return 0
    names = _cache_name_map(full=True)
    rows, now = [], int(time.time())
    for lid in META["lib_order"]:
        lib = META["libs"].get(lid)
        if not lib or not lib.get("exists", True) or lib.get("stale"):
            continue
        try:
            files = INDEX.all_files(lid)
        except Exception:
            files = {}
        for rel, f in files.items():
            mtime, size = f["mtime"], f["size"]
            # 缩略图：尺寸档 × webp（只比对文件名，不做任何文件系统操作）
            for side in THUMB_ALLOWED:
                h = cache_key(rel, mtime, size, "thumbwebp" + str(side),
                              "", lib_id=lid)[2]
                hit = names.get(h + ".cache")
                if hit:
                    rows.append((h, lid, rel, mtime, size, "thumb",
                                 f"thumb|webp|{side}", hit[1], now))
            # 大图：webp / jpeg
            for fmt in ("webp", "jpeg"):
                h = cache_key(rel, mtime, size, "photo" + fmt + str(PHOTO_MAX),
                              "", lib_id=lid)[2]
                hit = names.get(h + ".cache")
                if hit:
                    rows.append((h, lid, rel, mtime, size, "photo",
                                 f"photo|{fmt}|{PHOTO_MAX}", hit[1], now))
            # 转码产物：key 与 build_transcode 完全一致（含绝对路径）
            if f["kind"] == "mov":
                try:
                    fp = safe_resolve(lib["root"], rel)
                except Exception:
                    continue
                key = hashlib.sha256(
                    f"tc3|{fp}|{int(mtime)}|{size}".encode()).hexdigest()
                hit = names.get(key + ".mp4")
                if hit:
                    rows.append((key, lid, rel, mtime, size, "trans",
                                 f"h264-crf23|{int(size)}", hit[1], now))
    n = CACHE_INDEX.record_batch(rows)
    if n:
        CACHE_INDEX.mark_adopted()
    return n


def cache_adopt_if_needed():
    """清单为空但缓存目录非空时，自动收养一次（首次升级到本版本时会走到）。"""
    if CACHE_INDEX is None:
        return 0
    try:
        if CACHE_INDEX.count() > 0:
            return 0
    except Exception:
        return 0
    return cache_adopt()


def _gc_collect(classes=None, force=True, full=None):
    """
    内部实现：把 cache/ 下的文件逐个判定，返回完整明细（不截断）。
    绝不修改 / 删除任何文件。
    force=True 时强制重新遍历磁盘（预览/执行都用它，保证数字是当下真实值）。
    """
    want = set(classes) if classes else set(_GC_CLASSES)
    files, empty_dirs = _scan_cache(force=force, full=full)
    entries = {}
    if CACHE_INDEX is not None:
        for e in CACHE_INDEX.all_entries():
            entries[e["key"]] = e

    state_cache = {}

    def src_state(lid, rel):
        """返回 ('ok', mtime, size) / ('offline',) / ('gone',)"""
        k = (lid, rel)
        if k in state_cache:
            return state_cache[k]
        lib = META["libs"].get(lid)
        v = ("gone",)
        if not lib or not lib.get("root"):
            v = ("gone",)
        elif not lib.get("exists", True) or lib.get("stale"):
            v = ("offline",)            # 图库离线 → 保护，绝不判孤儿
        else:
            try:
                st = os.stat(safe_resolve(lib["root"], rel))
                v = ("ok", st.st_mtime, st.st_size)
            except Exception:
                v = ("gone",)
                if TRASH is not None:
                    try:
                        if TRASH.is_trashed(lid, rel):
                            v = ("trashed",)   # 源在回收站 ≠ 源已消失 → 保护
                    except Exception:
                        pass
        state_cache[k] = v
        return v

    items = []          # {path, size, cls, why}；cls=None 表示受保护（保留）
    protected = 0
    for path, size in files.items():
        name = os.path.basename(path)
        if ".tmp" in name.lower():
            items.append({"path": path, "size": size, "cls": "tmp_leak",
                          "why": "未完成的生成/转码残留"})
            continue
        e = entries.get(_key_of_cache_name(name))
        if e is None:
            items.append({"path": path, "size": size, "cls": "unrecorded",
                          "why": "清单无记录"})
            continue
        stt = src_state(e["lib_id"], e["rel"])
        if stt[0] == "offline":
            protected += 1
            items.append({"path": path, "size": size, "cls": None,
                          "why": f"图库离线，保留（{e['lib_id']}）"})
        elif stt[0] == "trashed":
            protected += 1
            items.append({"path": path, "size": size, "cls": None,
                          "why": f"源在回收站，保留（{e['lib_id']}/{e['rel']}）"})
        elif stt[0] == "gone":
            items.append({"path": path, "size": size, "cls": "missing_source",
                          "why": f"源已不存在：{e['lib_id']}/{e['rel']}"})
        elif (stt[1] != e["src_mtime"]) or (int(stt[2]) != int(e["src_size"])):
            items.append({"path": path, "size": size, "cls": "changed_source",
                          "why": f"源已改动：{e['lib_id']}/{e['rel']}"})

    # 清单里存在、但盘上没有对应文件的「幽灵行」
    ghost = []
    if CACHE_INDEX is not None:
        for k, e in entries.items():
            if not os.path.isfile(cache_file_for(k, e["kind"])):
                ghost.append(k)

    # 过滤出用户要求的类别
    picked = [it for it in items if it["cls"] and it["cls"] in want]
    return picked, ghost, protected, len(files), sum(files.values()), len(empty_dirs)


def cache_gc_plan(classes=None, force=True, full=None):
    """只读预览：预计可删除的孤儿缓存数量与空间。不删任何文件。"""
    picked, ghost, protected, tot_files, tot_bytes, empty_dirs = \
        _gc_collect(classes, force, full)
    by_class = []
    for cls in _GC_CLASSES:
        sub = [it for it in picked if it["cls"] == cls]
        if not sub:
            continue
        sample = [{
            "p": os.path.relpath(it["path"], APP_DIR).replace("\\", "/"),
            "kb": round(it["size"] / 1024, 1), "why": it["why"]}
            for it in sorted(sub, key=lambda x: -x["size"])[:10]]
        by_class.append({"cls": cls, "desc": _GC_DESC[cls],
                         "files": len(sub),
                         "bytes": sum(it["size"] for it in sub),
                         "sample": sample})
    o_files = len(picked)
    o_bytes = sum(it["size"] for it in picked)
    return {
        "cache": {"files": tot_files, "bytes": tot_bytes},
        "orphan": {"files": o_files, "bytes": o_bytes},
        "keep": {"files": tot_files - o_files, "bytes": tot_bytes - o_bytes,
                 "protectedOffline": protected},
        "byClass": by_class,
        "ghostRows": len(ghost),
        "emptyDirs": empty_dirs,
        "allClasses": list(_GC_CLASSES),
        "modes": {"dry": True},
    }


def cache_gc_apply(classes=None, full=None):
    """
    执行清理：**只删除 cache/ 目录内的孤儿缓存文件**。
    每一处删除前都做 realpath 前缀双校验；源照片目录永远不在删除范围内。
    返回 {deleted, bytes, ghostRows, errors}。
    """
    picked, ghost, _protected, _tf, _tb, _ed = _gc_collect(classes, True, full)
    cache_root = os.path.realpath(CACHE_DIR) + os.sep
    deleted = freed = 0
    errors = []
    removed_keys = []
    for it in picked:
        p = it["path"]
        try:
            rp = os.path.realpath(p)
            # ── 安全闸门 1：必须真实位于 cache/ 之内 ──
            if not rp.startswith(cache_root):
                errors.append(f"越界拒绝: {p}")
                continue
            # ── 安全闸门 2：只删普通文件（拒绝目录 / 指向外部的链接）──
            if not os.path.isfile(rp) or os.path.islink(p):
                errors.append(f"非常规文件，跳过: {p}")
                continue
            sz = os.path.getsize(rp)
            os.remove(rp)
            deleted += 1
            freed += sz
            k = _key_of_cache_name(os.path.basename(p))
            if CACHE_INDEX is not None:
                removed_keys.append(k)
                with _recorded_memo_lock:
                    _recorded_memo.discard(k)
        except FileNotFoundError:
            continue
        except Exception as e:
            errors.append(f"{p}: {e}")

    ghost_removed = 0
    if CACHE_INDEX is not None:
        try:
            if removed_keys:
                CACHE_INDEX.delete_keys(removed_keys)
            if ghost:
                ghost_removed = CACHE_INDEX.delete_keys(ghost)
                with _recorded_memo_lock:
                    for k in ghost:
                        _recorded_memo.discard(k)
        except Exception as e:
            errors.append(f"清单清理失败: {e}")

    # 空分片目录：只统计、**不在这里删**。
    # 实测本机 os.rmdir 约 112ms/次（杀软实时扫描介入），历史遗留的 5 万个空目录
    # 逐个删除要 1.6 小时，放在请求里做会把连接拖死。真正要防的是「别再造出空目录」——
    # 已由 cache_path() 改为纯路径拼接解决；存量空目录见 README 的离线清理命令。
    empty_dirs = 0
    try:
        empty_dirs = len(_scan_cache(force=False)[1])
    except Exception:
        pass

    _scan_cache_invalidate()                          # 文件已变，作废列表缓存
    return {"deleted": deleted, "bytes": freed,
            "ghostRows": ghost_removed, "emptyDirs": empty_dirs,
            "errors": errors[:20],
            "modes": {"dry": False}}


def probe_codec(src):
    """返回 'h264' / 'hevc' / None"""
    if not FFMPEG:
        return None
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", "-i", src],
                           capture_output=True, text=True, timeout=20)
        txt = (r.stderr or "") + (r.stdout or "")
        if "Video: h264" in txt:
            return "h264"
        if "hevc" in txt.lower() or "hvc1" in txt or "hev1" in txt:
            return "hevc"
        return None
    except Exception:
        return None


def parse_range(header, total):
    if not header:
        return 0, total - 1, "none"
    m = re.match(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", header, re.I)
    if not m:
        return 0, total - 1, "invalid"
    s, e = m.group(1), m.group(2)
    if s == "" and e == "":
        return 0, total - 1, "invalid"
    if s == "":
        n = int(e)
        if n <= 0:
            return 0, total - 1, "invalid"
        return max(0, total - n), total - 1, "ok"
    start = int(s)
    end = int(e) if e != "" else total - 1
    if start > end or start >= total:
        return 0, total - 1, "unsatisfiable"
    return start, min(end, total - 1), "ok"


# =========================================================
#  HTTP 服务
# =========================================================
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SeekLive/1.0"

    def log_message(self, fmt, *a):
        log.info("%s - %s", self.client_address[0], fmt % a)

    # ---------- 管理认证（只作用于源文件管理，不作用于浏览）----------
    def cookie_val(self, name):
        ck = self.headers.get("Cookie", "") or ""
        for part in ck.split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part[len(name) + 1:]
        return ""

    def admin_sid(self):
        return self.cookie_val(SESSION_COOKIE)

    def admin_authed(self):
        """当前请求是否带着有效的管理会话。浏览路径**不调用**本方法。"""
        if not admin_required():
            return False
        return admin_session_valid(self.admin_sid())

    def csrf_ok(self):
        """
        CSRF 防护第一道：管理 POST 必须带自定义头 X-SL-Admin: 1。
        跨站表单 / <img> / <script> 无法携带自定义头（会触发预检），
        本服务不开放 CORS，因此这一条已足以挡住跨站破坏性请求。
        """
        return (self.headers.get("X-SL-Admin") or "").strip() == "1"

    def same_origin_ok(self):
        """
        CSRF 防护第二道：若浏览器带了 Origin / Referer，则必须同源。
        两者都缺（本机 curl、脚本调用）时不拦 —— 那种情况仍需口令才能登录。
        """
        host = (self.headers.get("Host") or "").strip()
        org = (self.headers.get("Origin") or "").strip()
        if org:
            try:
                u = urllib.parse.urlparse(org)
                return bool(host) and u.netloc == host
            except Exception:
                return False
        ref = (self.headers.get("Referer") or "").strip()
        if ref:
            try:
                u = urllib.parse.urlparse(ref)
                return bool(host) and u.netloc == host
            except Exception:
                return False
        return True

    def send_need_auth(self, msg="需要管理授权"):
        """401 + needAuth 标记（前端据此弹授权框并自动重试一次）。"""
        self.send_bytes(
            json.dumps({"error": msg, "needAuth": True}, ensure_ascii=False
                       ).encode("utf-8"),
            "application/json; charset=utf-8", 401)

    # ---------- 发送 ----------
    def send_bytes(self, data, ctype, code=200, extra=None, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def send_json(self, obj, cache="no-store"):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", 200, cache=cache)

    def send_err(self, code, msg):
        self.send_bytes(f"{code} {msg}".encode(), "text/plain; charset=utf-8", code)

    # ---------- 路由 ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)

        # ★ 浏览路径完全匿名：这里刻意没有任何认证分支。
        #   /、静态资源、/api/libs、/api/tree、/api/items、/api/search、/api/item、
        #   /thumb、/photo、/raw、/video 一律不需要任何凭证。
        #   认证只出现在 do_POST 的管理接口里（mgmt_guard）。

        # ---- API ----
        if path == "/api/admin/status":
            return self.api_admin_status()
        if path == "/api/libs":
            return self.send_json({
                "libraries": [lib_public(META["libs"][i])
                              for i in META["lib_order"] if i in META["libs"]],
                "trash": {"retentionDays": int((META.get("trash") or DEFAULT_TRASH
                                                ).get("retentionDays") or 30)},
                # 首次索引可能还在后台跑：前端据此显示进度面板而不是「没有照片」
                "indexing": bool(idx_get().get("running")),
                "ready": BOOTSTRAP_DONE.is_set(),
            })
        if path == "/api/index/progress":
            return self.api_index_progress()
        if path == "/api/tree":
            return self.api_tree(qs)
        if path == "/api/items":
            return self.api_items(qs)
        if path == "/api/search":
            return self.api_search(qs)
        if path == "/api/item":
            return self.api_item(qs)
        if path == "/api/stats":
            return self.api_stats()
        if path == "/api/refresh":
            return self.api_refresh(qs)
        if path == "/api/index":
            return self.api_index()
        if path == "/api/cache":
            return self.api_cache()
        if path == "/api/cache/gc":
            return self.api_cache_gc(qs)
        if path == "/api/trash":
            return self.api_trash_list(qs)
        if path == "/api/trash/stats":
            return self.api_trash_stats()

        # ---- 媒体 ----
        if path == "/thumb":
            return self.serve_image(qs, "thumb")
        if path == "/photo":
            return self.serve_image(qs, "photo")
        if path == "/raw":
            return self.serve_raw(qs)
        if path == "/video":
            return self.serve_video(qs)
        # 回收站内媒体（只读；仅账本中登记、状态可预览的条目）
        if path == "/api/trash/thumb":
            return self.serve_trash_image(qs)
        if path == "/api/trash/media":
            return self.serve_trash_media(qs)

        # ---- 静态 ----
        # 静态资源与首页不再参与任何认证（整站闸门已移除），
        # 因此也不需要「入口下发 Cookie」这类补丁。
        if path in ("/", "/index.html"):
            return self.serve_static("index.html")
        if path in ("/app.css", "/app.js", "/favicon.svg", "/manifest.webmanifest"):
            return self.serve_static(path.lstrip("/"))

        return self.send_err(404, "not found")

    def do_POST(self):
        """所有有副作用的操作走 POST（GET 只做读取/预览/统计）。"""
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)
        # ★ 先把请求体读完再路由。若在「未读体」的情况下提前返回（例如 429 退避、
        #   401 未授权），残留字节会被 HTTP 解析器当成**下一个请求行** →
        #   出现 "Bad HTTP/0.9 request type" 并把请求体（可能含口令）写进访问日志。
        #   先读完即可同时消除「连接错乱」与「日志泄露」两个问题。
        self._body_raw = self._read_raw_body()
        # ---- 管理授权（口令只走请求体，**永不**走 URL —— 避免进入访问日志/历史）----
        if path == "/api/admin/login":
            return self.api_admin_login()
        if path == "/api/admin/logout":
            return self.api_admin_logout()
        # ---- 索引维护（非破坏性；全量重建才要管理授权）----
        if path == "/api/index/scan":
            return self.api_index_scan()
        # ---- 源文件管理（全部经 mgmt_guard 四道闸门）----
        if path == "/api/delete":
            return self.api_delete(qs)
        if path == "/api/trash/restore":
            return self.api_trash_restore(qs)
        if path == "/api/trash/purge":
            return self.api_trash_purge(qs)
        if path == "/api/trash/empty":
            return self.api_trash_empty(qs)
        return self.send_err(404, "not found")

    # ---------- 管理授权接口 ----------
    def api_index_scan(self):
        """POST /api/index/scan {full:0|1}
        触发一次后台索引并立即返回；前端轮询 /api/index/progress 看进度。
        增量重扫不删任何源文件，允许匿名触发；**全量重建会清空该库索引行**，
        因此要求已授权（且校验来源）。"""
        p = idx_get()
        if p.get("running"):
            return self.send_json({"ok": False, "busy": True,
                                   "error": "索引正在进行中，请稍候"})
        body = self.read_json()
        full = bool(body.get("full"))
        if full:
            if not admin_required():
                return self.send_err(403, "未配置管理口令：全量重建索引已禁用")
            if not self.admin_authed():
                return self.send_need_auth("全量重建索引需要管理授权")
            if not self.csrf_ok() or not self.same_origin_ok():
                return self.send_err(403, "来源校验未通过")
        threading.Thread(target=_bg_index, kwargs={"full": full},
                         name="idx-scan", daemon=True).start()
        return self.send_json({"ok": True, "full": full,
                              "mode": "full" if full else "incremental"})

    def api_admin_status(self):
        """只回答「要不要授权 / 当前是否已授权」，绝不返回口令或 sid。"""
        authed = self.admin_authed()
        exp = admin_session_expires(self.admin_sid()) if authed else None
        return self.send_json({
            "required": admin_required(),
            "authed": authed,
            "expiresAt": exp or 0,
            "sessionHours": float(ADMIN_CFG.get("sessionHours") or 0),
            "source": ADMIN_SOURCE if admin_required() else "",
        })

    def api_admin_login(self):
        ip = self.client_address[0] if self.client_address else "-"
        if not admin_required():
            return self.send_err(403, "未配置管理口令：源文件管理已禁用")
        ok, wait = admin_rate_check(ip)
        if not ok:
            self.send_bytes(
                json.dumps({"error": f"尝试过于频繁，请 {wait} 秒后再试"},
                           ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8", 429,
                extra={"Retry-After": str(wait)})
            return
        body = self.read_json()
        if not admin_login_ok(body.get("token")):
            n, delay = admin_rate_fail(ip)
            extra = f"（已连续失败 {n} 次）" if n >= 3 else ""
            self.send_bytes(
                json.dumps({"error": "口令错误" + extra}, ensure_ascii=False
                           ).encode("utf-8"),
                "application/json; charset=utf-8", 401,
                extra=({"Retry-After": str(delay)} if delay else None))
            return
        admin_rate_reset(ip)
        sid, exp = admin_session_create()
        maxage = int(ADMIN_CFG.get("sessionCookieMaxAge") or 0)
        cookie = (f"{SESSION_COOKIE}={sid}; Path=/; HttpOnly; SameSite=Lax"
                  + (f"; Max-Age={maxage}" if maxage > 0 else ""))
        self.send_bytes(
            json.dumps({"ok": True, "expiresAt": exp,
                        "sessionHours": float(ADMIN_CFG.get("sessionHours") or 0)},
                       ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8", 200,
            extra={"Set-Cookie": cookie})

    def api_admin_logout(self):
        if not self.csrf_ok():
            return self.send_err(403, "缺少管理请求头（X-SL-Admin）")
        admin_session_drop(self.admin_sid())
        cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        self.send_bytes(
            json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8", 200,
            extra={"Set-Cookie": cookie})

    # ---------- 管理请求的公共件 ----------
    def _read_raw_body(self, limit=16 * 1024 * 1024):
        """一次性读完请求体（上限 16MB，超限则放弃并关闭连接）。"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return b""
        if n > limit:
            self.close_connection = True
            return b""
        try:
            return self.rfile.read(n)
        except Exception:
            self.close_connection = True
            return b""

    def read_json(self):
        raw = getattr(self, "_body_raw", None)
        if raw is None:                       # 未经 do_POST 预读时兜底
            raw = self._read_raw_body()
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def mgmt_guard(self, lib, allow_offline=False):
        """源文件管理的服务端闸门（四道，顺序即优先级）。返回 (code, msg)；None = 放行。

        ① 未配置管理口令        → 403（管理功能整体关闭）
        ② 无有效管理会话        → 401 + needAuth（前端据此弹授权框）
        ③ 缺自定义头 / 跨站来源 → 403（CSRF 防护）
        ④ 图库自身不允许管理    → 403；图库离线 → 409

        ★ 浏览类请求**不会**经过这里；这四个破坏性接口一个都不能绕过。
        allow_offline=True：仅「永久删除回收站文件」使用（不碰图库目录）。
        """
        if not admin_required():
            return 403, "未配置管理口令：源文件管理功能已禁用（浏览不受影响）"
        if not self.admin_authed():
            return 401, "需要管理授权"
        if not self.csrf_ok():
            return 403, "缺少管理请求头（X-SL-Admin）"
        if not self.same_origin_ok():
            return 403, "来源校验未通过（跨站请求已拒绝）"
        allowed, reason = manage_state(lib, need_online=not allow_offline)
        if not allowed:
            return (409 if "离线" in reason else 403), reason
        return None, None

    def send_mgmt_err(self, code, msg):
        """管理接口的错误一律 JSON；401 额外带 needAuth，供前端弹授权框。"""
        if code == 401:
            return self.send_need_auth(msg)
        self.send_bytes(
            json.dumps({"error": msg}, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8", code)

    # ---------- 破坏性 API 实现 ----------
    def api_delete(self, qs):
        """POST /api/delete  {lib, items:[rel...], dry:0|1}
        dry=1 → 只预览（数量/类型/关联视频/清单）；dry=0 → 移入回收站。"""
        body = self.read_json()
        lid = body.get("lib") or (qs.get("lib") or [""])[0]
        lib = META["libs"].get(lid)
        if not lib:
            return self.send_err(404, "library not found")
        code, msg = self.mgmt_guard(lib)
        if code:
            return self.send_mgmt_err(code, msg)
        rels = body.get("items")
        if not isinstance(rels, list) or not rels:
            return self.send_err(400, "items 不能为空")
        dry = str(body.get("dry", (qs.get("dry") or ["0"])[0])) in ("1", "true", "True")
        try:
            res = mgmt_delete(lib, rels, dry=dry)
        except Exception as e:
            log.exception("delete failed")
            return self.send_err(500, f"删除失败：{e}")
        if not res.get("ok"):
            return self.send_err(400, res.get("error") or "删除未执行")
        return self.send_json(res)

    def api_trash_restore(self, qs):
        """POST /api/trash/restore  {lib, items:[itemId...] | batch, on_conflict}"""
        body = self.read_json()
        lid = body.get("lib") or (qs.get("lib") or [""])[0]
        lib = META["libs"].get(lid)
        if not lib:
            return self.send_err(404, "library not found")
        code, msg = self.mgmt_guard(lib)
        if code:
            return self.send_mgmt_err(code, msg)
        items = body.get("items") or []
        batch = body.get("batch") or (qs.get("batch") or [""])[0]
        on_conflict = body.get("on_conflict") or (qs.get("on_conflict") or ["skip"])[0]
        if not items and not batch:
            return self.send_err(400, "需要 items 或 batch")
        try:
            res = mgmt_restore(lib, items=items, batch=batch, on_conflict=on_conflict)
        except Exception as e:
            log.exception("restore failed")
            return self.send_err(500, f"恢复失败：{e}")
        return self.send_json(res)

    def api_trash_list(self, qs):
        """GET /api/trash?lib=  回收站列表（按逻辑项目组聚合，只读）。"""
        retention = int((META.get("trash") or DEFAULT_TRASH
                         ).get("retentionDays") or 30)
        if TRASH is None:
            return self.send_json({"groups": [], "stats": {}, "now": int(time.time()),
                                   "retentionDays": retention, "manage": {}})
        lid = (qs.get("lib") or [""])[0]
        raw = TRASH.list_items(lib_id=lid or None, status=TRASH_LIVE_STATES)
        now = int(time.time())
        groups = {}
        for r in raw:
            key = r["group_key"] or r["item_id"]
            g = groups.get(key)
            if g is None:
                lib = META["libs"].get(r["lib_id"])
                if lib:
                    allowed, reason = manage_state(lib, need_online=False)
                else:
                    allowed, reason = False, "图库不存在"
                g = groups[key] = {
                    "group": key, "lib": r["lib_id"],
                    "libName": (lib.get("name") if lib else r["lib_id"]),
                    "live": bool(r["live"]), "batch": r["batch_id"],
                    "files": 0, "videos": 0, "bytes": 0,
                    "deletedAt": 0, "expireAt": 0, "items": [],
                    "canManage": allowed, "manageReason": reason,
                    "issues": [],
                }
            g["files"] += 1
            if r["kind"] == "mov":
                g["videos"] += 1
            g["bytes"] += r["size"]
            if r["status"] != "trashed":
                g["issues"].append(r["status"])
            da = r["deleted_at"] or 0
            ea = r["expire_at"] or 0
            if da and (not g["deletedAt"] or da < g["deletedAt"]):
                g["deletedAt"] = da
            if ea and (not g["expireAt"] or ea < g["expireAt"]):
                g["expireAt"] = ea
            g["items"].append({"itemId": r["item_id"], "p": r["orig_rel"],
                               "n": r["name"], "k": r["kind"],
                               "role": r["pair_role"], "z": r["size"],
                               "st": r["status"], "err": r["error"]})
        out = list(groups.values())
        for g in out:
            g["remainDays"] = (max(0, math.ceil((g["expireAt"] - now) / 86400))
                               if g["expireAt"] else None)
            g["expired"] = bool(g["expireAt"] and g["expireAt"] <= now)
            g["type"] = ("live" if g["live"] else
                         ("video" if g["videos"] and g["files"] == g["videos"]
                          else "photo"))
            g["issues"] = sorted(set(g["issues"]))
            g["hasIssue"] = bool(g["issues"])
        out.sort(key=lambda x: -(x["deletedAt"] or 0))
        return self.send_json({
            "groups": out, "stats": TRASH.stats(lid or None),
            "now": now, "retentionDays": retention,
            "manage": {i: manage_info(META["libs"][i]) for i in META["lib_order"]},
        })

    def api_trash_stats(self):
        if TRASH is None:
            return self.send_json({"items": 0, "bytes": 0, "expired": 0, "byLib": {}})
        s = TRASH.stats()
        s["retentionDays"] = int((META.get("trash") or DEFAULT_TRASH
                                  ).get("retentionDays") or 30)
        s["manage"] = {i: manage_info(META["libs"][i]) for i in META["lib_order"]}
        return self.send_json(s)

    # ---------- 永久删除 / 清空回收站（阶段五）----------
    def api_trash_purge(self, qs):
        """POST /api/trash/purge  {lib, items:[itemId...] | batch, expired:0|1, confirm:1}
        - items / batch：立即永久删除指定条目（不等到期）
        - expired=1    ：永久删除该库所有已到期条目
        ★ 必须 confirm=1（二次确认）；只处理账本中已登记且状态为 trashed 的条目。
        """
        body = self.read_json()
        lid = body.get("lib") or (qs.get("lib") or [""])[0]
        lib = META["libs"].get(lid)
        if not lib:
            return self.send_err(404, "library not found")
        code, msg = self.mgmt_guard(lib, allow_offline=True)
        if code:
            return self.send_mgmt_err(code, msg)
        if TRASH is None:
            return self.send_err(503, "回收站账本不可用")
        if not _truthy(body.get("confirm")):
            return self.send_err(400, "永久删除不可逆，需要 confirm=1 二次确认")
        if _truthy(body.get("expired")):
            now = int(time.time())
            rows = [r for r in TRASH.list_items(lib_id=lid, status="trashed")
                    if r["expire_at"] and r["expire_at"] <= now]
            res = _purge_items(lid, rows, note="立即永久删除（到期项）")
            res["candidates"] = len(rows)
        else:
            items = body.get("items") or []
            batch = body.get("batch") or (qs.get("batch") or [""])[0]
            if not items and not batch:
                return self.send_err(400, "需要 items / batch / expired")
            try:
                res = mgmt_purge(lib, item_ids=items, batch=batch, note="立即永久删除")
            except Exception as e:
                log.exception("purge failed")
                return self.send_err(500, f"永久删除失败：{e}")
        if not res.get("ok"):
            return self.send_err(400, res.get("error") or "永久删除未执行")
        return self.send_json(res)

    def api_trash_empty(self, qs):
        """POST /api/trash/empty  {lib, confirm:1}
        清空该图库回收站（永久删除其全部已删除条目）。需 confirm=1 二次确认。"""
        body = self.read_json()
        lid = body.get("lib") or (qs.get("lib") or [""])[0]
        lib = META["libs"].get(lid)
        if not lib:
            return self.send_err(404, "library not found")
        code, msg = self.mgmt_guard(lib, allow_offline=True)
        if code:
            return self.send_mgmt_err(code, msg)
        if TRASH is None:
            return self.send_err(503, "回收站账本不可用")
        if not _truthy(body.get("confirm")):
            return self.send_err(400, "清空回收站不可逆，需要 confirm=1 二次确认")
        rows = TRASH.list_items(lib_id=lid, status=TRASH_LIVE_STATES)
        res = _purge_items(lid, rows, note="清空回收站")
        res["candidates"] = len(rows)
        return self.send_json(res)

    # ---------- 回收站媒体（只读）----------
    def _resolve_trash(self, qs):
        """?item=<itemId> → (item, abs_path, code, msg)。仅允许可预览状态。"""
        if TRASH is None:
            return None, None, 404, "回收站不可用"
        iid = (qs.get("item") or [""])[0]
        if not iid:
            return None, None, 400, "缺少 item"
        it = TRASH.item(iid)
        if not it or it["status"] not in ("trashed", "conflict", "failed", "moving"):
            return None, None, 404, "条目不存在或不可预览"
        ok, path, why = _trash_item_path(it)
        if not ok:
            log.warning("SECURITY block trash item %s: %s", iid, why)
            return None, None, 403, why
        if _is_link_like(path):
            return None, None, 403, "拒绝链接/junction"
        if not os.path.isfile(path):
            return None, None, 404, "回收站内文件不存在"
        return it, path, None, None

    def serve_trash_image(self, qs):
        """回收站缩略图 / 视频首帧（走与 /thumb 同一套磁盘缓存与抽帧）。"""
        it, fp, code, msg = self._resolve_trash(qs)
        if code:
            return self.send_err(code, msg)
        side = pick_thumb((qs.get("s") or ["320"])[0])
        lib = META["libs"].get(it["lib_id"]) or {"id": it["lib_id"], "name": ""}
        try:
            st = os.stat(fp)
            data, mime, hit = cached_image(lib, it["orig_rel"], fp, st, "thumb",
                                           side, THUMB_QUALITY, "webp",
                                           extra=f"trash:{it['item_id']}")
        except Exception as e:
            log.error("trash thumb fail %s: %s", it["item_id"], e)
            return self.send_err(500, f"处理失败：{e}")
        return self.send_bytes(data, mime, 200,
                               extra={"X-Cache": "HIT" if hit else "MISS"},
                               cache="public, max-age=600")

    def serve_trash_media(self, qs):
        """回收站内原始文件（Range 流式），用于预览被删除的视频。"""
        it, fp, code, msg = self._resolve_trash(qs)
        if code:
            return self.send_err(code, msg)
        total = os.path.getsize(fp)
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        if rng:
            start, end, kind = parse_range(rng, total)
            if kind in ("invalid", "unsatisfiable"):
                return self.send_err(400, "bad range")
            return self._stream_file(fp, start, end, total, ctype, 206)
        return self._stream_file(fp, 0, total - 1, total, ctype, 200)

    # ---------- API 实现 ----------
    def api_tree(self, qs):
        lib = get_lib(qs)
        if not lib:
            return self.send_err(404, "library not found")
        return self.send_json({
            "lib": lib_public(lib),
            "months": [{"ym": m["ym"], "n": m["n"],
                        "days": [{"d": d["d"], "n": d["n"]} for d in m["days"]]}
                       for m in lib["months"]],
            "dayOrder": lib["day_order"],
        })

    def api_items(self, qs):
        """
        /api/items?lib=&ym=&d=&off=&lim=&sort=desc|asc
        - 指定 ym+d → 该日全量（分页）
        - 只指定 ym  → 该月全量（分页）
        - 都不指定   → 全部（分页，供搜索/全部视图）
        """
        lib = get_lib(qs)
        if not lib:
            return self.send_err(404, "library not found")
        ym = (qs.get("ym") or [""])[0]
        dd = (qs.get("d") or [""])[0]
        off = max(0, int((qs.get("off") or ["0"])[0] or 0))
        lim = min(600, max(1, int((qs.get("lim") or ["120"])[0] or 120)))
        sort = (qs.get("sort") or ["desc"])[0]

        if ym and dd:
            src = lib["by_day"].get(ym + "|" + dd, [])
        elif ym:
            src = []
            for m in lib["months"]:
                if m["ym"] == ym:
                    for d in m["days"]:
                        src.extend(lib["by_day"].get(ym + "|" + d["d"], []))
                    break
        else:
            src = lib["items"]
        if sort == "asc":
            # 仅在不分页语义下安全；分页时按切片再反转
            total = len(src)
            page = src[off:off + lim]
            page = list(reversed(page))
        else:
            total = len(src)
            page = src[off:off + lim]
        return self.send_json({"total": total, "off": off, "lim": lim,
                               "items": [self.item_public(it) for it in page]})

    def api_search(self, qs):
        lib = get_lib(qs)
        if not lib:
            return self.send_err(404, "library not found")
        kw = (qs.get("q") or [""])[0].strip().lower()
        off = max(0, int((qs.get("off") or ["0"])[0] or 0))
        lim = min(600, max(1, int((qs.get("lim") or ["120"])[0] or 120)))
        if not kw:
            return self.send_json({"total": 0, "off": off, "lim": lim, "items": []})
        hits = [it for it in lib["items"] if kw in it["n"].lower()]
        return self.send_json({"total": len(hits), "off": off, "lim": lim,
                               "items": [self.item_public(it)
                                         for it in hits[off:off + lim]]})

    def api_item(self, qs):
        lib = get_lib(qs)
        if not lib:
            return self.send_err(404, "library not found")
        rel = (qs.get("p") or [""])[0]
        for it in lib["items"]:
            if it["p"] == rel:
                return self.send_json(self.item_public(it))
        # 也可能传的是 movie 路径
        for it in lib["items"]:
            if it["mv"] == rel:
                return self.send_json(self.item_public(it))
        return self.send_err(404, "item not found")

    def api_stats(self):
        # 走「剪枝扫描」（只进清单已知有文件的目录），避免为统计去翻 5 万个空目录
        files = _scan_cache(full=False)[0]
        cache_files = len(files)
        cache_bytes = sum(files.values())
        s = dict(_stats)
        s.update({"cacheFiles": cache_files,
                  "cacheMB": round(cache_bytes / 1024 / 1024, 1),
                  "workers": _GEN_WORKERS,
                  "ffmpeg": bool(FFMPEG), "heif": HEIF_OK,
                  "index": (INDEX.stats() if INDEX else {}),
                  "recheckSeconds": META["scan"].get("recheckSeconds", 0)})
        return self.send_json(s)

    def api_index_progress(self):
        """
        索引进度（只读，匿名可访问 —— 不含任何管理信息）。

        约定：total 为 null 表示**文件总数尚未统计出来**，
        前端此时不得显示百分比，只能显示「正在扫描图库，统计文件数量……」。
        """
        p = idx_get()
        libs = []
        for lid in list(META["lib_order"]):
            e = META["libs"].get(lid)
            if not e:
                continue
            libs.append({
                "id": lid, "name": e.get("name") or lid,
                "count": int(e.get("count") or 0), "live": int(e.get("live") or 0),
                "exists": bool(e.get("exists", True)), "stale": bool(e.get("stale")),
                "added": int(e.get("added") or 0),
                "changed": int(e.get("changed") or 0),
                "removed": int(e.get("removed") or 0),
                "scanMs": int(e.get("scan_ms") or 0),
            })
        p["libraries"] = libs
        # ★ 索引进行中，META["libs"] 正处于「被清空 → 逐库重建」的中间态：
        #   此刻实时算出来的汇总要么全 0、要么只统计了刚扫完的一两个库 —— **那不是结论**。
        #   所以进行中只回传上一次索引收尾时定格的汇总（可能根本没有，前端显示「—」）；
        #   只有 running=False（本次已收尾）才用实时汇总。
        if not p.get("running"):
            p["summary"] = idx_summary()
        p["recheckSeconds"] = int(META["scan"].get("recheckSeconds") or 0)
        p["ready"] = BOOTSTRAP_DONE.is_set()
        return self.send_json(p)

    def api_index(self):
        """索引状态：SQLite 体积、行数、各库最近一次扫描的增量统计。"""
        out = {"db": (INDEX.stats() if INDEX else {}),
               "scan": META["scan"],
               "libraries": []}
        for lid in META["lib_order"]:
            e = META["libs"][lid]
            m = INDEX.lib_meta(lid) or {} if INDEX else {}
            out["libraries"].append({
                "id": lid, "name": e["name"], "root": e["root"],
                "exists": e["exists"], "stale": bool(e.get("stale")),
                "count": e["count"], "live": e["live"],
                "scanned": m.get("scanned"), "added": m.get("added"),
                "changed": m.get("changed"), "removed": m.get("removed"),
                "movParsed": m.get("mov_parsed"),
                "lastScanMs": m.get("last_scan_ms"),
                "lastScanAt": m.get("last_scan_at"),
            })
        return self.send_json(out)

    def api_cache(self):
        """
        缓存现状（口径统一）：
          total = cache/ 全量（图片缓存 + 转码产物）
          image = 图片缓存（缩略图 + 大图，分片目录）
          trans = 转码产物（cache/trans/）
        ★ 这里**只做统计，不做孤儿判定**：判定要对每条清单逐源文件 stat，
          在大图库 + 杀软实时扫描的机器上会拖到几十秒。孤儿预览由
          /api/cache/gc 专门负责（用户在按钮上显式触发）。
        """
        files = _scan_cache(full=False)[0]
        img = {"files": 0, "bytes": 0}
        tr = {"files": 0, "bytes": 0}
        for p, s in files.items():
            rel = os.path.relpath(p, CACHE_DIR).replace("\\", "/")
            d = tr if rel.startswith("trans/") else img
            d["files"] += 1
            d["bytes"] += s
        return self.send_json({
            "cacheDir": CACHE_DIR,
            "total": {"files": img["files"] + tr["files"],
                      "bytes": img["bytes"] + tr["bytes"]},
            "image": img,
            "trans": tr,
            "manifest": (CACHE_INDEX.stats() if CACHE_INDEX else {}),
            "emptyDirs": _empty_dir_count(),
            "fullScanDone": _full_scan_done(),
        })

    def api_cache_gc(self, qs):
        """
        缓存回收。默认**只预览**（dry），必须显式 ?apply=1 才真正删除。
          ?apply=1            执行清理
          ?cls=a,b            只处理指定类别（tmp_leak/unrecorded/missing_source/changed_source）
          ?adopt=1            先收养既有缓存再判定
          ?full=1             深度全量扫描（能发现清单外、目录又没被动过的历史遗留文件；慢）
        删除范围严格限制在 cache/ 目录内，绝不触碰源照片。
        """
        if (qs.get("adopt") or ["0"])[0] == "1":
            cache_adopt()
        raw = (qs.get("cls") or [""])[0].strip()
        classes = [c for c in raw.split(",") if c in _GC_CLASSES] or None
        deep = True if (qs.get("full") or ["0"])[0] == "1" else None
        if (qs.get("apply") or ["0"])[0] == "1":
            res = cache_gc_apply(classes, deep)
            log.info("cache gc: 删除 %s 个文件 / %.2f MB（类别 %s%s）",
                     res["deleted"], res["bytes"] / 1048576,
                     classes or "全部", "，深度扫描" if deep else "")
            return self.send_json(res)
        return self.send_json(cache_gc_plan(classes, True, deep))

    def api_refresh(self, qs):
        """手动刷新索引。/api/refresh?lib=<id|all>&full=0|1"""
        lid = (qs.get("lib") or ["all"])[0]
        full = (qs.get("full") or ["0"])[0] == "1"
        targets = META["lib_order"] if lid in ("all", "") else [lid]
        res = []
        for t in targets:
            try:
                st = refresh_library(t, full=full)
                if st:
                    res.append(st)
            except Exception as e:
                log.error("refresh %s failed: %s", t, e)
                return self.send_err(500, f"刷新失败: {e}")
        return self.send_json({"ok": True, "full": full, "libraries": res,
                               "index": (INDEX.stats() if INDEX else {})})

    def item_public(self, it):
        return {"p": it["p"], "n": it["n"], "t": it["t"], "z": it["z"],
                "k": it["k"], "live": it["lv"] == 1, "pair": it["pr"],
                "mv": it["mv"], "c": it.get("c") or ""}

    # ---------- 媒体实现 ----------
    def _resolve(self, qs, key="p"):
        lib = get_lib(qs)
        if not lib:
            return None, None, None, 404, "library not found"
        rel = (qs.get(key) or [""])[0]
        try:
            fp = safe_resolve(lib["root"], rel)
        except SecurityError as e:
            log.warning("SECURITY block: %r (%s)", rel, e)
            return lib, rel, None, 403, f"路径被拒绝: {e}"
        except FileNotFoundError:
            return lib, rel, None, 404, "文件不存在"
        return lib, rel, fp, None, None

    def serve_image(self, qs, kind):
        lib, rel, fp, code, msg = self._resolve(qs)
        if code:
            return self.send_err(code, msg)
        fmt = (qs.get("fmt") or ["webp"])[0]
        if fmt not in ("webp", "jpeg"):
            fmt = "webp"
        if kind == "thumb":
            side, quality = pick_thumb((qs.get("s") or ["320"])[0]), THUMB_QUALITY
            fmt = "webp"                       # 缩略图永远用 webp
        else:
            side, quality = PHOTO_MAX, PHOTO_QUALITY
        try:
            st = os.stat(fp)
            data, mime, hit = cached_image(lib, rel, fp, st, kind,
                                           side, quality, fmt)
        except Exception as e:
            log.error("gen fail %s: %s", rel, e)
            return self.send_err(500, f"处理失败: {e}")
        return self.send_bytes(data, mime, 200,
                               extra={"X-Cache": "HIT" if hit else "MISS",
                                      "Vary": "Accept"},
                               cache="public, max-age=604800")

    def serve_raw(self, qs):
        lib, rel, fp, code, msg = self._resolve(qs)
        if code:
            return self.send_err(code, msg)
        rng = self.headers.get("Range")
        total = os.path.getsize(fp)
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if rng:
            start, end, kind = parse_range(rng, total)
            if kind in ("invalid", "unsatisfiable"):
                return self.send_err(400, "bad range")
            return self._stream_file(fp, start, end, total, ctype, 206)
        return self._stream_file(fp, 0, total - 1, total, ctype, 200)

    def serve_video(self, qs):
        """MOV/MP4，Range 流式；t=1 时 HEVC→H.264 转码"""
        lib, rel, fp, code, msg = self._resolve(qs)
        if code:
            return self.send_err(code, msg)
        trans = (qs.get("t") or ["0"])[0] == "1"
        extra = {}
        if trans:
            if not FFMPEG:
                return self.send_err(503, "服务端未找到 ffmpeg")
            mb = os.path.getsize(fp) / 1024 / 1024
            if mb > MAX_TRANSCODE_MB:
                # 整文件转码会让首字节等上几分钟；明确拒绝而不是把连接挂死
                return self.send_err(
                    415, f"视频 {mb:.0f}MB 超过在线转码上限 "
                         f"({MAX_TRANSCODE_MB}MB)，请下载后本地播放")
            out, hit = build_transcode(fp, lib_id=lib["id"], rel=rel)
            if not out:
                return self.send_err(500, "转码失败")
            fp = out
            extra["X-Transcode"] = "HIT" if hit else "MISS"

        total = os.path.getsize(fp)
        ctype = mimetypes.guess_type(fp)[0] or "video/quicktime"
        rng = self.headers.get("Range")
        start, end, kind = parse_range(rng, total)
        if kind == "unsatisfiable":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if kind == "invalid":
            return self.send_err(400, "malformed Range")
        return self._stream_file(fp, start, end, total, ctype,
                                 206 if kind == "ok" else 200,
                                 tq=(qs.get("q") or [""])[0], extra=extra)

    def _stream_file(self, fp, start, end, total, ctype, code, tq="", extra=None):
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "public, max-age=3600")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(fp, "rb") as f:
            f.seek(start)
            remain = length
            while remain > 0:
                chunk = f.read(min(VIDEO_CHUNK, remain))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remain -= len(chunk)

    # ---------- 静态文件 ----------
    def serve_static(self, name, extra=None):
        fp = os.path.join(WEB_DIR, name)
        if not os.path.isfile(fp):
            return self.send_err(404, "not found")
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if name.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif name.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif name.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(fp, "rb") as f:
            data = f.read()
        # 页面/脚本/样式：no-cache（每次都校验，避免改完前端浏览器还吃旧缓存）
        cache = ("no-cache" if name.endswith((".html", ".js", ".css"))
                 else "public, max-age=3600")
        return self.send_bytes(data, ctype, 200, extra=extra, cache=cache)


# =========================================================
#  启动
# =========================================================
def find_port(pref):
    if pref:
        return pref
    for p in range(9000, 9100):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _bg_index(full=False):
    """手动触发的索引（后台线程；前端轮询 /api/index/progress 看进度）。
    增量走 recheck_all（不重建 META，浏览不中断）；全量才重建该库索引。"""
    try:
        if full:
            load_libraries(full=True, mode="full")
        else:
            recheck_all(mode="incremental")
    except Exception as e:
        log.exception("手动索引失败")
        idx_finish(error=str(e))


def _bootstrap_async(rc, pt):
    """
    首次索引在**后台线程**里做（端口此前已开始监听）。

    这样做的唯一目的：让「首次索引」也能在页面上看到真实进度，而不是
    让页面空等。索引期间 /api/libs 会返回 ready=false / indexing=true，
    前端据此显示进度面板。
    """
    t0 = time.time()
    try:
        load_libraries(mode="startup")
    except Exception as e:
        log.exception("启动索引失败")
        idx_finish(error=str(e))
        print(f"[错误] 启动索引失败：{e}")
        return
    finally:
        BOOTSTRAP_DONE.set()          # 无论成败都放行客户端，避免永久卡在「就绪前」

    total = sum(l["count"] for l in META["libs"].values())
    allc = sum(l.get("movParsed", 0) for l in META["libs"].values())
    print(f"[索引] 合计 {total:,} 项 ｜ 启动核对 {(time.time() - t0):.2f}s ｜ "
          f"本次重解析 MOV {allc} 个")
    if INDEX:
        si = INDEX.stats()
        print(f"[索引] {si['rows']:,} 行 / {si['pairs']:,} 组配对 / "
              f"db {si['dbMB']} MB ｜ {si['dbPath']}")
    if CACHE_INDEX:
        adopted = cache_adopt_if_needed()
        cs = CACHE_INDEX.stats()
        note = f"（本次新收养 {adopted:,} 条既有缓存）" if adopted else ""
        print(f"[缓存] 清单 {cs['entries']:,} 条 / db {cs['dbMB']} MB{note}")
    if TRASH:
        # 启动到期检查：停机期间错过的到期项在本次启动补做（绝不静默跳过）
        try:
            pr = purge_expired()
            if pr.get("purged") or pr.get("failed"):
                print(f"[回收站] 启动到期检查：永久删除 {pr['purged']} 项 / "
                      f"失败 {pr['failed']} 项")
        except Exception as e:
            log.error("启动到期检查失败：%s", e)
        ts = TRASH.stats()
        ok, why = _trash_safe()
        print(f"[回收站] {ts['items']:,} 项 / {ts['bytes'] / 1048576:.1f} MB ｜ "
              f"到期 {ts['expired']} ｜ {'✅ 与图库互不包含' if ok else '⛔ ' + why}")

    if rc > 0:
        threading.Thread(target=recheck_loop, name="recheck", daemon=True).start()
    if TRASH and pt > 0:
        threading.Thread(target=purge_loop, name="purge", daemon=True).start()


def main():
    global FFMPEG, INDEX, CACHE_INDEX, TRASH
    port, token, lan, init_only = None, None, False, False
    rebuild, recheck = False, None
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--stop":
            return stop_server()
        elif a == "--init":
            init_only = True; i += 1
        elif a == "--rebuild-index":
            rebuild = True; i += 1
        elif a == "--recheck" and i + 1 < len(sys.argv):
            recheck = int(sys.argv[i + 1]); i += 2
        elif a == "--no-recheck":
            recheck = 0; i += 1
        elif a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1]); i += 2
        elif a == "--token" and i + 1 < len(sys.argv):
            token = sys.argv[i + 1]; i += 2
        elif a == "--admin-token" and i + 1 < len(sys.argv):
            token = sys.argv[i + 1]; i += 2
        elif a == "--lan":
            lan = True; i += 1
        else:
            i += 1

    if recheck is not None:
        os.environ["SEEKLIVE_RECHECK"] = str(recheck)

    if init_only:
        if os.path.isfile(CONFIG_PATH):
            print(f"配置文件已存在：{CONFIG_PATH}")
        else:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"scan": DEFAULT_SCAN, "libraries": DEFAULT_LIBS}, f,
                          ensure_ascii=False, indent=2)
            print(f"已生成配置文件：{CONFIG_PATH}")
        return 0

    if rebuild:
        FFMPEG = detect_ffmpeg()
        print("=" * 62)
        print("  重建索引（丢弃 data/index.db 中的既有条目）")
        print("=" * 62)
        load_libraries(full=True)
        total = sum(l["count"] for l in META["libs"].values())
        allc = sum(l.get("movParsed", 0) for l in META["libs"].values())
        print(f"[完成] 合计 {total:,} 项 ｜ 重解析 MOV {allc} 个")
        if INDEX:
            print(f"[索引] {INDEX.stats()}")
            INDEX.close()
        if CACHE_INDEX:
            cache_adopt_if_needed()
            print(f"[缓存] 清单 {CACHE_INDEX.stats()}")
            CACHE_INDEX.close()
        if TRASH:
            print(f"[回收站] {TRASH.stats()}")
            TRASH.close()
        return 0

    old_pid, old_port = check_already_running(port)
    if old_pid:
        print("=" * 62)
        print("  [!] 服务已在运行，本次启动已取消")
        print(f"      进程 PID = {old_pid}   端口 = {old_port}")
        print(f"      访问 http://127.0.0.1:{old_port}/")
        print("      如需重启：先运行「一键结束.bat」")
        print("=" * 62)
        return 2

    FFMPEG = detect_ffmpeg()
    # 管理口令必须在任何打印之前定好（打印只反映状态，不回显口令本体）
    set_admin_config(token)

    print("=" * 62)
    print("  SeekLive · 时光轴（正式版 Demo）")
    print("=" * 62)

    # 索引 / 缓存清单 / 回收站连接先同步打开（很快），
    # 保证「后台索引进行中」时到达的请求也能正常读到索引，不会 500。
    _open_stores()

    port = find_port(port)
    host = "0.0.0.0" if lan else "127.0.0.1"
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        print(f"  [错误] 端口 {port} 绑定失败: {e}")
        return 3
    write_pid(port)

    rc = int(META["scan"].get("recheckSeconds") or 0)
    pt = int((META.get("trash") or DEFAULT_TRASH).get("purgeIntervalSeconds") or 0)

    print(f"[能力] HEIC {'✅' if HEIF_OK else '❌'} ｜ "
          f"ffmpeg {'✅' if FFMPEG else '❌'} ｜ "
          f"WebP ✅ ｜ 生成并发 {_GEN_WORKERS}")
    print(f"[核对] 后台定期核对 {'每 ' + str(rc) + ' 秒' if rc > 0 else '已关闭'}")
    print(f"[回收站] 到期检查 {'每 ' + str(pt) + ' 秒' if pt > 0 else '仅启动时'}"
          f"（保留 {(META.get('trash') or DEFAULT_TRASH).get('retentionDays')} 天）")
    print()
    print(f"  本机访问 : http://127.0.0.1:{port}/")
    if lan:
        ip = lan_ip()
        if ip:
            print(f"  手机访问 : http://{ip}:{port}/")
    if admin_required():
        print(f"  源文件管理 : 已启用（口令来源：{ADMIN_SOURCE}；"
              f"会话 {ADMIN_CFG.get('sessionHours')} 小时）")
        print("              浏览免登录；删除/恢复/永久删除时才会要求授权。")
    else:
        print("  源文件管理 : 未配置 → 删除功能关闭（浏览不受影响）")
        print("              如需开启：python server.py --admin-token 你的口令")
    print("=" * 62)
    print("  Ctrl+C 停止，或双击「一键结束.bat」")
    print("  首次索引在后台进行：页面可立即打开，并显示索引进度。")

    # ★ 首次索引放到后台线程（端口已开始监听）：页面无需空等，
    #   前端定时轮询 /api/index/progress，不挂着一个请求等索引结束。
    threading.Thread(target=_bootstrap_async, args=(rc, pt),
                     name="bootstrap", daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        _recheck_stop.set()
        _purge_stop.set()
        httpd.server_close()
        if INDEX:
            INDEX.close()
        if CACHE_INDEX:
            CACHE_INDEX.close()
        if TRASH:
            TRASH.close()
        clear_pid()
    return 0


if __name__ == "__main__":
    sys.exit(main())
