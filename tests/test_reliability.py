"""Isolated regressions: scan failures, cache identity and concurrent generation."""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S
import index_db

class ReliabilityTests(unittest.TestCase):
    def test_cache_precision(self):
        self.assertNotEqual(S.cache_key('a.jpg', 100.1, 10, 'thumb'), S.cache_key('a.jpg', 100.9, 10, 'thumb'))

    def test_single_flight_result_and_error(self):
        for fail in (False, True):
            baseline = S._stats.get('singleflight_wait', 0)
            entered, release = threading.Event(), threading.Event()
            calls = []
            def produce():
                calls.append(1)
                entered.set()
                release.wait(5)
                if fail:
                    raise ValueError('synthetic failure')
                return 'ready'
            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(S.single_flight, 'regression', produce)
                self.assertTrue(entered.wait(5))
                second = pool.submit(S.single_flight, 'regression', produce)
                # Observe the waiter entering future.result without timing assumptions.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if S._stats.get('singleflight_wait', 0) > baseline:
                        break
                    time.sleep(.01)
                release.set()
                if fail:
                    for future in (first, second):
                        with self.assertRaises(ValueError): future.result()
                else:
                    self.assertEqual(first.result()[0], 'ready')
                    self.assertEqual(second.result()[0], 'ready')
            self.assertEqual(len(calls), 1)
        self.assertEqual(S.single_flight('regression', lambda: 'retry')[0], 'retry')

    def test_transcode_deduplicates(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(S.APP_DIR, 'tmp')) as tmp:
            src = os.path.join(tmp, 'clip.mp4')
            with open(src, 'wb') as f: f.write(b'fake')
            entered, release = threading.Event(), threading.Event()
            baseline = S._stats.get('singleflight_wait', 0)
            calls = []
            def convert(*args):
                calls.append(args)
                entered.set()
                release.wait(5)
                return ('converted.mp4', False)
            with patch.object(S, 'CACHE_DIR', tmp), patch.object(S, '_build_transcode', side_effect=convert):
                with ThreadPoolExecutor(2) as pool:
                    a = pool.submit(S.build_transcode, src)
                    self.assertTrue(entered.wait(5))
                    b = pool.submit(S.build_transcode, src)
                    deadline = time.monotonic() + 5
                    while S._stats.get('singleflight_wait', 0) <= baseline and time.monotonic() < deadline:
                        time.sleep(.01)
                    release.set()
                    self.assertEqual(a.result(), b.result())
            self.assertEqual(len(calls), 1)

    def test_incomplete_scan_preserves_index(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(S.APP_DIR, 'tmp')) as tmp:
            root = os.path.join(tmp, 'photos')
            sub = os.path.join(root, 'sub')
            os.makedirs(sub)
            photo = os.path.join(sub, 'a.jpg')
            with open(photo, 'wb') as f: f.write(b'synthetic')
            index = index_db.Index(os.path.join(tmp, 'index.db'))
            lib = {'id': 'regression', 'name': 'test', 'root': root}
            original = os.scandir
            def denied(path):
                if path == sub: raise PermissionError('synthetic')
                return original(path)
            try:
                with patch.object(S, 'INDEX', index), patch.object(S, 'USE_TS_INFER', False):
                    S.scan_library(lib, report=False)
                    for full in (False, True):
                        with patch.object(S.os, 'scandir', side_effect=denied):
                            with self.assertRaises(OSError): S.scan_library(lib, full=full, report=False)
                        self.assertEqual(len(index.all_files('regression')), 1)
                        self.assertTrue(lib['stale'])
                        self.assertTrue(os.path.isfile(photo))
            finally:
                index.close()

if __name__ == '__main__': unittest.main()
