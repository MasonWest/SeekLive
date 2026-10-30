"""Cold-cache navigation: waiting old work must never reach the decoder."""
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S
import thumb_work as W

TAB='a'*20


class DemandTests(unittest.TestCase):
    def test_epochs_are_monotonic_and_scoped(self):
        views=W.Views()
        a=views.register('admin',TAB,1)
        other=views.register('share',TAB,1)
        tab=views.register('admin','b'*20,1)
        latest=views.register('admin',TAB,2)
        self.assertTrue(a());self.assertFalse(other());self.assertFalse(tab());self.assertFalse(latest())
        self.assertTrue(views.register('admin',TAB,1)())
        self.assertFalse(latest())

    def test_invalid_and_bounded(self):
        views=W.Views(limit=2)
        self.assertIsNone(views.register('a','bad',1))
        self.assertIsNone(views.register('a',TAB,-1))
        self.assertIsNone(views.register('a',TAB,'invalid'))
        for i in range(10):views.register(str(i),TAB,1)
        self.assertEqual(len(views.latest),2)

    def test_cold_navigation_drops_old_waiters(self):
        views=W.Views()
        old=views.register('admin',TAB,1)
        gate=threading.BoundedSemaphore(1)
        gate.acquire()  # One already-running decoder, which we allow to finish.
        ran=[]
        def task(name,cancelled):
            try:
                with W.demand(cancelled), S.gen_gate():ran.append(name)
            except W.Superseded:return 'cancelled'
            return 'done'
        with patch.object(S,'_gen_sem',gate),ThreadPoolExecutor(12) as pool:
            previous=S._bg_waiting
            jobs=[pool.submit(task,'old-'+str(i),old) for i in range(8)]
            deadline=time.monotonic()+3
            while S._bg_waiting<previous+8 and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(S._bg_waiting,previous+8)
            new=views.register('admin',TAB,2)
            latest=pool.submit(task,'target-month',new)
            for job in jobs:self.assertEqual(job.result(timeout=2),'cancelled')
            gate.release()
            self.assertEqual(latest.result(timeout=2),'done')
            self.assertEqual(ran,['target-month'])
            self.assertEqual(S._bg_waiting,previous)
        self.assertTrue(gate.acquire(blocking=False));gate.release()

    def test_same_file_new_viewer_retries_obsolete_owner(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(S.APP_DIR,'tmp')) as root:
            src=os.path.join(root,'source.jpg')
            with open(src,'wb') as f:f.write(b'fake')
            lib={'id':'test','root':root};st=os.stat(src)
            views=W.Views();old=views.register('admin',TAB,1)
            gate=threading.BoundedSemaphore(1);gate.acquire()
            rendered=[]
            def render(*args):rendered.append(1);return b'ready','image/webp',1,1
            def request(cancelled):
                with W.demand(cancelled):return S.cached_image(lib,'source.jpg',src,st,'thumb',640,78,'webp')
            with patch.object(S,'CACHE_DIR',root),patch.object(S,'CACHE_INDEX',None),patch.object(S,'META',{'cache':{'minFreeGB':0}}),patch.object(S,'_gen_sem',gate),patch.object(S,'render_media',side_effect=render),ThreadPoolExecutor(2) as pool:
                a=pool.submit(request,old)
                deadline=time.monotonic()+3
                while S._bg_waiting<1 and time.monotonic()<deadline:time.sleep(.01)
                new=views.register('admin',TAB,2)
                b=pool.submit(request,new)
                with self.assertRaises(W.Superseded):a.result(timeout=2)
                gate.release()
                self.assertEqual(b.result(timeout=2)[0],b'ready')
                self.assertEqual(rendered,[1])


if __name__=='__main__':unittest.main()
