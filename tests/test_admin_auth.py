#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · 管理认证独立化 —— 正反两面测试
==========================================

覆盖用户明确要求的全部验收点：

  A. 状态机闭环：无口令 / 错误口令 / 正确登录 / 会话有效 / 会话过期 /
                 退出失效 / 管理操作成功
  B. 访问边界：**无口令**时静态资源 / 照片 / 视频 / Live Photo / 浏览 API 全部匿名可读；
               **配了口令**时默认拒绝 —— 静态外壳仍匿名，数据与媒体一律要身份（v1.1）
  C. 拒绝边界：未授权的 删除 / 恢复 / 永久删除 / 清空回收站
               全部被**服务端**拒绝（不是前端置灰）
  D. 安全细节：Cookie 属性、来源校验、CSRF、登录失败退避、日志脱敏
  E. 启动参数确实进入服务端认证状态（杜绝「参数已打印、闸门未生效」）

运行：  python tests/test_admin_auth.py
说明：  本套件会**先停止**当前正在运行的服务实例（PID 文件守卫只允许单实例），
       并在结束后停掉自己启动的实例。全程只读浏览 + dry=1 预览，不动任何真实文件。
       少数用例依赖环境里的 libraries.json（≥2 个库、含只读库 wework）；
       未提供时**明确跳过**，不误报失败。
"""

import http.cookiejar
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
PY = sys.executable
ADMIN_JSON = os.path.join(APP, "admin.json")
LOG_FILE = os.path.join(APP, "logs", "server.log")

PASS = FAIL = 0
FAILED = []


def chk(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}" + (f"  [{extra}]" if extra else ""))
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  ❌ {name}" + (f"  [{extra}]" if extra else ""))


def sect(t):
    print(f"\n=== {t} ===")


def skip(name, why=""):
    """环境未提供所需素材时**明确跳过**（不静默通过，也不误报失败）。"""
    print(f"  ⏭ 跳过 {name}" + (f"  [{why}]" if why else ""))


# ---------------------------------------------------------
#  HTTP 小工具
# ---------------------------------------------------------
def request(url, method="GET", data=None, headers=None, opener=None,
            timeout=20, raw_body=None):
    body = raw_body
    if body is None and data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    op = opener or urllib.request.build_opener()
    try:
        resp = op.open(r, timeout=timeout)
        return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def new_session():
    """独立 Cookie 会话（模拟一个浏览器）。"""
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)), cj


def anonymous():
    return urllib.request.build_opener()


def jbody(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------
#  服务实例（一次只能跑一个：PID 文件守卫）
# ---------------------------------------------------------
class Srv:
    def __init__(self, port, extra_args=(), admin_json=None):
        self.port = port
        self.extra_args = list(extra_args)
        self.admin_json = admin_json
        self.proc = None
        self.out = os.path.join(HERE, f"_admin_srv_{port}.out")

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def _stop_running(self):
        subprocess.run([PY, "server.py", "--stop"], cwd=APP,
                       capture_output=True, timeout=30)
        time.sleep(1.2)
        self._kill_port_squatter()

    def _listener_pid(self):
        """返回**本实例端口**上正在 LISTEN 的进程 pid（没有则 None）。"""
        try:
            out = subprocess.run(["netstat", "-ano"], capture_output=True,
                                 timeout=15).stdout.decode("utf-8", "ignore")
        except Exception:
            return None
        needle = f":{self.port} "
        for line in out.splitlines():
            if "LISTENING" in line and needle in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
        return None

    def _kill_port_squatter(self):
        """★ 兜底清理：PID 文件是**全局唯一**的，一旦被覆盖或丢失（上一次运行被
        Ctrl+C / 超时打断就会这样），`--stop` 就找不到它，端口上会留一个孤儿实例。
        之后新实例会因为「服务已在运行」而启动失败，表现为莫名其妙的启动失败 / 401。
        这里按**本实例自己的端口**精确清掉孤儿 —— 只动测试用的 905x 端口。"""
        pid = self._listener_pid()
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=15)
            time.sleep(0.8)

    def start(self):
        self._stop_running()
        if self.admin_json is None:
            if os.path.isfile(ADMIN_JSON):
                os.remove(ADMIN_JSON)
        else:
            with open(ADMIN_JSON, "w", encoding="utf-8") as f:
                json.dump(self.admin_json, f, ensure_ascii=False, indent=2)
        args = [PY, "-u", "server.py", "--port", str(self.port), "--no-recheck"]
        args += self.extra_args
        fh = open(self.out, "wb")
        self.proc = subprocess.Popen(args, cwd=APP, stdout=fh, stderr=subprocess.STDOUT)
        deadline = time.time() + 40
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False, self.banner()
            try:
                st, _, _ = request(self.base + "/api/admin/status", timeout=2)
                if st == 200:
                    break
            except Exception:
                pass
            time.sleep(0.4)
        else:
            return False, self.banner()
        # 端口先于索引就绪：必须等启动索引真正跑完，断言才有确定性。
        # ★ v1.1 默认拒绝之后，配了口令的实例**匿名取不到 /api/libs**（401），
        #   所以就绪探针必须先登录再问 —— 否则每个实例都要空等 90 秒并误报「启动失败」。
        probe = self._probe_opener()
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                st, _, raw = request(self.base + "/api/libs", timeout=5, opener=probe)
                d = jbody(raw)
                if st == 200 and d.get("ready") is True and not d.get("indexing"):
                    return True, self.banner()
            except Exception:
                pass
            time.sleep(0.4)
        return False, self.banner()

    def _configured_token(self):
        """本实例是否配了管理口令；配了就把值取出来（供就绪探针登录用）。"""
        for i, a in enumerate(self.extra_args):
            if a in ("--admin-token", "--token") and i + 1 < len(self.extra_args):
                return self.extra_args[i + 1]
        if self.admin_json:
            return (self.admin_json or {}).get("adminToken") or None
        return None

    def _probe_opener(self):
        """就绪探针用的 opener：配了口令 → 先登录拿会话；没配 → 匿名。"""
        tok = self._configured_token()
        if not tok:
            return anonymous()
        op, _ = new_session()
        try:
            request(self.base + "/api/admin/login", "POST", {"token": tok},
                    {"X-SL-Admin": "1"}, op, timeout=5)
        except Exception:
            pass
        return op

    def banner(self):
        try:
            with open(self.out, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def stop(self):
        self._stop_running()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass


def cleanup_admin_json(backup=None):
    if backup:
        shutil.move(backup, ADMIN_JSON)
    elif os.path.isfile(ADMIN_JSON):
        os.remove(ADMIN_JSON)


# ---------------------------------------------------------
#  主流程
# ---------------------------------------------------------
def main():
    print("=" * 66)
    print("  SeekLive · 管理认证独立化 · 正反两面测试")
    print("=" * 66)

    # 备份真实 admin.json（若存在）
    backup = None
    if os.path.isfile(ADMIN_JSON):
        backup = ADMIN_JSON + ".testbak"
        shutil.move(ADMIN_JSON, backup)

    pw = "Zq7-" + secrets.token_hex(4)          # 用于日志脱敏断言的独特口令
    other = "Zq7-" + secrets.token_hex(4)

    try:
        # ---------------------------------------------------------
        sect("组 1 · 无口令（默认姿态）：浏览全放行、管理全关闭")
        # ---------------------------------------------------------
        s = Srv(9050)
        ok, banner = s.start()
        chk("无口令实例启动成功", ok, banner.strip().splitlines()[-1] if banner else "")

        st, _, raw = request(s.base + "/api/admin/status")
        d = jbody(raw)
        chk("状态：required=false（未配置管理口令）", st == 200 and d.get("required") is False,
            f"HTTP {st} {d}")

        # 匿名浏览：静态资源
        for p in ("/", "/index.html", "/app.css", "/app.js", "/favicon.svg",
                  "/manifest.webmanifest"):
            st, _, _ = request(s.base + p)
            chk(f"无口令 · 匿名静态 {p} → 200", st == 200, f"HTTP {st}")

        # 匿名浏览：普通 API
        st, _, raw = request(s.base + "/api/libs")
        libs = jbody(raw).get("libraries") or []
        if len(libs) < 2:
            skip("图库数量 ≥ 2", f"本机 libraries.json 只配了 {len(libs)} 个库")
        chk("无口令 · 匿名 /api/libs → 200", st == 200 and len(libs) >= 1,
            f"HTTP {st} libs={len(libs)}")

        lib_ids = [l["id"] for l in libs]
        img_lib = next((i for i in lib_ids if i == "live"), lib_ids[0])
        vid_lib = next((i for i in lib_ids if i == "videos"), lib_ids[0])

        st, _, raw = request(s.base + f"/api/tree?lib={img_lib}")
        chk("无口令 · 匿名 /api/tree → 200", st == 200, f"HTTP {st}")

        st, _, raw = request(s.base + f"/api/items?lib={img_lib}&lim=2")
        items = jbody(raw).get("items") or []
        img_rel = items[0]["p"] if items else ""
        chk("无口令 · 匿名 /api/items → 200 且有条目", st == 200 and bool(img_rel),
            f"HTTP {st} rel={img_rel}")

        st, _, raw = request(s.base + f"/api/items?lib={vid_lib}&lim=3")
        vitems = jbody(raw).get("items") or []
        vid_rel = next((x["p"] for x in vitems if x.get("k") == "vid"), "") or img_rel
        chk("无口令 · 匿名 /api/items(视频库) → 200", st == 200, f"HTTP {st}")

        st, _, _ = request(s.base + f"/api/item?lib={img_lib}&p=" + urllib.parse.quote(img_rel))
        chk("无口令 · 匿名 /api/item → 200", st == 200, f"HTTP {st}")
        st, _, _ = request(s.base + f"/api/search?lib={img_lib}&q=IMG&lim=2")
        chk("无口令 · 匿名 /api/search → 200", st == 200, f"HTTP {st}")

        # 匿名浏览：照片 / 视频 / Live Photo 媒体（绝不加登录）
        st, _, _ = request(s.base + f"/thumb?lib={img_lib}&p="
                           + urllib.parse.quote(img_rel) + "&s=160")
        chk("无口令 · 匿名 /thumb → 200（缩略图）", st == 200, f"HTTP {st}")
        st, _, _ = request(s.base + f"/photo?lib={img_lib}&p="
                           + urllib.parse.quote(img_rel))
        chk("无口令 · 匿名 /photo → 200（大图）", st == 200, f"HTTP {st}")
        st, _, _ = request(s.base + f"/raw?lib={img_lib}&p="
                           + urllib.parse.quote(img_rel))
        chk("无口令 · 匿名 /raw → 200（原图）", st == 200, f"HTTP {st}")
        st, _, _ = request(s.base + f"/video?lib={vid_lib}&p="
                           + urllib.parse.quote(vid_rel),
                           headers={"Range": "bytes=0-1023"})
        chk("无口令 · 匿名 /video → 200/206（视频流）", st in (200, 206), f"HTTP {st}")

        # 未配置口令 → 四个破坏性操作必须 403（管理整体关闭）
        for name, path, payload in (
                ("/api/delete", "/api/delete", {"lib": vid_lib, "items": ["x"], "dry": 1}),
                ("/api/trash/restore", "/api/trash/restore", {"lib": vid_lib, "items": ["x"]}),
                ("/api/trash/purge", "/api/trash/purge",
                 {"lib": vid_lib, "items": ["x"], "confirm": 1}),
                ("/api/trash/empty", "/api/trash/empty",
                 {"lib": vid_lib, "confirm": 1})):
            st, _, raw = request(s.base + path, "POST", payload,
                                 headers={"X-SL-Admin": "1"})
            chk(f"无口令 · POST {name} → 403", st == 403,
                f"HTTP {st} {jbody(raw).get('error','')}")

        st, _, raw = request(s.base + "/api/admin/login", "POST", {"token": "whatever"},
                             headers={"X-SL-Admin": "1"})
        chk("无口令 · 登录接口 → 403（未配置）", st == 403, f"HTTP {st}")

        # ---------------------------------------------------------
        sect("组 2 · 有口令：默认拒绝（静态外壳匿名、数据/媒体要身份）、管理需授权")
        # ---------------------------------------------------------
        s2 = Srv(9051, ["--admin-token", pw])
        ok, banner = s2.start()
        chk("有口令实例启动成功", ok, banner.strip().splitlines()[-1] if banner else "")

        st, _, raw = request(s2.base + "/api/admin/status")
        d = jbody(raw)
        chk("状态：required=true, authed=false（匿名）",
            st == 200 and d.get("required") is True and d.get("authed") is False,
            f"HTTP {st} {d}")
        chk("启动提示写明「源文件管理：已启用」",
            "源文件管理 : 已启用" in banner, "banner")
        chk("启动输出不含口令明文（脱敏）", pw not in banner, "banner")

        # ★ v1.1 默认拒绝：配了口令后，**静态外壳仍匿名，数据与媒体一律要身份**。
        #   （这正是相对旧版"浏览公开"的核心变化，单独断言。）
        anon = anonymous()
        detail, static_ok = [], True
        for p in ("/", "/app.css", "/app.js"):
            st, _, _ = request(s2.base + p, opener=anon)
            detail.append(f"{p}:{st}")
            static_ok = static_ok and st == 200
        chk("有口令 · 匿名静态外壳仍 200（前端要能自判身份）", static_ok, " ".join(detail))

        detail, api_denied = [], True
        for p in ("/api/libs", f"/api/tree?lib={img_lib}",
                  f"/api/items?lib={img_lib}&lim=1"):
            st, _, _ = request(s2.base + p, opener=anon)
            detail.append(f"{p.split('?')[0]}:{st}")
            api_denied = api_denied and st == 401
        chk("有口令 · 匿名浏览 API → 401（默认拒绝）", api_denied, " ".join(detail))

        media_probes = (
            ("/thumb", f"/thumb?lib={img_lib}&p=" + urllib.parse.quote(img_rel) + "&s=160", {}),
            ("/photo", f"/photo?lib={img_lib}&p=" + urllib.parse.quote(img_rel), {}),
            ("/raw", f"/raw?lib={img_lib}&p=" + urllib.parse.quote(img_rel), {}),
            ("/video", f"/video?lib={vid_lib}&p=" + urllib.parse.quote(vid_rel),
             {"Range": "bytes=0-1023"}),
        )
        for label, url, hdrs in media_probes:
            st, _, _ = request(s2.base + url, headers=hdrs, opener=anon)
            chk(f"有口令 · 匿名 {label} → 401（默认拒绝）", st == 401, f"HTTP {st}")

        # 授权之后：同样这些路径必须全部可用 —— 证明拒绝的是"未授权"而非"功能坏了"
        sess, _cj = new_session()
        request(s2.base + "/api/admin/login", "POST", {"token": pw},
                {"X-SL-Admin": "1"}, sess)
        detail, api_ok = [], True
        for p in ("/api/libs", f"/api/tree?lib={img_lib}",
                  f"/api/items?lib={img_lib}&lim=1"):
            st, _, _ = request(s2.base + p, opener=sess)
            detail.append(f"{p.split('?')[0]}:{st}")
            api_ok = api_ok and st == 200
        chk("有口令 · 授权后浏览 API 全部 200", api_ok, " ".join(detail))
        for label, url, hdrs in media_probes:
            st, _, _ = request(s2.base + url, headers=hdrs, opener=sess)
            chk(f"有口令 · 授权后 {label} → 200/206", st in (200, 206), f"HTTP {st}")

        # 未授权（无会话）→ 401 + needAuth
        for name, path, payload in (
                ("/api/delete", "/api/delete", {"lib": vid_lib, "items": ["x"], "dry": 1}),
                ("/api/trash/restore", "/api/trash/restore", {"lib": vid_lib, "items": ["x"]}),
                ("/api/trash/purge", "/api/trash/purge",
                 {"lib": vid_lib, "items": ["x"], "confirm": 1}),
                ("/api/trash/empty", "/api/trash/empty",
                 {"lib": vid_lib, "confirm": 1})):
            st, _, raw = request(s2.base + path, "POST", payload,
                                 headers={"X-SL-Admin": "1"})
            b = jbody(raw)
            chk(f"有口令 · 未授权 POST {name} → 401 且 needAuth",
                st == 401 and b.get("needAuth") is True, f"HTTP {st} {b}")

        # ---------------------------------------------------------
        sect("组 3 · 登录 → 会话 → 管理操作 → 退出")
        # ---------------------------------------------------------
        op, cj = new_session()
        st, h, raw = request(s2.base + "/api/admin/login", "POST",
                             {"token": "definitely-wrong"}, {"X-SL-Admin": "1"}, op)
        chk("错误口令 → 401", st == 401, f"HTTP {st} {jbody(raw).get('error','')}")

        st, h, raw = request(s2.base + "/api/admin/login", "POST",
                             {"token": pw}, {"X-SL-Admin": "1"}, op)
        sc = h.get("Set-Cookie") or ""
        chk("正确口令 → 200", st == 200, f"HTTP {st}")
        chk("下发会话 Cookie（sl_admin）", "sl_admin=" in sc, sc[:80])
        chk("Cookie 含 HttpOnly（JS 不可读）", "HttpOnly" in sc, sc[:80])
        chk("Cookie 含 SameSite=Lax（挡跨站 POST）", "SameSite=Lax" in sc, sc[:80])
        chk("Cookie 值为随机 sid，不是口令本身", pw not in sc, sc[:60])
        chk("响应体不含口令与 sid", pw not in raw.decode("utf-8", "ignore"))

        sid1 = next((c.value for c in cj if c.name == "sl_admin"), "")
        chk("Cookie 长度像随机凭证（≥32）", len(sid1) >= 32, f"len={len(sid1)}")

        st, _, raw = request(s2.base + "/api/admin/status", opener=op)
        d = jbody(raw)
        chk("会话有效：status.authed=true", st == 200 and d.get("authed") is True, f"{d}")
        chk("status 返回过期时间（expiresAt>now）",
            int(d.get("expiresAt") or 0) > int(time.time()), f"{d.get('expiresAt')}")

        # 管理操作成功（dry=1 只预览，不动文件）
        st, _, raw = request(s2.base + "/api/delete", "POST",
                             {"lib": vid_lib, "items": [vid_rel], "dry": 1},
                             {"X-SL-Admin": "1"}, op)
        b = jbody(raw)
        chk("会话有效 · POST /api/delete dry=1 → 200 且未移动文件",
            st == 200 and b.get("dry") is True and int(b.get("moved") or 0) == 0,
            f"HTTP {st} dry={b.get('dry')} files={b.get('files')}")

        st, _, raw = request(s2.base + "/api/trash/restore", "POST",
                             {"lib": vid_lib, "items": []}, {"X-SL-Admin": "1"}, op)
        chk("会话有效 · POST /api/trash/restore → 到得了业务层（非 401/403）",
            st not in (401, 403), f"HTTP {st}")

        # 只读库：口令不能改变 readonly
        if "wework" in lib_ids:
            st, _, raw = request(s2.base + "/api/delete", "POST",
                                 {"lib": "wework", "items": ["x"], "dry": 1},
                                 {"X-SL-Admin": "1"}, op)
            chk("会话有效 · 只读库 wework → 403（口令不改 readonly）",
                st == 403, f"HTTP {st} {jbody(raw).get('error','')}")
        else:
            skip("会话有效 · 只读库 wework → 403", "本机 libraries.json 未配置 id=wework 的库")

        # CSRF / 来源校验
        st, _, raw = request(s2.base + "/api/delete", "POST",
                             {"lib": vid_lib, "items": [vid_rel], "dry": 1}, None, op)
        chk("有会话 · 缺 X-SL-Admin 头 → 403（CSRF 第一道）",
            st == 403, f"HTTP {st} {jbody(raw).get('error','')}")

        st, _, raw = request(s2.base + "/api/delete", "POST",
                             {"lib": vid_lib, "items": [vid_rel], "dry": 1},
                             {"X-SL-Admin": "1", "Origin": "http://evil.example"}, op)
        chk("有会话 · 跨站 Origin → 403（来源校验）", st == 403,
            f"HTTP {st} {jbody(raw).get('error','')}")

        st, _, raw = request(s2.base + "/api/delete", "POST",
                             {"lib": vid_lib, "items": [vid_rel], "dry": 1},
                             {"X-SL-Admin": "1",
                              "Referer": "http://evil.example/x.html"}, op)
        chk("有会话 · 跨站 Referer → 403（来源校验）", st == 403,
            f"HTTP {st} {jbody(raw).get('error','')}")

        st, _, raw = request(s2.base + "/api/delete", "POST",
                             {"lib": vid_lib, "items": [vid_rel], "dry": 1},
                             {"X-SL-Admin": "1",
                              "Origin": f"http://127.0.0.1:{s2.port}"}, op)
        chk("有会话 · 同源 Origin → 200（正常浏览器路径）", st == 200, f"HTTP {st}")

        # 会话固定：再次登录必须换新 sid
        op2, cj2 = new_session()
        request(s2.base + "/api/admin/login", "POST", {"token": pw},
                {"X-SL-Admin": "1"}, op2)
        sid2 = next((c.value for c in cj2 if c.name == "sl_admin"), "")
        chk("每次登录签发新 sid（防会话固定）", bool(sid2) and sid2 != sid1,
            f"{sid1[:8]}… vs {sid2[:8]}…")

        # 退出 → 立即失效
        st, h, raw = request(s2.base + "/api/admin/logout", "POST", None,
                             {"X-SL-Admin": "1"}, op)
        chk("退出 → 200", st == 200, f"HTTP {st}")
        chk("退出下发过期 Cookie", "Max-Age=0" in (h.get("Set-Cookie") or ""),
            (h.get("Set-Cookie") or "")[:60])
        st, _, raw = request(s2.base + "/api/admin/status", opener=op)
        chk("退出后 status.authed=false", jbody(raw).get("authed") is False, f"{jbody(raw)}")
        st, _, raw = request(s2.base + "/api/delete", "POST",
                             {"lib": vid_lib, "items": [vid_rel], "dry": 1},
                             {"X-SL-Admin": "1"}, op)
        chk("退出后再删 → 401", st == 401, f"HTTP {st}")

        # ---------------------------------------------------------
        sect("组 4 · 登录失败退避（独立实例）")
        # ---------------------------------------------------------
        s4 = Srv(9053, ["--admin-token", pw])
        ok, _ = s4.start()
        chk("退避测试实例启动成功", ok)
        codes = []
        for _ in range(6):
            st, _, _ = request(s4.base + "/api/admin/login", "POST",
                               {"token": "brute-force-guess"},
                               {"X-SL-Admin": "1"})
            codes.append(st)
        chk("前 5 次错误口令 → 401", codes[:5] == [401] * 5, str(codes))
        chk("第 6 次 → 429（退避生效）", codes[5] == 429, str(codes))
        st, _, _ = request(s4.base + "/api/admin/login", "POST", {"token": pw},
                           {"X-SL-Admin": "1"})
        chk("退避期内即使口令正确也被挡（429）", st == 429, f"HTTP {st}")

        # ---------------------------------------------------------
        sect("组 5 · 会话过期（admin.json sessionHours 极小）")
        # ---------------------------------------------------------
        exp_pw = "Zq7-exp-" + secrets.token_hex(3)
        s5 = Srv(9052, [], admin_json={"adminToken": exp_pw, "sessionHours": 0.0003})
        ok, banner5 = s5.start()
        chk("admin.json 实例启动成功（口令来源：admin.json）", ok,
            banner5.strip().splitlines()[-1] if banner5 else "")
        st, _, raw = request(s5.base + "/api/admin/status")
        d = jbody(raw)
        chk("status.source=admin.json（口令来自独立配置）",
            d.get("required") is True and d.get("source") == "admin.json", f"{d}")
        chk("启动输出不含 admin.json 里的口令", exp_pw not in banner5)

        op5, _ = new_session()
        st, _, _ = request(s5.base + "/api/admin/login", "POST", {"token": exp_pw},
                           {"X-SL-Admin": "1"}, op5)
        chk("admin.json 口令登录 → 200", st == 200, f"HTTP {st}")
        st, _, raw = request(s5.base + "/api/admin/status", opener=op5)
        chk("登录后立即 authed=true", jbody(raw).get("authed") is True, f"{jbody(raw)}")
        time.sleep(2.2)                                   # 0.0003h ≈ 1.08s
        st, _, raw = request(s5.base + "/api/admin/status", opener=op5)
        chk("会话过期后 authed=false", jbody(raw).get("authed") is False, f"{jbody(raw)}")
        st, _, raw = request(s5.base + "/api/delete", "POST",
                             {"lib": vid_lib, "items": [vid_rel], "dry": 1},
                             {"X-SL-Admin": "1"}, op5)
        chk("会话过期后再删 → 401", st == 401, f"HTTP {st}")

        # ---------------------------------------------------------
        sect("组 6 · 启动参数确实进入服务端认证状态")
        # ---------------------------------------------------------
        pwA = "Zq7-A-" + secrets.token_hex(4)
        s6 = Srv(9054, ["--admin-token", pwA])
        ok, banner6 = s6.start()
        chk("--admin-token 实例启动成功", ok)
        st, _, raw = request(s6.base + "/api/admin/status")
        d = jbody(raw)
        chk("--admin-token → status.required=true（参数真的生效）",
            d.get("required") is True, f"{d}")
        chk("--admin-token → source 标注「命令行」", d.get("source") == "命令行", f"{d}")
        st, _, _ = request(s6.base + "/api/admin/login", "POST", {"token": pwA},
                           {"X-SL-Admin": "1"})
        chk("用参数值登录 → 200（闸门用的确实是参数值）", st == 200, f"HTTP {st}")
        st, _, _ = request(s6.base + "/api/admin/login", "POST", {"token": other},
                           {"X-SL-Admin": "1"})
        chk("用别的口令登录 → 401", st == 401, f"HTTP {st}")

        # --token 兼容别名
        pwB = "Zq7-B-" + secrets.token_hex(4)
        s7 = Srv(9055, ["--token", pwB])
        ok, _ = s7.start()
        chk("--token 别名实例启动成功", ok)
        st, _, raw = request(s7.base + "/api/admin/status")
        chk("--token 别名 → required=true", jbody(raw).get("required") is True, f"{jbody(raw)}")
        st, _, _ = request(s7.base + "/api/admin/login", "POST", {"token": pwB},
                           {"X-SL-Admin": "1"})
        chk("--token 别名的值可登录 → 200", st == 200, f"HTTP {st}")

        # 别名与 --admin-token 语义完全相同：配了口令 → 匿名浏览一律 401
        st, _, _ = request(s7.base + "/api/libs", opener=anonymous())
        chk("--token 别名下匿名浏览 → 401（与 --admin-token 同语义）", st == 401, f"HTTP {st}")

        # ---------------------------------------------------------
        sect("组 7 · 日志脱敏")
        # ---------------------------------------------------------
        # 用实例 s6 的输出 + 全局 server.log
        out6 = s6.banner()
        chk("服务端 stdout/stderr 不含口令明文", pwA not in out6 and pw not in out6)
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                logtxt = f.read()
        except OSError:
            logtxt = ""
        hit = [t for t in (pw, pwA, pwB, other, exp_pw) if t in logtxt]
        chk("logs/server.log 不含任何测试口令", not hit, "命中=" + (",".join(hit) or "无"))
        chk("访问日志里的登录请求只有路径、无凭据",
            "/api/admin/login" not in logtxt or pwA not in logtxt)
        st, _, raw = request(s6.base + "/api/admin/status")
        body = raw.decode("utf-8", "ignore")
        chk("/api/admin/status 不泄露口令、不泄露 sid",
            pwA not in body and "sl_admin=" not in body)

        # 收尾：停掉最后一个实例
        s7.stop()
        s6.stop()
        s5.stop()
        s4.stop()
        s2.stop()
        s.stop()

    finally:
        cleanup_admin_json(backup)

    print("\n" + "=" * 66)
    print(f"  结果：{PASS} 通过 / {FAIL} 失败")
    if FAILED:
        print("  失败项：")
        for n in FAILED:
            print(f"    · {n}")
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
