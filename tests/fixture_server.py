#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeekLive · 浏览器 E2E 用的**夹具服务**
=====================================

复用 `tests/test_access.py` 的 `setup_fixture()`：一套**临时、隔离**的假图库
（不碰真实照片目录、不写真实 data/shares.db），再起一个真实的
`ThreadingHTTPServer + Handler`，供 `tests/e2e/qa_share.js` 用真浏览器驱动。

启动后会打印一行 `READY <base-url>`（供父进程等待），随后一直服务。

    python tests/fixture_server.py --port 9137 --token test-pw-123
"""

import os
import sys
import argparse
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
for p in (APP, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import server            # noqa: E402
import test_access as fx  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9137)
    ap.add_argument("--token", default="test-pw-123")
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="seeklive_fixture_")
    fx.setup_fixture(tmp, token=a.token)

    httpd = server.ThreadingHTTPServer(("127.0.0.1", a.port), server.Handler)
    port = httpd.server_address[1]
    print(f"READY http://127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
