#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · 访问控制 / 免登录分享 —— 服务端权限矩阵自测
======================================================

设计要点（为什么是「进程内 hermetic」而不是起真实服务）
------------------------------------------------------
`server.py` 的所有判定都读模块级全局（META / ACCESS_* / SHARES / ADMIN_PW…），
所以本测试**直接 import server 并临时改写这些全局**，用临时目录当图库根、
用临时 shares.db 当分享账本，然后用真的 `ThreadingHTTPServer` + `Handler` 跑一遍：

  · 不碰用户的真实图库目录、不重建真实索引、不写真实 data/shares.db；
  · 不起子进程、不等索引，秒级完成；
  · 覆盖的是**真实的请求处理链路**（read_guard / media_guard / 各 api_*）；
  · `Cache-Control` 也用真实响应头断言（share 必须 private, no-store）。

覆盖验收 1–10 条 + 管理员兼容性（C5）。

运行：  python tests/test_access.py
退出码 0 = 全绿。
"""

import os
import sys
import json
import time
import shutil
import tempfile
import threading
import urllib.parse
import urllib.request
import http.client

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
if APP not in sys.path:
    sys.path.insert(0, APP)

import server            # noqa: E402
import share_db          # noqa: E402
import index_db          # noqa: E402
import cache_db          # noqa: E402

PASS, FAIL = [], []


def chk(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f"   [{detail}]" if detail else ""))


def sect(t):
    print(f"\n=== {t} ===")


# ---------------- 请求辅助（绕开本机 HTTP 代理；不自动跟随 3xx） ----------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """分享落地页是 302，必须能拿到原始响应（Set-Cookie / Location），不能被自动跟随。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                      _NoRedirect())


def req(base, path, method="GET", body=None, headers=None, cookies=None):
    url = base + path
    h = dict(headers or {})
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with _OPENER.open(r, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def jbody(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def set_cookie(headers, name):
    """从响应头里取某个 Set-Cookie 的值（只取 name=value 段）。"""
    sc = headers.get("Set-Cookie") or ""
    for part in sc.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:]
    return ""


# ---------------- 造一个假图库（结构与 index_db.build_index 一致） ----------------
def make_jpeg(path, w=64, h=48):
    from PIL import Image
    Image.new("RGB", (w, h), (120, 160, 200)).save(path, "JPEG")


def bucket(items):
    import collections
    by_day, day_order = collections.defaultdict(list), []
    for it in items:
        lt = time.localtime(it["t"])
        key = time.strftime("%Y-%m", lt) + "|" + time.strftime("%d", lt)
        if key not in by_day:
            day_order.append(key)
        by_day[key].append(it)
    months = collections.OrderedDict()
    for key in day_order:
        ym, dd = key.split("|")
        m = months.setdefault(ym, {"ym": ym, "n": 0, "days": []})
        m["n"] += len(by_day[key])
        m["days"].append({"d": dd, "n": len(by_day[key]),
                          "ts": by_day[key][0]["t"]})
    return dict(by_day), day_order, list(months.values())


def build_lib(lid, name, root, specs):
    """specs: [(rel, ts, kind='img'|'vid', live=0, mov=None)]"""
    def write_blob(p, kind):
        if os.path.isfile(p):
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if kind == "img":
            make_jpeg(p)
        else:
            with open(p, "wb") as f:      # 内容无关紧要，整段流式返回
                f.write(b"\x00" * 4096)

    items = []
    for rel, ts, kind, live, mov in specs:
        p = os.path.join(root, rel.replace("/", os.sep))
        write_blob(p, kind)
        if mov:                            # Live Photo 的配对视频也要真实存在
            write_blob(os.path.join(root, mov.replace("/", os.sep)), "vid")
        size = os.path.getsize(p)
        items.append({"p": rel, "n": os.path.basename(rel), "t": int(ts),
                      "z": size, "k": kind, "lv": int(live),
                      "pr": "mov" if live else "none",
                      "mv": (mov or rel) if kind == "vid" else mov,
                      "c": ""})
    items.sort(key=lambda x: (-x["t"], x["p"]))
    by_day, day_order, months = bucket(items)
    live = sum(1 for i in items if i["lv"] == 1)
    return {"id": lid, "name": name, "root": root, "cfg": {}, "stale": False,
            "exists": True, "readonly": True, "deletion_enabled": False,
            "count": len(items), "live": live, "scan_ms": 0, "movParsed": 0,
            "items": items, "by_day": by_day, "day_order": day_order,
            "months": months}


def start_server(port=0):
    httpd = server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def setup_fixture(tmp, token="test-pw-123"):
    """
    把 server 的模块级全局换成一套**临时、隔离**的夹具：
    临时图库（两个库、6+1 张假照片，含一组 Live Photo）+ 临时 shares.db
    + 打开访问保护并配口令。

    `tests/fixture_server.py` 也复用本函数起一个真实 HTTP 服务，
    供浏览器 E2E（tests/e2e/qa_share.js）使用。
    """
    libA = os.path.join(tmp, "libA")
    libB = os.path.join(tmp, "libB")
    server.CACHE_DIR = os.path.join(tmp, "cache")
    server.INDEX = index_db.Index(os.path.join(tmp, "index.db"))
    server.CACHE_INDEX = cache_db.CacheIndex(os.path.join(tmp, "cache.db"))
    server.TRASH = None

    now = int(time.time())
    day = 86400

    def ts(y, mo, d, hh=10):
        """固定日期 → 时间戳（用固定日期而不是 now±day，才能造出**多个不同月份**）。"""
        return int(time.mktime((y, mo, d, hh, 0, 0, 0, 0, -1)))

    specsA = [
        ("2024-09/IMG_0001.JPG", ts(2024, 9, 1), "img", 0, None),
        ("2024-09/IMG_0002.HEIC", ts(2024, 9, 2), "img", 1, "2024-09/IMG_0002.MOV"),
        ("2024-09/IMG_0003.JPG", ts(2024, 9, 3), "img", 0, None),
        ("2024-10/IMG_0004.JPG", ts(2024, 10, 1), "img", 0, None),
        ("2024-10/IMG_0005.JPG", ts(2024, 10, 2), "img", 0, None),
        ("2024-10/IMG_0006.MOV", ts(2024, 10, 3), "vid", 0, None),
        # 单独留一个「一天多张」的日子（放在**最老的月份**，不影响上面两个
        # 各含 3 张的月份）—— 供 E2E 验证每天选择框的「半选」状态。
        ("2024-08/IMG_0007.JPG", ts(2024, 8, 30), "img", 0, None),
        ("2024-08/IMG_0008.JPG", ts(2024, 8, 30), "img", 0, None),
    ]
    specsB = [("x/IMG_9001.JPG", ts(2024, 8, 1), "img", 0, None)]
    server.META["libs"] = {
        "t": build_lib("t", "测试库", libA, specsA),
        "u": build_lib("u", "另一个库", libB, specsB),
    }
    server.META["lib_order"] = ["t", "u"]
    server.META["trash"] = dict(server.DEFAULT_TRASH)
    server.META["scan"] = dict(server.DEFAULT_SCAN)

    server.SHARES = share_db.ShareStore(os.path.join(tmp, "shares.db"))

    # ---- 访问控制：开保护 + 配口令 ----
    server.ADMIN_PW = token
    server.ADMIN_SOURCE = "测试"
    server.ADMIN_CFG["sessionHours"] = 12
    server.ACCESS_CFG = dict(server.ACCESS_CFG)
    server.ACCESS_CFG.update({"protectRead": True, "shareEnabled": True,
                              "cookieSecure": "auto", "pepper": "",
                              "defaultExpireDays": 0, "maxItems": 5000})
    server.ACCESS_PROTECT = True
    # 夹具不走「启动索引」，直接标记就绪 —— 否则 /api/libs 的 ready=false
    # 会让前端进入「索引进度面板」而不是时间轴。
    server.BOOTSTRAP_DONE.set()
    return tmp


def main():
    tmp = tempfile.mkdtemp(prefix="seeklive_access_")
    print("=" * 66)
    print("  SeekLive · 访问控制矩阵自测（hermetic，不碰真实图库）")
    print("=" * 66)

    setup_fixture(tmp)

    httpd, base = start_server()
    try:
        run_matrix(base)
    finally:
        httpd.shutdown()
        try:
            server.SHARES.close()
        except Exception:
            pass
        for store in (server.INDEX, server.CACHE_INDEX):
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
            print("   ✗", f)
    print("=" * 66)
    return 1 if FAIL else 0


def run_matrix(base):
    anon = None
    R = ("2024-09/IMG_0001.JPG", "2024-09/IMG_0002.HEIC", "2024-09/IMG_0003.JPG")
    MOV2 = "2024-09/IMG_0002.MOV"

    # =========================================================
    sect("组 1 · 匿名：静态外壳可读，数据/媒体一律 401")
    # =========================================================
    for p in ("/", "/index.html", "/app.css", "/app.js", "/favicon.svg",
              "/manifest.webmanifest"):
        st, _, _ = req(base, p)
        chk(f"匿名 {p} → 200（静态外壳匿名）", st == 200, f"HTTP {st}")

    st, _, raw = req(base, "/api/session")
    d = jbody(raw)
    chk("匿名 /api/session → 200 且 scope=anon",
        st == 200 and d.get("scope") == "anon", f"HTTP {st} {d.get('scope')}")
    chk("匿名 /api/session 不回传任何相册数据",
        not d.get("share") and d.get("canManage") is False, f"{d}")

    for p in ("/api/libs", "/api/tree?lib=t", "/api/items?lib=t",
              "/api/search?lib=t&q=IMG", "/api/item?lib=t&p=" + urllib.parse.quote(R[0]),
              "/api/stats", "/api/index", "/api/index/progress", "/api/cache",
              "/api/trash", "/api/trash/stats"):
        st, _, _ = req(base, p)
        chk(f"匿名 GET {p.split('?')[0]} → 401", st == 401, f"HTTP {st}")

    for p, hd in (("/thumb?lib=t&p=" + urllib.parse.quote(R[0]) + "&s=160", {}),
                  ("/photo?lib=t&p=" + urllib.parse.quote(R[0]), {}),
                  ("/raw?lib=t&p=" + urllib.parse.quote(R[0]), {}),
                  ("/video?lib=t&p=" + urllib.parse.quote(MOV2), {"Range": "bytes=0-99"})):
        st, _, _ = req(base, p, headers=hd)
        chk(f"匿名 {p.split('?')[0]} → 401", st == 401, f"HTTP {st}")

    # HEAD 不能绕过（验收 9）
    st, _, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote(R[0]), method="HEAD")
    chk("匿名 HEAD /raw → 401（与 GET 同约束）", st == 401, f"HTTP {st}")
    st, _, _ = req(base, "/api/tree?lib=t", method="HEAD")
    chk("匿名 HEAD /api/tree → 401", st == 401, f"HTTP {st}")

    # GET 副作用不能被匿名触发（验收 8）
    st, _, _ = req(base, "/api/refresh?lib=t")
    chk("匿名 GET /api/refresh → 401/403（不再匿名可调）", st in (401, 403), f"HTTP {st}")
    st, _, _ = req(base, "/api/cache/gc?apply=1")
    chk("匿名 GET /api/cache/gc?apply=1 → 401/403", st in (401, 403), f"HTTP {st}")

    # =========================================================
    sect("组 2 · 管理员：登录后原功能全绿（兼容性 C5）")
    # =========================================================
    st, hd, raw = req(base, "/api/admin/login", "POST", {"token": "wrong-pw"},
                      {"X-SL-Admin": "1"})
    chk("错误口令 → 401", st == 401, f"HTTP {st}")
    st, hd, raw = req(base, "/api/admin/login", "POST", {"token": "test-pw-123"},
                      {"X-SL-Admin": "1"})
    sid = set_cookie(hd, server.SESSION_COOKIE)
    chk("正确口令 → 200 且下发 sl_admin", st == 200 and bool(sid), f"HTTP {st}")
    ck_admin = {server.SESSION_COOKIE: sid}

    st, _, raw = req(base, "/api/session", cookies=ck_admin)
    chk("管理员 /api/session → scope=admin & canManage",
        st == 200 and jbody(raw).get("scope") == "admin"
        and jbody(raw).get("canManage") is True, f"{jbody(raw).get('scope')}")

    allok = True
    detail = []
    for p in ("/api/libs", "/api/tree?lib=t", "/api/items?lib=t",
              "/api/search?lib=t&q=IMG",
              "/api/item?lib=t&p=" + urllib.parse.quote(R[0]),
              "/api/stats", "/api/index", "/api/index/progress", "/api/cache",
              "/api/trash", "/api/trash/stats"):
        st, _, _ = req(base, p, cookies=ck_admin)
        detail.append(f"{p.split('?')[0]}:{st}")
        allok = allok and st == 200
    chk("管理员：全部只读接口 → 200", allok, " ".join(detail))

    st, hd, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote(R[0]), cookies=ck_admin)
    chk("管理员 /raw → 200", st == 200, f"HTTP {st}")
    st, _, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote(R[0]),
                   headers={"Range": "bytes=0-9"}, cookies=ck_admin)
    chk("管理员 /raw Range → 206", st == 206, f"HTTP {st}")
    st, hd, _ = req(base, "/thumb?lib=t&p=" + urllib.parse.quote(R[0]) + "&s=160",
                    cookies=ck_admin)
    chk("管理员 /thumb → 200", st == 200, f"HTTP {st}")
    chk("管理员媒体响应头仍是 public（不改缓存策略）",
        "public" in (hd.get("Cache-Control") or ""), hd.get("Cache-Control") or "")
    st, _, _ = req(base, "/video?lib=t&p=" + urllib.parse.quote(MOV2),
                   headers={"Range": "bytes=0-99"}, cookies=ck_admin)
    chk("管理员 /video Range → 206", st == 206, f"HTTP {st}")

    # =========================================================
    sect("组 3 · 分享：落地 → Cookie → 仅授权集合")
    # =========================================================
    st, _, raw = req(base, "/api/share/create", "POST",
                     {"lib": "t", "rels": [R[0], R[1], R[2]], "title": "九月精选",
                      "kind": "month"}, {"X-SL-Admin": "1"}, ck_admin)
    d = jbody(raw)
    token = d.get("token") or ""
    chk("管理员创建分享 → 200 且返回令牌/链接",
        st == 200 and bool(token) and d.get("url") == f"/s/{token}",
        f"HTTP {st} {d.get('error','')}")
    chk("分享元信息：数量=3、标题正确",
        (d.get("share") or {}).get("count") == 3
        and (d.get("share") or {}).get("title") == "九月精选", f"{d.get('share')}")

    st, _, raw = req(base, "/api/share/create", "POST",
                     {"lib": "t", "rels": [R[0]]}, {"X-SL-Admin": "1"})
    chk("匿名/无会话创建分享 → 401 + needAuth",
        st == 401 and jbody(raw).get("needAuth") is True, f"HTTP {st}")

    st, hd, _ = req(base, f"/s/{token}")
    share_ck = set_cookie(hd, server.SHARE_COOKIE)
    sc = hd.get("Set-Cookie") or ""
    chk("分享落地 /s/<token> → 302", st == 302, f"HTTP {st}")
    chk("302 带 Set-Cookie: sl_share", bool(share_ck), sc[:60])
    chk("Cookie 含 Path=/（否则媒体带不上）", "Path=/" in sc, sc[:80])
    chk("Cookie 含 HttpOnly + SameSite=Lax",
        "HttpOnly" in sc and "SameSite=Lax" in sc, sc[:80])
    chk("HTTP（无 X-Forwarded-Proto）下不加 Secure",
        "Secure" not in sc, sc[:80])
    chk("302 未把令牌写进 Location（不回显 URL）",
        (hd.get("Location") or "") == "/?share=1", hd.get("Location") or "")

    # Secure 自适应：带 X-Forwarded-Proto: https 时必须加 Secure
    st, hd2, _ = req(base, f"/s/{token}", headers={"X-Forwarded-Proto": "https"})
    chk("经反向代理（X-Forwarded-Proto: https）→ Cookie 加 Secure",
        "Secure" in (hd2.get("Set-Cookie") or ""), (hd2.get("Set-Cookie") or "")[:80])

    ck_share = {server.SHARE_COOKIE: share_ck}
    st, _, raw = req(base, "/api/session", cookies=ck_share)
    d = jbody(raw)
    chk("分享访客 /api/session → scope=share + 标题/数量",
        st == 200 and d.get("scope") == "share"
        and (d.get("share") or {}).get("count") == 3
        and d.get("canManage") is False, f"{d}")

    st, _, raw = req(base, "/api/libs", cookies=ck_share)
    d = jbody(raw)
    libs = d.get("libraries") or []
    chk("分享访客 /api/libs → 只有一个库、count=授权数",
        st == 200 and len(libs) == 1 and libs[0]["count"] == 3, f"{d}")

    st, _, raw = req(base, "/api/tree?lib=t", cookies=ck_share)
    d = jbody(raw)
    dayn = sum(dd["n"] for m in (d.get("months") or []) for dd in m["days"])
    chk("分享访客 /api/tree → 只含授权条目（3）", st == 200 and dayn == 3, f"{dayn}")

    st, _, raw = req(base, "/api/items?lib=t", cookies=ck_share)
    d = jbody(raw)
    rels = {it["p"] for it in (d.get("items") or [])}
    chk("分享访客 /api/items → total=3 且不含集合外条目",
        st == 200 and d.get("total") == 3 and rels == set(R),
        f"total={d.get('total')} rels={sorted(rels)}")

    st, _, raw = req(base, "/api/search?lib=t&q=IMG_0006", cookies=ck_share)
    chk("分享访客搜索集合外文件 → 0 条",
        st == 200 and jbody(raw).get("total") == 0, f"{jbody(raw).get('total')}")

    st, _, raw = req(base, "/api/item?lib=t&p=" + urllib.parse.quote("2024-10/IMG_0004.JPG"),
                     cookies=ck_share)
    chk("分享访客取集合外条目 → 403（有身份、无权限）", st == 403, f"HTTP {st}")

    st, hd, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote(R[0]), cookies=ck_share)
    chk("分享访客取授权原图 → 200", st == 200, f"HTTP {st}")
    chk("分享媒体响应头 private, no-store（撤销要即时生效）",
        "private" in (hd.get("Cache-Control") or "")
        and "no-store" in (hd.get("Cache-Control") or ""),
        hd.get("Cache-Control") or "")

    st, _, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote("2024-10/IMG_0004.JPG"),
                   cookies=ck_share)
    chk("分享访客取集合外原图 → 403", st == 403, f"HTTP {st}")
    st, _, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote(MOV2), cookies=ck_share)
    chk("分享访客取**授权 Live 的配对视频** → 200", st == 200, f"HTTP {st}")
    st, _, _ = req(base, "/video?lib=t&p=" + urllib.parse.quote(MOV2),
                   headers={"Range": "bytes=0-99"}, cookies=ck_share)
    chk("分享访客 /video（配对视频） → 206", st == 206, f"HTTP {st}")
    st, _, _ = req(base, "/thumb?lib=t&p=" + urllib.parse.quote(R[0]) + "&s=160",
                   cookies=ck_share)
    chk("分享访客 /thumb 授权条目 → 非 401/403", st not in (401, 403), f"HTTP {st}")

    # 越权：改库 / 改路径（验收 4）
    st, _, _ = req(base, "/api/tree?lib=u", cookies=ck_share)
    chk("分享访客改 lib 到别的库 → 403", st == 403, f"HTTP {st}")
    st, _, _ = req(base, "/raw?lib=u&p=" + urllib.parse.quote("x/IMG_9001.JPG"),
                   cookies=ck_share)
    chk("分享访客改 lib 取别库媒体 → 403", st == 403, f"HTTP {st}")
    st, _, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote("../../etc/passwd"),
                   cookies=ck_share)
    chk("分享访客路径穿越 → 403", st == 403, f"HTTP {st}")
    st, _, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote("C:/Windows/win.ini"),
                   cookies=ck_share)
    chk("分享访客绝对路径 → 403", st == 403, f"HTTP {st}")

    # 回收站不向 share 暴露（验收 7）
    for p in ("/api/trash", "/api/trash/stats", "/api/trash/thumb?item=x",
              "/api/trash/media?item=x"):
        st, _, _ = req(base, p, cookies=ck_share)
        chk(f"分享访客 {p.split('?')[0]} → 403（回收站只给 admin）",
            st == 403, f"HTTP {st}")

    # admin-only 集合接口（验收 10：分享入口不使其变匿名）
    for p in ("/api/stats", "/api/index", "/api/cache", "/api/index/progress",
              "/api/refresh?lib=t", "/api/cache/gc"):
        st, _, _ = req(base, p, cookies=ck_share)
        chk(f"分享访客 {p.split('?')[0]} → 403", st == 403, f"HTTP {st}")

    # sl_share 不干扰 sl_admin（C5）
    both = dict(ck_admin)
    both.update(ck_share)
    st, _, raw = req(base, "/api/session", cookies=both)
    chk("同时持 sl_admin + sl_share → 以 admin 身份为准",
        jbody(raw).get("scope") == "admin", f"{jbody(raw).get('scope')}")
    st, _, _ = req(base, "/api/stats", cookies=both)
    chk("同时持两 Cookie → 管理员接口仍 200", st == 200, f"HTTP {st}")

    # =========================================================
    sect("组 4 · 撤销 / 过期：即时失效（验收 5）")
    # =========================================================
    st, _, raw = req(base, "/api/share/list", "POST", {"lib": "t"},
                     {"X-SL-Admin": "1"}, ck_admin)
    shares = jbody(raw).get("shares") or []
    myid = next((s["id"] for s in shares if s["title"] == "九月精选"), "")
    chk("管理员 /api/share/list → 能列出（且不回显令牌）",
        st == 200 and bool(myid) and "token" not in (shares[0] if shares else {}),
        f"HTTP {st} n={len(shares)}")

    st, _, raw = req(base, "/api/share/revoke", "POST", {"id": myid},
                     {"X-SL-Admin": "1"}, ck_admin)
    chk("管理员撤销分享 → 200", st == 200 and jbody(raw).get("revoked") == 1,
        f"HTTP {st} {jbody(raw)}")
    st, _, _ = req(base, "/api/tree?lib=t", cookies=ck_share)
    chk("撤销后：旧 Cookie 请求列表 → 401", st == 401, f"HTTP {st}")
    st, _, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote(R[0]), cookies=ck_share)
    chk("撤销后：旧 Cookie 请求媒体 → 401", st == 401, f"HTTP {st}")
    st, _, _ = req(base, f"/s/{token}")
    chk("撤销后：分享链接本身 → 404", st == 404, f"HTTP {st}")

    # 过期分享：直接经账本造一条「已过期」的（API 不接受负数天数）
    server.SHARES.create(server.share_token_hash("expired-token-xyz"), "t",
                         [{"rel": R[0], "mov_rel": None}],
                         title="已过期", expires_at=int(time.time()) - 10)
    st, hd3, _ = req(base, "/s/expired-token-xyz")
    chk("已过期分享：落地页 → 404", st == 404, f"HTTP {st}")
    st, _, _ = req(base, "/raw?lib=t&p=" + urllib.parse.quote(R[0]),
                   cookies={server.SHARE_COOKIE: "expired-token-xyz"})
    chk("已过期分享：媒体请求 → 401", st == 401, f"HTTP {st}")

    st, _, _ = req(base, "/s/definitely-not-a-real-token")
    chk("不存在的分享令牌 → 404（不区分「已撤销」，避免探测）", st == 404, f"HTTP {st}")

    # =========================================================
    sect("组 4b · keep-alive：同一连接上的后续请求必须重新判定身份")
    # =========================================================
    # HTTP/1.1 长连接会**复用同一个 Handler 实例**；若把身份判定缓存在 self 上
    # 却不在每个请求开头清掉，撤销后仍会沿用旧的 share 结论（越权放行）。
    # 本组用**同一条 TCP 连接**做「撤销前 → 撤销 → 撤销后」三连击来守住它。
    st, _, raw = req(base, "/api/share/create", "POST",
                     {"lib": "t", "rels": [R[0]], "title": "KEEPALIVE",
                      "expireDays": 7},
                     {"X-SL-Admin": "1"}, ck_admin)
    ka_tok = jbody(raw).get("token") or ""
    chk("keep-alive 前置：新建一条分享", st == 200 and bool(ka_tok), f"HTTP {st}")
    st, _, raw = req(base, "/api/share/list", "POST", {"lib": "t"},
                     {"X-SL-Admin": "1"}, ck_admin)
    ka_id = next((s["id"] for s in (jbody(raw).get("shares") or [])
                  if s.get("title") == "KEEPALIVE"), "")

    u = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=20)
    ka_hdr = {"Cookie": f"{server.SHARE_COOKIE}={ka_tok}"}

    def ka_get(path):
        conn.request("GET", path, headers=ka_hdr)
        r = conn.getresponse()
        return r.status, jbody(r.read())

    try:
        s, b = ka_get("/api/session")
        chk("keep-alive ①（撤销前）同一连接 → scope=share",
            s == 200 and b.get("scope") == "share", f"HTTP {s} {b.get('scope')}")
        s, _ = ka_get("/api/tree?lib=t")
        chk("keep-alive ②（撤销前）同一连接 → 200", s == 200, f"HTTP {s}")

        st, _, raw = req(base, "/api/share/revoke", "POST", {"id": ka_id},
                         {"X-SL-Admin": "1"}, ck_admin)
        chk("keep-alive ③ 撤销 → 200", st == 200 and jbody(raw).get("revoked") == 1,
            f"HTTP {st}")

        s, b = ka_get("/api/session")
        chk("keep-alive ④（撤销后）同一连接必须改判 anon —— 不得沿用旧结论",
            s == 200 and b.get("scope") == "anon", f"HTTP {s} {b.get('scope')}")
        s, _ = ka_get("/api/tree?lib=t")
        chk("keep-alive ⑤（撤销后）同一连接 → 401", s == 401, f"HTTP {s}")
    finally:
        conn.close()

    # =========================================================
    sect("组 3b · 分享链接的对外地址（admin.json → publicUrl）")
    # =========================================================
    # publicUrl 只影响「复制出去的链接长什么样」，**不参与任何权限判断**。
    for raw_v, want in (("  https://photos.example.com/  ", "https://photos.example.com"),
                        ("photos.example.com", "https://photos.example.com"),
                        ("http://192.168.1.9:9000/", "http://192.168.1.9:9000"),
                        ("", ""), (None, "")):
        got = server.norm_public_url(raw_v)
        chk(f"归一化 publicUrl {raw_v!r} → {want!r}", got == want, f"got {got!r}")

    old_pub = server.ADMIN_CFG.get("publicUrl")
    server.ADMIN_CFG["publicUrl"] = "https://photos.example.com"
    st, _, raw = req(base, "/api/share/create", "POST",
                     {"lib": "t", "rels": [R[0]], "title": "PUBURL"},
                     {"X-SL-Admin": "1"}, ck_admin)
    d = jbody(raw)
    tok2 = d.get("token") or ""
    chk("配了 publicUrl → fullUrl 用对外地址、url 仍是相对路径",
        st == 200 and d.get("fullUrl") == f"https://photos.example.com/s/{tok2}"
        and d.get("url") == f"/s/{tok2}", f"{d.get('fullUrl')} / {d.get('url')}")
    st, _, raw = req(base, "/api/session", cookies=ck_admin)
    chk("管理员 /api/session 带上 publicUrl（供前端提示）",
        st == 200 and jbody(raw).get("publicUrl") == "https://photos.example.com",
        f"{jbody(raw).get('publicUrl')}")

    server.ADMIN_CFG["publicUrl"] = ""
    st, _, raw = req(base, "/api/share/create", "POST",
                     {"lib": "t", "rels": [R[0]], "title": "NOPUB"},
                     {"X-SL-Admin": "1"}, ck_admin)
    chk("未配 publicUrl → fullUrl 为空串（前端用 location.origin 兜底）",
        st == 200 and jbody(raw).get("fullUrl") == "", f"{jbody(raw).get('fullUrl')!r}")
    server.ADMIN_CFG["publicUrl"] = old_pub or ""

    # 走一遍真实的「从 admin.json 读」路径（不只是归一化函数本身）
    _bak = (server.ADMIN_CFG_PATH, server.ADMIN_PW, server.ADMIN_SOURCE,
            server.ADMIN_CFG.get("sessionHours"))
    _fd, _tf = tempfile.mkstemp(suffix=".json")
    with os.fdopen(_fd, "w", encoding="utf-8") as _f:
        json.dump({"adminToken": "tmp-only", "publicUrl": "photos.example.com/"}, _f)
    try:
        server.ADMIN_CFG_PATH = _tf
        server.set_admin_config(None)
        chk("set_admin_config 从 admin.json 读入并归一化 publicUrl",
            server.ADMIN_CFG.get("publicUrl") == "https://photos.example.com",
            f"{server.ADMIN_CFG.get('publicUrl')!r}")
    finally:
        server.ADMIN_CFG_PATH, server.ADMIN_PW, server.ADMIN_SOURCE = _bak[:3]
        server.ADMIN_CFG["sessionHours"] = _bak[3]
        os.remove(_tf)

    # =========================================================
    sect("组 5 · 未配口令时降级（R8）")
    # =========================================================
    server.ADMIN_PW = None
    server.ACCESS_PROTECT = False
    try:
        st, _, raw = req(base, "/api/session")
        chk("未配口令 → /api/session 仍 200 且 scope=anon",
            st == 200 and jbody(raw).get("scope") == "anon", f"HTTP {st}")
        st, _, _ = req(base, "/api/libs")
        chk("未配口令 → 浏览回复匿名（不锁死站点）", st == 200, f"HTTP {st}")
        st, _, _ = req(base, "/api/refresh?lib=t")
        chk("未配口令 → /api/refresh 仍被拒（写操作永不匿名）",
            st in (401, 403), f"HTTP {st}")
    finally:
        server.ADMIN_PW = "test-pw-123"
        server.ACCESS_PROTECT = True

    # =========================================================
    sect("组 6 · 管理员维护接口（放最后：会真的重扫索引）")
    # =========================================================
    st, _, _ = req(base, "/api/refresh?lib=t", cookies=ck_admin)
    chk("管理员 /api/refresh 缺 X-SL-Admin 头 → 403（CSRF）", st == 403, f"HTTP {st}")
    st, _, raw = req(base, "/api/refresh?lib=t", cookies=ck_admin,
                     headers={"X-SL-Admin": "1"})
    chk("管理员 /api/refresh（带头） → 2xx", 200 <= st < 300, f"HTTP {st}")
    st, _, raw = req(base, "/api/cache/gc", cookies=ck_admin,
                     headers={"X-SL-Admin": "1"})
    chk("管理员 /api/cache/gc（预览） → 200", st == 200, f"HTTP {st}")
    st, _, raw = req(base, "/api/cache/gc?apply=1", cookies=ck_admin,
                     headers={"X-SL-Admin": "1"})
    chk("管理员 /api/cache/gc?apply=1 → 200（授权后可执行）", st == 200, f"HTTP {st}")


if __name__ == "__main__":
    sys.exit(main())
