import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from vision_db import VisionDB,Conflict
from vision_pipeline import synchronize,publish,references,control,status,digest,refresh_pet_review_policy,ReferenceCache
from vision_worker import run


class VisionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='tmp/vision-lab');self.root=Path(self.temp.name);self.library=self.root/'photos';self.library.mkdir();self.photo=self.library/'image.jpg';self.photo.write_bytes(b'fixture')
        self.index=self.root/'index.db';connection=sqlite3.connect(self.index)
        connection.executescript('CREATE TABLE libs(lib_id TEXT PRIMARY KEY,root TEXT,root_exists INTEGER);CREATE TABLE files(lib_id TEXT,rel TEXT,kind TEXT);')
        connection.execute('INSERT INTO libs VALUES(?,?,1)',('lib',str(self.library.resolve())));connection.execute("INSERT INTO files VALUES('lib','image.jpg','img')");connection.commit();connection.close()
        self.path=self.root/'vision.db';self.store=VisionDB(self.path)
        self.state=dict(dataset_id='d',group_names={'P':'Name'},face_checks={'F':'keep'})
        self.catalog=dict(groups=[dict(id='P',members=['F'])],observations=[dict(id='F',sample='reference',kind='person')],confirmed=[])
        self.store.save('d',self.state,0,catalog=self.catalog);self.store.store_feature('d','F','sface-2021dec',[1.,0.])
        with self.store.db:self.store.db.execute("UPDATE vision_control SET runtime='runtime',heartbeat=?,model='model' WHERE id=1",(time.time(),))
    def tearDown(self):self.store.close();self.temp.cleanup()
    def job(self):
        synchronize(self.store,self.index,'model');return self.store.claim('owner',kind='vision')
    def observations(self,job):return [dict(id='NEW',sample=job['payload']['source']['id'],kind='person',box=[0,0,1,1],vector=[1.,0.],source_version=[job['payload']['source']['size'],job['payload']['source']['mtime_ns']])]
    def test_reference_clock_cross_connection_and_unrelated_updates(self):
        cache=ReferenceCache();initial=cache.get(self.store,'d')
        other=VisionDB(self.path)
        try:
            generation=lambda:self.store.db.execute('SELECT generation FROM vision_reference_clock').fetchone()[0]
            before=generation()
            with other.db:other.db.execute('UPDATE vision_control SET heartbeat=heartbeat+1')
            self.assertEqual(generation(),before)
            self.assertIs(cache.get(self.store,'d'),initial)
            import array
            with other.db:other.db.execute("UPDATE features SET vector=? WHERE dataset='d' AND observation='F'",(array.array('f',[0.,1.]).tobytes(),))
            self.assertGreater(generation(),before)
            self.assertNotEqual(cache.get(self.store,'d'),initial)
        finally:other.close()
    def test_completed_scan_waits_for_review_batches_before_requeue(self):
        job=self.job();publish(self.store,job,'owner','runtime',self.observations(job),[])
        import array
        with self.store.db:self.store.db.execute("UPDATE features SET vector=? WHERE observation='F'",(array.array('f',[0.,1.]).tobytes(),))
        self.assertEqual(synchronize(self.store,self.index,'model',review_delay=60)['queued'],0)
        with self.store.db:self.store.db.execute('UPDATE review_state SET updated=updated-61')
        self.assertEqual(synchronize(self.store,self.index,'model',review_delay=60)['queued'],1)
    def test_unreviewed_species_is_repaired_from_current_scan(self):
        from vision_pipeline import repair_unreviewed_species
        job=self.job();obs=self.observations(job);obs[0]['kind']='cat'
        with self.store.db:self.store.db.execute("INSERT INTO entities VALUES('d','CAT','cat','Cat','followed')")
        publish(self.store,job,'owner','runtime',obs,[dict(observation='NEW',entity='CAT',score=.9)])
        with self.store.db:self.store.db.execute("UPDATE observations SET kind='dog' WHERE id='NEW'")
        before=self.store.load('d')
        self.assertEqual(repair_unreviewed_species(self.store),1)
        self.assertEqual(self.store.db.execute("SELECT kind FROM observations WHERE id='NEW'").fetchone()[0],'cat')
        self.assertEqual(self.store.load('d'),before)
        self.assertEqual(repair_unreviewed_species(self.store),0)
    def test_incremental_dedupe_and_file_model_invalidation(self):
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],1)
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],0)
        self.photo.write_bytes(b'new contents')
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],1)
        self.assertEqual(synchronize(self.store,self.index,'new-model')['queued'],1)
    def test_restoring_previous_reference_requeues_completed_job(self):
        job=self.job();publish(self.store,job,'owner','runtime',self.observations(job),[])
        changed=copy.deepcopy(self.state);changed['face_checks']['F']='reject';self.store.save('d',changed,1)
        second_sync=synchronize(self.store,self.index,'model');self.assertEqual(second_sync['queued'],1)
        second=self.store.claim('owner2',kind='vision');publish(self.store,second,'owner2','runtime',self.observations(second),[])
        self.store.save('d',self.state,2)
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],1)
    def test_v1_upgrade_preserves_names_and_confirmations(self):
        before=self.store.load('d');self.store.db.executescript('DROP TABLE vision_scans;DROP TABLE vision_suggestions;DROP TABLE vision_face_cache;DROP TABLE vision_media;DROP TABLE vision_control;PRAGMA user_version=1;')
        self.store.close();self.store=VisionDB(self.path)
        self.assertEqual(self.store.load('d'),before)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0],2)
    def test_publish_keeps_manual_state_and_only_creates_candidate(self):
        job=self.job();before=self.store.load('d')
        publish(self.store,job,'owner','runtime',self.observations(job),[dict(observation='NEW',entity='P',score=.9)])
        self.assertEqual(self.store.load('d'),before)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memberships WHERE observation='NEW'").fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM vision_suggestions').fetchone()[0],1)
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],0)
    def test_new_detection_box_on_archived_photo_does_not_duplicate_identity_review(self):
        job=self.job();source=job['payload']['source']['id']
        with self.store.db:self.store.db.execute("UPDATE observations SET sample=? WHERE dataset='d' AND id='F'",(source,))
        publish(self.store,job,'owner','runtime',self.observations(job),[dict(observation='NEW',entity='P',score=.9)])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM vision_suggestions').fetchone()[0],0)
    def test_reference_refresh_keeps_unreviewed_candidates_and_explicit_rejections(self):
        import array
        job=self.job();publish(self.store,job,'owner','runtime',self.observations(job),[dict(observation='NEW',entity='P',score=.9)])
        with self.store.db:self.store.db.execute("UPDATE features SET vector=? WHERE observation='F'",(array.array('f',[0.,1.]).tobytes(),))
        synchronize(self.store,self.index,'model')
        self.assertEqual(self.store.db.execute("SELECT status FROM vision_suggestions WHERE observation='NEW'").fetchone()[0],'pending')
        with self.store.db:self.store.db.execute("UPDATE vision_suggestions SET status='rejected'")
        self.photo.write_bytes(b'changed fixture')
        synchronize(self.store,self.index,'model')
        self.assertEqual(self.store.db.execute("SELECT status FROM vision_suggestions WHERE observation='NEW'").fetchone()[0],'rejected')
    def test_newly_indexed_photo_is_queued_and_matched_without_silent_archive(self):
        first=self.job();publish(self.store,first,'owner','runtime',self.observations(first),[])
        added=self.library/'added.jpg';added.write_bytes(b'new fixture')
        index=sqlite3.connect(self.index);index.execute("INSERT INTO files VALUES('lib','added.jpg','img')");index.commit();index.close()
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],1)
        job=self.store.claim('new-owner',kind='vision');observation=self.observations(job);observation[0]['id']='NEW-ADDED'
        before=self.store.load('d');publish(self.store,job,'new-owner','runtime',observation,[dict(observation='NEW-ADDED',entity='P',score=.95)])
        self.assertEqual(self.store.load('d'),before)
        self.assertEqual(self.store.db.execute("SELECT status FROM vision_suggestions WHERE observation='NEW-ADDED'").fetchone()[0],'pending')
        self.assertFalse(self.store.db.execute("SELECT 1 FROM memberships WHERE observation='NEW-ADDED'").fetchone())
    def prepare_old_pet_scan(self,rejected=False):
        state=copy.deepcopy(self.state);state['animal_entities']={'PET':dict(id='PET',kind='cat',name='Cat',status='followed',members=['C1','C2'])}
        catalog=copy.deepcopy(self.catalog);catalog['observations'] += [dict(id=oid,sample='ref-'+oid,kind='cat') for oid in ('C1','C2')]
        self.store.save('d',state,1,catalog=catalog)
        self.store.store_feature('d','C1','pet-small',[.7,(1-.7**2)**.5]);self.store.store_feature('d','C2','pet-small',[.68,(1-.68**2)**.5])
        job=self.job();observation=self.observations(job)[0];observation.update(id='CAT-NEW',kind='cat',vector=[1.,0.])
        publish(self.store,job,'owner','runtime',[observation],[])
        _,refs=references(self.store,'d');old=digest(refs)
        with self.store.db:
            self.store.db.execute('UPDATE vision_scans SET reference=?',(old,))
            if rejected:self.store.db.execute("INSERT INTO vision_suggestions VALUES('d','CAT-NEW','PET',?,?,?,?, 'rejected')",(job['payload']['source']['id'],job['payload']['version'],old,.7))
    def test_pet_policy_refresh_uses_cached_observations_and_is_idempotent(self):
        self.prepare_old_pet_scan();before=self.store.load('d')
        result=refresh_pet_review_policy(self.store);self.assertEqual(result['scans_updated'],1);self.assertEqual(result['pending_pets'],1)
        self.assertEqual(self.store.load('d'),before)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM memberships WHERE observation='CAT-NEW'").fetchone())
        self.assertEqual(refresh_pet_review_policy(self.store)['scans_updated'],0)
    def test_pet_policy_refresh_preserves_explicit_rejection(self):
        self.prepare_old_pet_scan(rejected=True);refresh_pet_review_policy(self.store)
        self.assertEqual(self.store.db.execute("SELECT status FROM vision_suggestions WHERE observation='CAT-NEW'").fetchone()[0],'rejected')
    def test_reference_cache_invalidates_vector_and_source_changes(self):
        cache=ReferenceCache();before=cache.get(self.store,'d')
        self.assertEqual(cache.get(self.store,'d'),before)
        with self.store.db:
            import array
            self.store.db.execute('UPDATE features SET vector=? WHERE observation=?',(array.array('f',[0.,1.]).tobytes(),'F'))
        self.assertNotEqual(cache.get(self.store,'d')[0],before[0])
        changed=copy.deepcopy(self.state);changed['person_interest']={'P':'unfollowed'};self.store.save('d',changed,1)
        self.assertEqual(cache.get(self.store,'d')[1],[])
    def test_cached_publication_rechecks_human_references(self):
        job=self.job();cache=ReferenceCache();cache.get(self.store,'d')
        changed=copy.deepcopy(self.state);changed['person_interest']={'P':'unfollowed'};self.store.save('d',changed,1)
        with self.assertRaises(Conflict):publish(self.store,job,'owner','runtime',self.observations(job),[],reference_cache=cache)
    def test_pending_reference_updates_coalesce_instead_of_multiplying(self):
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],1)
        changed=copy.deepcopy(self.state);changed['person_interest']={'P':'unfollowed'};self.store.save('d',changed,1)
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM jobs WHERE kind='vision' AND status='pending'").fetchone()[0],1)
        job=self.store.claim('owner',kind='vision');self.assertEqual(job['payload']['reference'],references(self.store,'d')[0])
    def test_current_scan_retires_redundant_older_reference_jobs(self):
        job=self.job();publish(self.store,job,'owner','runtime',self.observations(job),[])
        payload=copy.deepcopy(job['payload']);payload['reference']='obsolete'
        with self.store.db:self.store.db.execute("INSERT INTO jobs(key,kind,payload,status,priority) VALUES('old-ref','vision',?,'pending',0)",(json.dumps(payload),))
        self.assertEqual(synchronize(self.store,self.index,'model')['queued'],0)
        self.assertEqual(self.store.db.execute("SELECT status FROM jobs WHERE key='old-ref'").fetchone()[0],'done')
    def test_old_runtime_or_changed_reference_cannot_publish(self):
        job=self.job()
        with self.assertRaises(Conflict):publish(self.store,job,'owner','old-runtime',self.observations(job),[])
        changed=copy.deepcopy(self.state);changed['face_checks']['F']='reject';self.store.save('d',changed,1)
        with self.assertRaises(Conflict):publish(self.store,job,'owner','runtime',self.observations(job),[])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM observations WHERE id='NEW'").fetchone()[0],0)
    def test_source_change_discards_results(self):
        job=self.job();self.photo.write_bytes(b'changed')
        with self.assertRaises(ValueError):publish(self.store,job,'owner','runtime',self.observations(job),[])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM vision_scans').fetchone()[0],0)
    def test_offline_library_preserves_human_records(self):
        self.job();connection=sqlite3.connect(self.index);connection.execute('UPDATE libs SET root_exists=0');connection.commit();connection.close()
        synchronize(self.store,self.index,'model')
        self.assertEqual(self.store.load('d')['state'],self.state)
        self.assertEqual(status(self.store)['media'],0)
    def test_scan_requests_incremental_index_work_separately(self):
        control(self.store,'scan');control(self.store,'scan')
        self.assertIsNone(self.store.claim('inference',kind='vision'))
        job=self.store.claim('coordinator',kind='vision-index')
        self.assertIsNotNone(job)
        self.assertIsNone(self.store.claim('another-coordinator',kind='vision-index'))
    def test_same_root_alias_does_not_mix_library_identity(self):
        connection=sqlite3.connect(self.index);connection.execute('INSERT INTO libs VALUES(?,?,1)',('alias',str(self.library.resolve())));connection.execute("INSERT INTO files VALUES('alias','image.jpg','img')");connection.commit();connection.close()
        synchronize(self.store,self.index,'model')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM vision_media').fetchone()[0],2)
    def test_reference_change_reuses_full_analysis_and_pause_yields(self):
        self.job();self.store.release(1,'owner');control(self.store,'pause');calls=[]
        class FakeEngine:
            def __init__(inner,*args):pass
            def analyse(inner,source,hint,idle,legacy):calls.append(1);return [dict(id='NEW',sample=source['id'],kind='person',box=[0,0,1,1],vector=[1.,0.],source_version=[source['size'],source['mtime_ns']])]
        def wait_done(number):
            until=time.time()+5
            while time.time()<until:
                if self.store.db.execute("SELECT count(*) FROM jobs WHERE kind='vision' AND status='done'").fetchone()[0]>=number:return
                time.sleep(.03)
            self.fail('Worker did not checkpoint in time')
        with patch('vision_worker.model_versions',return_value={'full':'model','face':'face'}),patch('vision_worker.Engine',FakeEngine),patch('vision_worker.match',return_value=[]):
            thread=threading.Thread(target=run,args=(self.path,self.root,self.root/'assets','runtime'));thread.start()
            try:
                time.sleep(.3);self.assertEqual(calls,[])
                control(self.store,'resume')
                with self.store.db:self.store.db.execute('UPDATE vision_control SET foreground_until=?',(time.time()+.4,))
                time.sleep(.2);self.assertEqual(calls,[])
                wait_done(1);self.assertEqual(len(calls),1)
                self.store.store_feature('d','F','sface-2021dec',[1.,0.])
                # Change reference eligibility rather than decoding the image again.
                changed=copy.deepcopy(self.state);changed['person_interest']={'P':'unfollowed'};self.store.save('d',changed,1)
                synchronize(self.store,self.index,'model');wait_done(2);self.assertEqual(len(calls),1)
            finally:
                with self.store.db:self.store.db.execute("UPDATE vision_control SET runtime='stopped'")
                thread.join(3);self.assertFalse(thread.is_alive())


if __name__=='__main__':unittest.main()
