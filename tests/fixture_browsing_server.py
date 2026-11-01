"""Isolated large-gallery fixture. No real library/config/state files are used.

python tests/fixture_browsing_server.py --port 9142
The fixture intentionally supplies an admin identity for local UI testing only.
"""
import argparse
import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S
import test_access as F


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=9142)
    parser.add_argument('--ui', action='store_true', help='Small multi-year fixture with synthetic landscape covers')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='browse-fixture-', dir=os.path.join(S.APP_DIR, 'tmp')) as root:
        F.setup_fixture(root)
        latest = datetime.datetime(2024, 10, 1, 12).timestamp()
        specs = [(f'large/IMG_{i:05}.jpg', latest, 'img', 0, None) for i in range(2401)]
        for day in range(1, 181):
            specs.extend((f'day{day}/IMG_{i:05}.jpg', latest - day * 86400, 'img', 0, None)
                         for i in range(8))
        if args.ui:
            specs = []
            for year, month, count in [(2026,10,45),(2026,9,23),(2026,8,9),(2026,3,17),(2025,12,32),(2025,6,8),(2024,11,15)]:
                for i in range(count):
                    ts = datetime.datetime(year, month, min(28, 1 + i // 2), 12).timestamp()
                    specs.append((f'{year}-{month:02}/SCENE_{i:03}.jpg', ts, 'img', 0, None))
        S.META['libs']['t'] = F.build_lib('t', '浏览测试', os.path.join(root, 'large'), specs)
        if args.ui:
            from PIL import Image, ImageDraw
            palettes = [(130,163,160),(213,163,114),(149,167,190),(153,165,132)]
            for i, (rel, *_) in enumerate(specs):
                base = palettes[i % len(palettes)]
                image = Image.new('RGB', (640,480)); draw = ImageDraw.Draw(image)
                for y in range(480):
                    draw.line((0,y,640,y), fill=tuple(min(255,int(c+(1-y/480)*45)) for c in base))
                draw.ellipse((390,65,460,135), fill=(250,225,174))
                draw.polygon([(0,310),(120,140+i%60),(290,330),(430,200),(640,320),(640,480),(0,480)],fill=tuple(int(c*.7) for c in base))
                draw.polygon([(0,410),(200,300),(360,390),(540,290),(640,370),(640,480),(0,480)],fill=tuple(int(c*.45) for c in base))
                image.save(os.path.join(root,'large',rel),'JPEG',quality=85)
        # Test-only identity: this server is loopback-only and owns synthetic images.
        S.Handler.read_scope = lambda self: ('admin', None)
        httpd = S.ThreadingHTTPServer(('127.0.0.1', args.port), S.Handler)
        print(f'READY http://127.0.0.1:{httpd.server_address[1]}', flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            for store in (S.INDEX, S.CACHE_INDEX, S.SHARES):
                store.close()


if __name__ == '__main__':
    main()
