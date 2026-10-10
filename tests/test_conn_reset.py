# -*- coding: utf-8 -*-
"""
连接级异常 专项测试：客户端提前断开不该在控制台刷 Traceback
用法： python tests/test_conn_reset.py

复现方式：连上后只发半截请求行，再用 SO_LINGER=0 直接 RST。
浏览器关标签页 / 灯箱换图 / 取消预加载时就是这个样子。
期望：可控对照（基类 handle_error）会打出堆栈；本项目重写后静默，
      且正常请求不受影响。
用进程内实例（bind 到 127.0.0.1:0）测试，不碰正在运行的服务与 server.pid。
"""
import io
import os
import socket
import struct
import sys
import threading
import time
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S


def rst_once(port):
    s = socket.socket()
    s.connect(("127.0.0.1", port))
    s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n")      # 只发一半
    time.sleep(0.05)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()                                                # 直接 RST


def run_case(title, quiet):
    cls = S.ThreadingHTTPServer
    orig = cls.handle_error
    base_tb = None
    if not quiet:                     # 还原成 socketserver 基类的行为做对照
        import socketserver
        base_tb = socketserver.BaseServer.handle_error
        cls.handle_error = base_tb
    try:
        httpd = cls(("127.0.0.1", 0), S.Handler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            for _ in range(4):
                rst_once(port)
            time.sleep(0.6)
            # 正常请求必须照常工作
            import urllib.request
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            code = op.open(f"http://127.0.0.1:{port}/", timeout=10).status
        httpd.shutdown()
        out = buf.getvalue()
        tb = out.count("Traceback")
        print(f"[{title}] 正常请求 HTTP {code}｜stderr 里 Traceback 段数 = {tb}")
        if tb:
            first = [l for l in out.splitlines() if l.strip().startswith("ConnectionResetError")]
            if first:
                print("           最后一行:", first[-1].strip())
        return {"http": code, "tb": tb}
    finally:
        cls.handle_error = orig


if __name__ == "__main__":
    base = run_case("对照：基类 handle_error", quiet=False)
    new = run_case("本项目 handle_error   ", quiet=True)
    ok = new["http"] == 200 and new["tb"] == 0 and base["tb"] > 0
    print("\n结果:", "PASS" if ok else "FAIL",
          "（对照应打出堆栈、本实现应一条也不打，且正常请求 200）")
    sys.exit(0 if ok else 1)
