#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · 公网 Tunnel 安全加固 —— 服务端封锁自测（hermetic）
============================================================

背景：Cloudflare Tunnel 把 share.qqtang-match.fun 指向本机 localhost:9000。
cloudflared 与本服务**同机**运行 → 隧道请求的 TCP 对端必然是回环地址；同时
cloudflared / CF 边缘必带 X-Forwarded-Proto / X-Forwarded-For /
CF-Connecting-IP 等头，Host 原样透传公网域名。服务端据此把「公网请求」的
管理员面整体关闭（在任何口令验证 / 会话创建之前拒绝）。

  组 1  判据真值表（is_public_request 纯函数，含伪造头情形）
  组 2  公网（回环 + 特征头）：管理面全部 403，正确口令也永不被验证
  组 3  本机回环（无特征头）：管理认证不受影响（不误伤 localhost）
  组 4  公网分享：匿名识别 / 落地 / 授权集合 / 未授权拒绝 / 撤销即时失效
  组 5  伪造代理请求头：不能绕过公网封锁；也不会把 LAN 误判成公网
  组 6  局域网 / EasyTier（真实非回环对端）：管理认证行为不变

hermetic：import server 改写模块级全局 + 临时图库 + 临时 shares.db，
跑真实的 ThreadingHTTPServer + Handler；不起子进程、不碰真实数据。
用 http.client 直连（不走本机 HTTP 代理），Host 等请求头可精确控制，
以此模拟「cloudflared 从回环连入」与「局域网 / EasyTier 客户端」。

运行：  py -3 tests/test_public_guard.py
退出码 0 = 全绿。
"""

import collections
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
if APP not in sys.path:
    sys.path.insert(0, APP)

import server            # noqa: E402
import share_db          # noqa: E402
import index_db          # noqa: E402
import cache_db          # noqa: E402

PASS, FAIL, SKIPS = [], [], []

PUBLIC_HOST = "share.qqtang-match.fun"          # admin.json → publicUrl 的公网域名
PUBLIC_URL = "https://" + PUBLIC_HOST
TOKEN = "test-pw-123"

# cloudflared / CF 边缘一定会带、且 Host 原样透传公网域名（见报告里的实测转储）
TUNNEL_HEADERS = {
    "Host": PUBLIC_HOST,
    "X-Forwarded-Proto": "https",
    "X-Forwarded-For": "203.0.113.7",
    "CF-Connecting-IP": "203.0.113.7",
    "Cf-Ray": "8f3a2b1c4d5e6f70-SJC",
}


def chk(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{extra}]" if extra else ""))


def sect(t):
    print(f"\n=== {t} ===")


def skip(name, why=""):
    SKIPS.append(name)
    print(f"  ⏭ 跳过 {name}" + (f"  [{why}]" if why else ""))


# ---------------------------------------------------------
#  HTTP 小工具（http.client 直连：无代理、请求头完全可控）
# ---------------------------------------------------------
class Resp(object):
    def __init__(self, status, headers, set_cookies, body, err=""):
        self.status = status
        self.headers = headers            # 小写键
        self.cookies = set_cookies        # 所有 Set-Cookie 原值
        self.body = body
        self.err = err


def req(netloc, path, method="GET", body=None, headers=None, cookies=None,
        timeout=20):
    """netloc 形如 '127.0.0.1:9060' / '192.168.31.81:9060'。

    headers 里可以带 Host / X-Forwarded-* / CF-* —— http.client 会原样使用
    调用方给的 Host（skip_host），正好用来模拟 cloudflared 与伪造攻击。
    """
    host, _, port = netloc.rpartition(":")
    h = dict(headers or {})
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    try:
        c = http.client.HTTPConnection(host, int(port), timeout=timeout)
        try:
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            raw = r.read()
            hd, sc = {}, []
            for k, v in r.getheaders():
                if k.lower() == "set-cookie":
                    sc.append(v)
                else:
                    hd[k.lower()] = v
            return Resp(r.status, hd, sc, raw)
        finally:
            c.close()
    except Exception as e:                       # 连接层异常 → 让断言自然失败
        return Resp(0, {}, [], b"", repr(e))


def cookie_from(resp, name):
    for sc in resp.cookies:
        for part in sc.split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part[len(name) + 1:]
    return ""


def jbody(resp):
    try:
        return json.loads(resp.body.decode("utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------
#  夹具（结构与 tests/test_access.py 一致：两个库 + Live Photo 配对）
# ---------------------------------------------------------
def make_jpeg(path, w=64, h=48):
    from PIL import Image
    Image.new("RGB", (w, h), (120, 160, 200)).save(path, "JPEG")


def bucket(items):
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
    def write_blob(p, kind):
        if os.path.isfile(p):
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if kind == "img":
            make_jpeg(p)
        else:
            with open(p, "wb") as f:
                f.write(b"\x00" * 4096)

    items = []
    for rel, ts, kind, live, mov in specs:
        p = os.path.join(root, rel.replace("/", os.sep))
        write_blob(p, kind)
        if mov:
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


def setup_fixture(tmp):
    libA = os.path.join(tmp, "libA")
    libB = os.path.join(tmp, "libB")
    server.CACHE_DIR = os.path.join(tmp, "cache")
    server.INDEX = index_db.Index(os.path.join(tmp, "index.db"))
    server.CACHE_INDEX = cache_db.CacheIndex(os.path.join(tmp, "cache.db"))
    server.TRASH = None

    def ts(y, mo, d, hh=10):
        return int(time.mktime((y, mo, d, hh, 0, 0, 0, 0, -1)))

    specsA = [
        ("2024-09/IMG_0001.JPG", ts(2024, 9, 1), "img", 0, None),
        ("2024-09/IMG_0002.HEIC", ts(2024, 9, 2), "img", 1, "2024-09/IMG_0002.MOV"),
        ("2024-09/IMG_0003.JPG", ts(2024, 9, 3), "img", 0, None),
        ("2024-10/IMG_0004.JPG", ts(2024, 10, 1), "img", 0, None),
        ("2024-10/IMG_0005.JPG", ts(2024, 10, 2), "img", 0, None),
        ("2024-10/IMG_0006.MOV", ts(2024, 10, 3), "vid", 0, None),
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

    # ---- 访问控制：开保护 + 配口令 + 配公网域名 ----
    server.ADMIN_PW = TOKEN
    server.ADMIN_SOURCE = "测试"
    server.ADMIN_CFG["sessionHours"] = 12
    server.ADMIN_CFG["publicUrl"] = PUBLIC_URL
    server.ACCESS_CFG = dict(server.ACCESS_CFG)
    server.ACCESS_CFG.update({"protectRead": True, "shareEnabled": True,
                              "cookieSecure": "auto", "pepper": "",
                              "defaultExpireDays": 0, "maxItems": 5000})
    server.ACCESS_PROTECT = True
    server.BOOTSTRAP_DONE.set()
    return tmp


def start_server(bind):
    httpd = server.ThreadingHTTPServer((bind, 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"{bind}:{httpd.server_address[1]}"


def non_loopback_ipv4s():
    """本机所有非回环 IPv4（局域网 + EasyTier 虚拟网卡都会出现在这里）。"""
    ips = []
    try:
        host = socket.gethostname()
        for ip in socket.gethostbyname_ex(host)[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


# ---------------------------------------------------------
#  组 1 · 判据真值表
# ---------------------------------------------------------
def group1_truth_table():
    sect("组 1 · 判据真值表（is_public_request：只收紧、不放宽）")

    def hg(d):
        return lambda k: (d or {}).get(k)

    cases = [
        ("回环 + Host=公网域名（无其他头）→ 公网",
         "127.0.0.1", {"Host": PUBLIC_HOST}, True),
        ("回环 + Host=公网域名 + 全套 CF 头 → 公网",
         "127.0.0.1", TUNNEL_HEADERS, True),
        ("回环 + Host=LAN + X-Forwarded-Proto → 公网",
         "127.0.0.1", {"Host": "192.168.31.81:9000",
                       "X-Forwarded-Proto": "https"}, True),
        ("回环 + Host=LAN + 伪造 X-Forwarded-For → 公网",
         "127.0.0.1", {"Host": "192.168.31.81:9000",
                       "X-Forwarded-For": "192.168.31.81"}, True),
        ("回环 + Host=LAN + 伪造 CF-Connecting-IP → 公网",
         "127.0.0.1", {"Host": "192.168.31.81:9000",
                       "CF-Connecting-IP": "192.168.31.81"}, True),
        ("回环 + 无任何特征头（本机浏览器）→ 非公网",
         "127.0.0.1", {"Host": "127.0.0.1:9000"}, False),
        ("回环 + localhost + 无特征头 → 非公网",
         "127.0.0.1", {"Host": "localhost:9000"}, False),
        ("LAN 对端 + 干净请求 → 非公网",
         "192.168.31.81", {"Host": "192.168.31.81:9000"}, False),
        ("LAN 对端 + 伪造 XFF/XFP/CF-Connecting-IP → 非公网（头不提升信任）",
         "192.168.31.81", {"Host": "192.168.31.81:9000",
                           "X-Forwarded-For": "1.2.3.4",
                           "X-Forwarded-Proto": "https",
                           "CF-Connecting-IP": "1.2.3.4"}, False),
        ("EasyTier 对端 + 干净请求 → 非公网",
         "10.126.126.2", {"Host": "10.126.126.2:9000"}, False),
        ("EasyTier 对端 + Host=公网域名 → 公网（只收紧不放宽）",
         "10.126.126.2", {"Host": PUBLIC_HOST}, True),
        ("::1 回环 + X-Forwarded-Proto → 公网",
         "::1", {"Host": "whatever", "X-Forwarded-Proto": "https"}, True),
        ("公网 IPv4 对端 + 干净请求 → 非公网（端口直抖的极端情况另议）",
         "203.0.113.9", {"Host": "203.0.113.9:9000"}, False),
    ]
    for name, peer, hd, want in cases:
        got = server.is_public_request(peer, hg(hd))
        chk(name, got is want, f"got={got} want={want}")

    # publicUrl 未配置：Host 判据自动退场，回环+特征头判据仍然有效
    keep = server.ADMIN_CFG.get("publicUrl")
    server.ADMIN_CFG["publicUrl"] = ""
    try:
        got = server.is_public_request("127.0.0.1", hg(TUNNEL_HEADERS))
        chk("publicUrl 未配置时：回环 + CF 特征头 → 仍判公网（不依赖配置）",
            got is True, f"got={got}")
        got = server.is_public_request("127.0.0.1",
                                       hg({"Host": PUBLIC_HOST}))
        chk("publicUrl 未配置时：Host=公网域名 → 不再单凭 Host 判定",
            got is False, f"got={got}")
    finally:
        server.ADMIN_CFG["publicUrl"] = keep

    chk("回环判定兼容 IPv4-mapped（::ffff:127.0.0.1）",
        server.is_loopback_ip("::ffff:127.0.0.1") is True)
    chk("非 IP 字符串不会误判成回环",
        server.is_loopback_ip("not-an-ip") is False)


# ---------------------------------------------------------
#  组 2 · 公网封锁
# ---------------------------------------------------------
ADMIN_GETS = [
    "/api/admin/status", "/api/admin/login", "/api/admin/logout",
    "/api/refresh?lib=t", "/api/index", "/api/index/progress",
    "/api/cache", "/api/cache/gc?apply=1", "/api/stats",
    "/api/trash", "/api/trash/stats",
    "/api/trash/thumb?item=x", "/api/trash/media?item=x",
    "/api/definitely-not-a-real-path",
]
ADMIN_POSTS = [
    ("/api/admin/login", {"token": TOKEN}),          # 正确口令也必须被拒
    ("/api/admin/login", {"token": "wrong-token"}),
    ("/api/admin/logout", {}),
    ("/api/index/scan", {"full": 1}),
    ("/api/index/scan", {"full": 0}),
    ("/api/share/create", {"lib": "t",
                           "rels": ["2024-09/IMG_0001.JPG"]}),
    ("/api/share/list", {}),
    ("/api/share/revoke", {"id": "x"}),
    ("/api/delete", {"lib": "t", "items": [], "dry": 1}),
    ("/api/trash/restore", {}),
    ("/api/trash/purge", {}),
    ("/api/trash/empty", {}),
]


def group2_public_block(net):
    sect("组 2 · 公网 Tunnel：管理面全部 403（口令验证/会话创建之前）")
    for p in ADMIN_GETS:
        r = req(net, p, headers=TUNNEL_HEADERS)
        chk(f"公网 GET {p.split('?')[0]} → 403", r.status == 403,
            f"HTTP {r.status} {r.err}")
    for p, body in ADMIN_POSTS:
        r = req(net, p, "POST", body,
                dict(TUNNEL_HEADERS, **{"X-SL-Admin": "1"}))
        chk(f"公网 POST {p} → 403", r.status == 403,
            f"HTTP {r.status} {r.err}")

    # ---- 关键：正确口令也永不到达验证环节，且不创建会话 ----
    before = len(server._admin_sessions)
    r = req(net, "/api/admin/login", "POST", {"token": TOKEN},
            dict(TUNNEL_HEADERS, **{"X-SL-Admin": "1"}))
    d = jbody(r)
    chk("公网用**正确口令**登录 → 403 且不返回「口令错误」"
        "（口令从未被验证）",
        r.status == 403 and "口令错误" not in (d.get("error") or ""),
        f"HTTP {r.status} {d.get('error', '')}")
    chk("公网登录拒绝：不下发任何会话 Cookie", not r.cookies, str(r.cookies))
    chk("公网登录拒绝：不创建管理员会话",
        len(server._admin_sessions) == before,
        f"会话数 {before} → {len(server._admin_sessions)}")
    chk("拒绝信息指明公网封锁（可向用户解释）",
        "公网" in (d.get("error") or ""), d.get("error", ""))

    # 带着（伪造的）管理会话 Cookie 访问公网，也不发生权限提升
    r = req(net, "/api/session", headers=dict(
        TUNNEL_HEADERS, Cookie=f"{server.SESSION_COOKIE}=forged-sid"))
    d = jbody(r)
    chk("公网带伪造管理 Cookie 的 /api/session → scope=anon、canManage=false",
        r.status == 200 and d.get("scope") == "anon"
        and d.get("canManage") is False
        and (d.get("admin") or {}).get("authed") is False,
        f"HTTP {r.status} {d.get('scope')}")

    # ---- 公网仍然可用的面 ----
    for p in ("/", "/index.html", "/app.css", "/app.js", "/favicon.svg",
              "/manifest.webmanifest"):
        r = req(net, p, headers=TUNNEL_HEADERS)
        chk(f"公网 GET {p} → 200（静态外壳不受影响）", r.status == 200,
            f"HTTP {r.status}")
    r = req(net, "/api/session", headers=TUNNEL_HEADERS)
    d = jbody(r)
    chk("公网 /api/session → 200 且 scope=anon（匿名会话识别不受影响）",
        r.status == 200 and d.get("scope") == "anon", f"HTTP {r.status}")
    r = req(net, "/api/bootstrap", headers=TUNNEL_HEADERS)
    d = jbody(r)
    chk("公网 /api/bootstrap → 200 且 anon 只回身份（不含相册数据）",
        r.status == 200 and (d.get("session") or {}).get("scope") == "anon"
        and "libs" not in d and "items" not in d, f"HTTP {r.status}")
    r = req(net, "/api/libs", headers=TUNNEL_HEADERS)
    chk("公网匿名 /api/libs → 401（与改动前一致：过闸门但无身份）",
        r.status == 401, f"HTTP {r.status}")
    r = req(net, "/s/no-such-token", headers=TUNNEL_HEADERS)
    chk("公网 /s/<无效令牌> → 404（不区分不存在/已撤销）",
        r.status == 404, f"HTTP {r.status}")


# ---------------------------------------------------------
#  组 3 · 本机回环（无特征头）不受影响
# ---------------------------------------------------------
def group3_local_admin(net):
    sect("组 3 · 本机回环（无特征头）：管理认证不受影响")
    r = req(net, "/api/admin/status")
    d = jbody(r)
    chk("本机 /api/admin/status → 200", r.status == 200, f"HTTP {r.status}")
    chk("本机 status：要求授权、尚未登录",
        d.get("required") is True and d.get("authed") is False, f"{d}")
    r = req(net, "/api/admin/login", "POST", {"token": "wrong-pw"},
            {"X-SL-Admin": "1"})
    chk("本机错误口令 → 401（未被公网封锁误伤）", r.status == 401,
        f"HTTP {r.status}")
    r = req(net, "/api/admin/login", "POST", {"token": TOKEN},
            {"X-SL-Admin": "1"})
    sid = cookie_from(r, server.SESSION_COOKIE)
    chk("本机正确口令 → 200 且下发会话 Cookie",
        r.status == 200 and bool(sid), f"HTTP {r.status}")
    ck = {server.SESSION_COOKIE: sid}
    r = req(net, "/api/libs", cookies=ck)
    chk("本机会话 /api/libs → 200", r.status == 200, f"HTTP {r.status}")
    r = req(net, "/api/admin/logout", "POST", {}, {"X-SL-Admin": "1"}, ck)
    chk("本机登出 → 200", r.status == 200, f"HTTP {r.status}")
    r = req(net, "/api/libs", cookies=ck)
    chk("登出后 /api/libs → 401", r.status == 401, f"HTTP {r.status}")


# ---------------------------------------------------------
#  组 4 · 公网分享照常
# ---------------------------------------------------------
def group4_public_share(net):
    sect("组 4 · 公网分享：匿名识别 / 落地 / 授权集合 / 撤销即时")
    r = req(net, "/api/admin/login", "POST", {"token": TOKEN},
            {"X-SL-Admin": "1"})
    ck = {server.SESSION_COOKIE: cookie_from(r, server.SESSION_COOKIE)}
    R = ["2024-09/IMG_0001.JPG", "2024-09/IMG_0002.HEIC", "2024-09/IMG_0003.JPG"]
    MOV = "2024-09/IMG_0002.MOV"
    OUT = "2024-10/IMG_0004.JPG"
    r = req(net, "/api/share/create", "POST",
            {"lib": "t", "rels": R, "title": "九月精选", "kind": "month"},
            {"X-SL-Admin": "1"}, ck)
    tok = jbody(r).get("token") or ""
    chk("（本机管理员）创建分享 → 200 且返回令牌",
        r.status == 200 and bool(tok), f"HTTP {r.status}")

    r = req(net, f"/s/{tok}", headers=TUNNEL_HEADERS)
    sc = cookie_from(r, server.SHARE_COOKIE)
    chk("公网 /s/<token> → 302 且种下分享 Cookie",
        r.status == 302 and bool(sc), f"HTTP {r.status}")
    chk("公网落地 Cookie 带 Secure（X-Forwarded-Proto=https 生效，行为不变）",
        any("Secure" in c for c in r.cookies), str(r.cookies))
    chk("302 Location 不回显令牌", r.headers.get("location") == "/?share=1",
        r.headers.get("location") or "")
    sck = {server.SHARE_COOKIE: sc}

    r = req(net, "/api/session", headers=TUNNEL_HEADERS, cookies=sck)
    d = jbody(r)
    chk("公网分享访客 /api/session → scope=share、count=3、不可管理",
        r.status == 200 and d.get("scope") == "share"
        and (d.get("share") or {}).get("count") == 3
        and d.get("canManage") is False, f"HTTP {r.status} {d.get('scope')}")

    r = req(net, "/api/bootstrap", headers=TUNNEL_HEADERS, cookies=sck)
    d = jbody(r)
    chk("公网 /api/bootstrap（分享首屏一次往返）→ 200 且只含被分享的库",
        r.status == 200 and (d.get("session") or {}).get("scope") == "share"
        and len((d.get("libs") or {}).get("libraries") or []) == 1
        and bool(d.get("items")), f"HTTP {r.status}")

    r = req(net, "/api/libs", headers=TUNNEL_HEADERS, cookies=sck)
    libs = jbody(r).get("libraries") or []
    chk("公网 /api/libs → 只有一个库、count=3",
        r.status == 200 and len(libs) == 1 and libs[0]["count"] == 3,
        f"{libs}")

    r = req(net, "/api/items?lib=t", headers=TUNNEL_HEADERS, cookies=sck)
    d = jbody(r)
    rels = {it["p"] for it in (d.get("items") or [])}
    chk("公网 /api/items → 恰好 3 张授权照片",
        r.status == 200 and d.get("total") == 3 and rels == set(R),
        f"total={d.get('total')}")

    for p in ("/thumb?lib=t&p=" + urllib.parse.quote(R[0]) + "&s=640",
              "/photo?lib=t&p=" + urllib.parse.quote(R[0]),
              "/raw?lib=t&p=" + urllib.parse.quote(R[0])):
        r = req(net, p, headers=TUNNEL_HEADERS, cookies=sck)
        chk(f"公网 {p.split('?')[0]} 授权照片 → 200", r.status == 200,
            f"HTTP {r.status} {r.err}")
    r = req(net, "/raw?lib=t&p=" + urllib.parse.quote(R[0]),
            headers=TUNNEL_HEADERS, cookies=sck)
    cc = r.headers.get("cache-control") or ""
    chk("公网分享媒体仍是 private, no-store（撤销即时生效不受影响）",
        "private" in cc and "no-store" in cc, cc)
    r = req(net, "/video?lib=t&p=" + urllib.parse.quote(MOV),
            headers=dict(TUNNEL_HEADERS, Range="bytes=0-99"), cookies=sck)
    chk("公网 /video 授权 Live 配对视频 → 206", r.status == 206,
        f"HTTP {r.status}")

    # 未授权照片：仍然拒绝
    r = req(net, "/raw?lib=t&p=" + urllib.parse.quote(OUT),
            headers=TUNNEL_HEADERS, cookies=sck)
    chk("公网取集合外原图 → 403", r.status == 403, f"HTTP {r.status}")
    r = req(net, "/api/item?lib=t&p=" + urllib.parse.quote(OUT),
            headers=TUNNEL_HEADERS, cookies=sck)
    chk("公网取集合外条目 → 403", r.status == 403, f"HTTP {r.status}")
    r = req(net, "/raw?lib=u&p=" + urllib.parse.quote("x/IMG_9001.JPG"),
            headers=TUNNEL_HEADERS, cookies=sck)
    chk("公网跨库取媒体 → 403", r.status == 403, f"HTTP {r.status}")

    # 撤销 → 即时失效
    r = req(net, "/api/share/list", "POST", {}, {"X-SL-Admin": "1"}, ck)
    row = next((s for s in (jbody(r).get("shares") or [])), {})
    r = req(net, "/api/share/revoke", "POST", {"id": row.get("id")},
            {"X-SL-Admin": "1"}, ck)
    chk("撤销分享 → 200", r.status == 200, f"HTTP {r.status}")
    r = req(net, f"/s/{tok}", headers=TUNNEL_HEADERS)
    chk("撤销后公网 /s/<token> → 404（即时失效）", r.status == 404,
        f"HTTP {r.status}")
    r = req(net, "/api/items?lib=t", headers=TUNNEL_HEADERS, cookies=sck)
    chk("撤销后公网 /api/items（旧 Cookie）→ 401/403",
        r.status in (401, 403), f"HTTP {r.status}")
    r = req(net, "/raw?lib=t&p=" + urllib.parse.quote(R[0]),
            headers=TUNNEL_HEADERS, cookies=sck)
    chk("撤销后公网取原图（旧 Cookie）→ 401/403",
        r.status in (401, 403), f"HTTP {r.status}")


# ---------------------------------------------------------
#  组 5 · 伪造代理请求头
# ---------------------------------------------------------
def group5_forged_headers(net, lan_nets):
    sect("组 5 · 伪造代理请求头：不能绕过公网封锁")
    # 5.1 隧道对端（回环）但把 Host / XFF / CF-Connecting-IP 全伪造成局域网
    forged_lan = {
        "Host": "192.168.31.81:9000",
        "X-Forwarded-For": "192.168.31.81",
        "X-Forwarded-Proto": "http",
        "CF-Connecting-IP": "192.168.31.81",
        "Cf-Ray": "8f3a2b1c4d5e6f70-SJC",
    }
    for p, m, body in (("/api/admin/login", "POST", {"token": TOKEN}),
                       ("/api/admin/status", "GET", None),
                       ("/api/admin/logout", "POST", {})):
        r = req(net, p, m, body,
                dict(forged_lan, **{"X-SL-Admin": "1"}))
        chk(f"回环对端 + 全套伪造局域网头 → {m} {p} 仍 403",
            r.status == 403, f"HTTP {r.status}")

    # 5.2 只带单个特征头也照样识破（cloudflared 至少会带一个）
    for name, val in (("X-Forwarded-For", "192.168.31.81"),
                      ("X-Forwarded-Proto", "https"),
                      ("CF-Connecting-IP", "192.168.31.81"),
                      ("Cf-Connecting-IP", "192.168.31.81"),
                      ("Cf-Ray", "8f3a2b1c4d5e6f70-SJC")):
        r = req(net, "/api/admin/login", "POST", {"token": TOKEN},
                {name: val, "X-SL-Admin": "1"})
        chk(f"回环对端 + 仅 {name} → 登录仍 403", r.status == 403,
            f"HTTP {r.status}")

    # 5.3 不带任何特征头 = 本机请求 → 放行（确认不会被上面的规则误伤）
    r = req(net, "/api/admin/login", "POST", {"token": TOKEN},
            {"X-SL-Admin": "1"})
    chk("回环对端 + 无特征头 → 登录正常 200（本机管理员）",
        r.status == 200, f"HTTP {r.status} {r.err}")

    if not lan_nets:
        skip("组 5.4/5.5 · LAN 对端伪造", "未发现可用的非回环 IPv4")
        return
    for ip, lnet in lan_nets.items():
        r = req(lnet, "/api/admin/login", "POST", {"token": TOKEN},
                {"Host": PUBLIC_HOST, "X-SL-Admin": "1"})
        chk(f"[{ip}] 对端 + Host=公网域名 → 登录 403（按公网处理，只收紧）",
            r.status == 403, f"HTTP {r.status}")
        r = req(lnet, "/api/admin/login", "POST", {"token": TOKEN},
                {"X-Forwarded-For": "1.2.3.4", "X-Forwarded-Proto": "https",
                 "CF-Connecting-IP": "1.2.3.4", "X-SL-Admin": "1"})
        chk(f"[{ip}] 对端 + 伪造 XFF/XFP/CF-IP（Host 仍是本机 IP）→ 登录 200"
            "（代理头不提升也不降级信任）", r.status == 200,
            f"HTTP {r.status} {r.err}")


# ---------------------------------------------------------
#  组 6 · 局域网 / EasyTier：管理认证行为不变
# ---------------------------------------------------------
def group6_lan(lan_nets):
    sect("组 6 · 局域网 / EasyTier（真实非回环对端）：管理认证行为不变")
    if not lan_nets:
        skip("组 6", "未发现可用的非回环 IPv4")
        return
    for ip, net in lan_nets.items():
        r = req(net, "/api/admin/status")
        chk(f"[{ip}] /api/admin/status → 200", r.status == 200,
            f"HTTP {r.status} {r.err}")
        r = req(net, "/api/admin/login", "POST", {"token": "wrong-token"},
                {"X-SL-Admin": "1"})
        chk(f"[{ip}] 错误口令 → 401（未被公网封锁误伤）", r.status == 401,
            f"HTTP {r.status}")
        r = req(net, "/api/admin/login", "POST", {"token": TOKEN},
                {"X-SL-Admin": "1"})
        sid = cookie_from(r, server.SESSION_COOKIE)
        chk(f"[{ip}] 正确口令 → 200 且下发会话",
            r.status == 200 and bool(sid), f"HTTP {r.status}")
        ck = {server.SESSION_COOKIE: sid}
        ok, detail = True, []
        for p in ("/api/libs", "/api/tree?lib=t", "/api/items?lib=t",
                  "/api/stats", "/api/index", "/api/index/progress",
                  "/api/cache", "/api/trash"):
            st = req(net, p, cookies=ck).status
            detail.append(f"{p.split('?')[0]}:{st}")
            ok = ok and st == 200
        chk(f"[{ip}] 登录后只读管理接口全部 200", ok, " ".join(detail))
        r = req(net, "/api/share/create", "POST",
                {"lib": "t", "rels": ["2024-09/IMG_0001.JPG"]},
                {"X-SL-Admin": "1"}, ck)
        chk(f"[{ip}] 创建分享 → 200", r.status == 200, f"HTTP {r.status}")
        r = req(net, "/raw?lib=t&p=" + urllib.parse.quote("2024-09/IMG_0001.JPG"),
                cookies=ck)
        chk(f"[{ip}] 管理原图 → 200", r.status == 200, f"HTTP {r.status}")
        r = req(net, "/api/admin/logout", "POST", {}, {"X-SL-Admin": "1"}, ck)
        chk(f"[{ip}] 登出 → 200", r.status == 200, f"HTTP {r.status}")
        r = req(net, "/api/libs", cookies=ck)
        chk(f"[{ip}] 登出后 → 401", r.status == 401, f"HTTP {r.status}")


# ---------------------------------------------------------
def main():
    tmp = tempfile.mkdtemp(prefix="seeklive_public_")
    print("=" * 66)
    print("  SeekLive · 公网 Tunnel 安全加固自测（hermetic，不碰真实图库）")
    print("=" * 66)
    servers = []
    try:
        setup_fixture(tmp)
        group1_truth_table()

        h, net = start_server("127.0.0.1")
        servers.append(h)
        group2_public_block(net)
        group3_local_admin(net)
        group4_public_share(net)

        lan_nets = {}
        for ip in non_loopback_ipv4s():
            try:
                h2, n2 = start_server(ip)
            except Exception as e:
                skip(f"绑定 {ip}（LAN/EasyTier 组）", repr(e))
                continue
            servers.append(h2)
            lan_nets[ip] = n2
        if lan_nets:
            print(f"  · 非回环 IPv4（LAN/EasyTier 对端模拟）：{sorted(lan_nets)}")
        group5_forged_headers(net, lan_nets)
        group6_lan(lan_nets)
    finally:
        for h in servers:
            try:
                h.shutdown()
            except Exception:
                pass
        try:
            if server.SHARES:
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
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}"
          + (f" / 跳过 {len(SKIPS)}" if SKIPS else ""))
    if FAIL:
        for f in FAIL:
            print("   ✗", f)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
