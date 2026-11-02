"""Installed-app assets must load anonymously, including through the public tunnel."""
import http.client
import io
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.parse

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class PwaIconsTest(unittest.TestCase):
    def test_installer_assets_without_login(self):
        previous = server.ACCESS_PROTECT
        server.ACCESS_PROTECT = True
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            for headers in ({}, {'X-Forwarded-Proto': 'https'}):
                conn = http.client.HTTPConnection(*httpd.server_address, timeout=5)
                try:
                    conn.request('GET', '/manifest.webmanifest', headers=headers)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    manifest = json.loads(response.read())
                    assets = [(icon['src'], icon['sizes']) for icon in manifest['icons']]
                    assets.append(('icons/apple-touch-icon.png', '180x180'))
                    for source, size in assets:
                        with self.subTest(headers=headers, source=source):
                            conn.request('GET', '/' + source, headers=headers)
                            response = conn.getresponse()
                            body = response.read()
                            self.assertEqual(response.status, 200)
                            filename = urllib.parse.urlsplit(source).path
                            self.assertEqual(body, (Path(server.WEB_DIR) / filename).read_bytes())
                            if size != 'any':
                                self.assertEqual(response.getheader('Content-Type'), 'image/png')
                                with Image.open(io.BytesIO(body)) as image:
                                    self.assertEqual(image.size, tuple(map(int, size.split('x'))))
                                    self.assertEqual(image.convert('RGBA').getchannel('A').getextrema(), (255, 255))
                    # Only the listed public icons are exposed, not arbitrary files.
                    conn.request('GET', '/icons/private.png', headers=headers)
                    response = conn.getresponse()
                    response.read()
                    self.assertIn(response.status, (401, 403))
                finally:
                    conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            server.ACCESS_PROTECT = previous


if __name__ == '__main__':
    unittest.main()
