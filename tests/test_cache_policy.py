"""Isolated capacity, lease and manifest migration regressions."""
import hashlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_db
import cache_policy as P
import server as S


def entry(key, kind, size, access=1, created=1):
    return dict(key=key, kind=kind, bytes=size, created=created, last_access=access)


class PolicyTests(unittest.TestCase):
    def cfg(self, **kw):
        return P.config(dict(maxGB=100/P.GIB, minFreeGB=0, graceSeconds=0, **kw))

    def test_validation(self):
        for raw in ({"maxGB": -1}, {"maxGB": float('nan')}, {"lowWatermark": 1},
                    {"shares": {"trans": 2}}, {"checkIntervalSeconds": 0}):
            with self.assertRaises(ValueError): P.config(raw)

    def test_disabled_does_not_evict(self):
        self.assertEqual(P.choose_victims([entry('a','trans',999)],999,0,P.config())[0], [])

    def test_borrow_unused_budget(self):
        self.assertEqual(P.choose_victims([entry('a','trans',90)],90,999,self.cfg())[0], [])

    def test_lru_low_water_and_thumbnail_reserve(self):
        rows = [entry('thumb','thumb',20),entry('photo','photo',40),
                entry('new','trans',30,10),entry('old','trans',30,2)]
        victims, need = P.choose_victims(rows,120,999,self.cfg(),now=100)
        self.assertEqual(need,40)
        self.assertEqual([v['key'] for v in victims],['old','photo'])

    def test_grace_and_active_exclusion(self):
        rows = [dict(entry('busy','trans',100),eligible=False),entry('new','photo',100,99,99)]
        cfg=self.cfg();cfg['graceSeconds']=10
        self.assertEqual(P.choose_victims(rows,200,999,cfg,now=100)[0],[])

    def test_low_disk_reclaims_even_below_maximum(self):
        cfg=self.cfg();cfg['minFreeGB']=20/P.GIB
        victims, need=P.choose_victims([entry('a','trans',30)],30,5,cfg,now=100)
        self.assertEqual(need,15)
        self.assertEqual(len(victims),1)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.path.join(S.APP_DIR,'tmp'))
        self.root=os.path.join(self.tmp.name,'cache');os.makedirs(self.root)
        self.store=cache_db.CacheIndex(os.path.join(self.tmp.name,'cache.db'))
        self.gov=P.Governor(self.root,self.store)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def write(self,name='a',size=10):
        path=os.path.join(self.root,name)
        with open(path,'wb') as f:f.write(b'x'*size)
        return path

    def test_lease_protects_output_and_temporary_until_request_finishes(self):
        path=self.write();tmp=self.write('a.123.tmp')
        with self.gov.request():
            with self.gov.pin(path):pass
            self.assertFalse(self.gov.remove(path))
            self.assertFalse(self.gov.remove(tmp))
        self.assertTrue(self.gov.remove(path))
        self.assertTrue(self.gov.remove(tmp))

    def test_path_boundary_and_replaced_file(self):
        outside=os.path.join(self.tmp.name,'original.jpg')
        with open(outside,'wb') as f:f.write(b'original')
        self.assertFalse(self.gov.remove(outside))
        path=self.write();st=os.stat(path)
        self.write(size=20)
        self.assertFalse(self.gov.remove(path,st.st_size,st.st_mtime_ns))
        self.assertTrue(os.path.exists(outside))

    def test_reserved_space_is_coordinated(self):
        cfg=P.config({'minFreeGB':0})
        with patch.object(self.gov,'disk_free',return_value=100):
            with self.gov.reserve(70,cfg):
                with self.assertRaises(P.CacheSpaceError):
                    with self.gov.reserve(40,cfg):pass
            self.assertEqual(self.gov.reserved,0)

    def test_access_batch_and_created_time_preserved(self):
        self.store.record('a','t','a',1,1,'thumb','thumb|webp|640',1,created=1)
        self.gov.touch('a');self.gov.flush()
        self.store.record('a','t','a',1,1,'thumb','thumb|webp|640',1)
        row=self.store.all_entries()[0]
        self.assertEqual(row['created'],1)
        self.assertGreater(row['last_access'],1)

    def test_v1_migration_is_idempotent(self):
        db=os.path.join(self.tmp.name,'old.db')
        conn=sqlite3.connect(db)
        conn.executescript(cache_db._SCHEMA)
        conn.execute("INSERT INTO entries(key,created) VALUES('old',42)")
        conn.commit();conn.close()
        for _ in range(2):
            store=cache_db.CacheIndex(db)
            self.assertEqual(store.all_entries()[0]['last_access'],42)
            self.assertEqual(store.get_meta('schema_version'),'2')
            store.close()

    def test_server_capacity_evicts_only_cache_and_preserves_active(self):
        src=os.path.join(self.tmp.name,'original.jpg')
        with open(src,'wb') as f:f.write(b'original')
        st=os.stat(src)
        meta={'libs':{'t':{'id':'t','root':self.tmp.name,'exists':True}},
              'cache':{'maxGB':100/P.GIB,'minFreeGB':0,'graceSeconds':0,'temporaryGraceSeconds':0}}
        with patch.object(S,'CACHE_DIR',self.root),patch.object(S,'CACHE_INDEX',self.store),patch.object(S,'META',meta):
            paths=[]
            for i,kind,size in ((0,'thumb',20),(1,'photo',40),(2,'trans',60)):
                key=hashlib.sha256(str(i).encode()).hexdigest()
                path=S.cache_file_for(key,kind);os.makedirs(os.path.dirname(path),exist_ok=True)
                with open(path,'wb') as f:f.write(b'x'*size)
                self.store.record(key,'t','original.jpg',st.st_mtime,st.st_size,kind,
                                  'thumb|webp|640' if kind=='thumb' else kind,size,created=1)
                paths.append(path)
            S._scan_cache_invalidate()
            gov=S.cache_governor()
            with gov.pin(paths[2]):
                result=S.cache_capacity_apply()
                self.assertTrue(os.path.exists(paths[2]))
            self.assertTrue(os.path.isfile(src))
            self.assertTrue(os.path.exists(paths[0]))
            # A later pass can finish reclaiming the previously active video.
            S._scan_cache_invalidate()
            result=S.cache_capacity_apply()
            self.assertLessEqual(result['usageBytes'],80)

    def test_fresh_unrecorded_and_active_tmp_gc_protection(self):
        with patch.object(S,'CACHE_DIR',self.root),patch.object(S,'CACHE_INDEX',self.store),patch.object(S,'META',{'libs':{}}):
            path=self.write('orphan.tmp')
            S._scan_cache_invalidate()
            self.assertEqual(S.cache_gc_plan(full=True)['orphan']['files'],0)
            os.utime(path,(1,1))
            with S.cache_governor().pin(path):
                self.assertEqual(S.cache_gc_apply(full=True)['deleted'],0)
            self.assertEqual(S.cache_gc_apply(full=True)['deleted'],1)

    def test_image_generation_survives_concurrent_gc(self):
        src=os.path.join(self.tmp.name,'original.jpg')
        with open(src,'wb') as f:f.write(b'original')
        st=os.stat(src)
        lib={'id':'t','root':self.tmp.name}
        meta={'libs':{'t':lib},'cache':{'minFreeGB':0,'temporaryGraceSeconds':0}}
        key=S.cache_key('original.jpg',st.st_mtime,st.st_size,'thumbwebp640',lib_id='t')[2]
        with patch.object(S,'CACHE_DIR',self.root),patch.object(S,'CACHE_INDEX',self.store),patch.object(S,'META',meta):
            cp=S.cache_file_for(key,'thumb')
            def render(*args):
                os.makedirs(os.path.dirname(cp),exist_ok=True)
                with open(cp+'.test.tmp','wb') as f:f.write(b'partial')
                self.assertEqual(S.cache_gc_apply(full=True)['deleted'],0)
                return b'complete','image/webp',1,1
            with patch.object(S,'render_media',side_effect=render):
                result=S.cached_image(lib,'original.jpg',src,st,'thumb',640,78,'webp')
            self.assertEqual(result[0],b'complete')
            self.assertTrue(os.path.isfile(cp))
            self.assertTrue(os.path.isfile(src))


if __name__=='__main__':unittest.main()
