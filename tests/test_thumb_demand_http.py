"""Exercise viewport cancellation through real HTTP, against a fake photo library."""
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
sys.path[:0] = [os.path.dirname(__file__), os.path.dirname(os.path.dirname(__file__))]
import server as S
import test_access as F


class HttpDemandTests(unittest.TestCase):
    def test_navigation_notice_rejects_old_and_keeps_auth(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(S.APP_DIR,'tmp')) as root:
            F.setup_fixture(root)
            httpd, base=F.start_server()
            try:
                status,headers,_=F.req(base,'/api/admin/login','POST',{'token':'test-pw-123'}, {'X-SL-Admin':'1'})
                self.assertEqual(status,200)
                sid=F.set_cookie(headers,S.SESSION_COOKIE)
                cookies={S.SESSION_COOKIE:sid}
                tab='http-regression-tab-123'
                notice='/api/thumb/view?_slview='+tab+'&_slepoch=2'
                self.assertEqual(F.req(base,notice)[0],401)
                # HEAD must not advance scheduling state.
                self.assertEqual(F.req(base,notice,method='HEAD',cookies=cookies)[0],200)
                gate=threading.BoundedSemaphore(1);gate.acquire()
                photo='/thumb?lib=t&p=2024-09/IMG_0001.JPG&_slview='+tab
                rendered=[]
                def render(*args):rendered.append(1);return b'fake-webp','image/webp',1,1
                with patch.object(S,'_gen_sem',gate),patch.object(S,'render_media',side_effect=render),ThreadPoolExecutor(2) as pool:
                    old=pool.submit(F.req,base,photo+'&_slepoch=1',cookies=cookies)
                    deadline=time.monotonic()+3
                    while S._bg_waiting<1 and time.monotonic()<deadline:time.sleep(.01)
                    self.assertEqual(S._bg_waiting,1)
                    self.assertEqual(F.req(base,notice,cookies=cookies)[0],200)
                    status,hd,_=old.result(timeout=3)
                    self.assertEqual(status,409)
                    self.assertEqual(hd.get('X-Thumbnail-State'),'superseded')
                    self.assertEqual(rendered,[])
                    current=pool.submit(F.req,base,photo+'&_slepoch=2',cookies=cookies)
                    gate.release()
                    self.assertEqual(current.result(timeout=3)[0],200)
                    self.assertEqual(rendered,[1])
                    # A late old request cannot roll back the epoch, even with a disk hit.
                    self.assertEqual(F.req(base,photo+'&_slepoch=1',cookies=cookies)[0],409)
                self.assertEqual(F.req(base,photo+'&_slepoch=2')[0],401)
            finally:
                httpd.shutdown();httpd.server_close()
                for store in (S.INDEX,S.CACHE_INDEX,S.SHARES):store.close()


if __name__=='__main__':unittest.main()
