import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from vision_db import VisionDB,Conflict


class VisionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='tmp/vision-lab');self.path=Path(self.temp.name)/'vision.db';self.store=VisionDB(self.path)
        self.catalog=dict(groups=[dict(id='P1',members=['F1'])],observations=[dict(id=oid,sample='sample',kind=kind) for oid,kind in [('F1','person'),('C1','cat'),('C2','cat'),('D1','dog'),('B1','bird')]],confirmed=[])
        self.state=dict(dataset_id='d',group_names={'P1':'Person'},face_checks={'F1':'keep'},animal_checks={},animal_entities={'PET1':dict(id='PET1',kind='cat',name='Cat',status='followed',members=['C1'])})
        self.store.save('d',self.state,0,catalog=self.catalog)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_reopen_preserves_structured_memberships(self):
        other=VisionDB(self.path)
        try:
            self.assertEqual(other.load('d')['state'],self.state)
            self.assertEqual(other.db.execute('SELECT count(*) FROM memberships').fetchone()[0],2)
            self.assertEqual(other.db.execute('PRAGMA journal_mode').fetchone()[0],'wal')
        finally:other.close()
    def test_stale_tab_cannot_overwrite_newer_state(self):
        changed=copy.deepcopy(self.state);changed['group_names']['P1']='New'
        self.store.save('d',changed,1)
        with self.assertRaises(Conflict):self.store.save('d',self.state,1)
        self.assertEqual(self.store.load('d')['state']['group_names']['P1'],'New')
    def test_species_conflict_rolls_back_whole_transaction(self):
        changed=copy.deepcopy(self.state);changed['animal_entities']['PET1']['members'].append('B1')
        with self.assertRaises(ValueError):self.store.save('d',changed,1)
        self.assertEqual(self.store.load('d')['revision'],1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memberships').fetchone()[0],2)
    def test_manual_class_correction_can_archive_bird(self):
        changed=copy.deepcopy(self.state);changed['animal_checks']['C1']='bird';changed['animal_entities']['PET1']['kind']='bird'
        self.store.save('d',changed,1)
        self.assertEqual(self.store.db.execute("SELECT kind FROM entities WHERE id='PET1'").fetchone()[0],'bird')
    def test_duplicate_assignment_rejected(self):
        changed=copy.deepcopy(self.state);changed['animal_entities']['PET2']=copy.deepcopy(changed['animal_entities']['PET1'])
        changed['animal_entities']['PET2']['id']='PET2'
        with self.assertRaises(ValueError):self.store.save('d',changed,1)
    def test_ignoring_machine_group_does_not_confirm_identity(self):
        changed=copy.deepcopy(self.state);changed['animal_entities']['PET1'].update(status='unfollowed',confirmed=False)
        self.store.save('d',changed,1)
        self.assertEqual(self.store.db.execute("SELECT confirmation FROM memberships WHERE observation='C1'").fetchone()[0],'candidate')
    def test_bulk_archive_and_undo_preserve_observations(self):
        changed=copy.deepcopy(self.state);changed['animal_entities']['PET1']['members'].append('C2')
        self.store.save('d',changed,1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memberships WHERE entity='PET1'").fetchone()[0],2)
        self.store.save('d',self.state,2,source='undo')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memberships WHERE entity='PET1'").fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM observations').fetchone()[0],5)
    def test_job_priority_lease_and_stale_worker(self):
        self.store.enqueue('slow','scan',{},0);self.store.enqueue('fast','scan',{},10)
        first=self.store.claim('owner-a');self.assertEqual(first['id'],2)
        with self.assertRaises(Conflict):self.store.finish(first['id'],'owner-b')
        with self.store.db:self.store.db.execute('UPDATE jobs SET lease_until=0 WHERE id=2')
        next_job=self.store.claim('owner-b');self.assertEqual(next_job['id'],2)
        with self.assertRaises(Conflict):self.store.finish(2,'owner-a')
        self.store.finish(2,'owner-b');self.assertEqual(self.store.claim('owner-c')['id'],1)
    def test_refuse_unrelated_database(self):
        path=Path(self.temp.name)/'index.db';db=sqlite3.connect(path);db.execute('CREATE TABLE files(id INTEGER)');db.execute('PRAGMA user_version=1');db.close()
        with self.assertRaises(ValueError):VisionDB(path)
        db=sqlite3.connect(path)
        try:self.assertEqual(db.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall(),[('files',)])
        finally:db.close()
    def test_feature_batch_rejects_nonfinite_without_partial_insert(self):
        with self.assertRaises(ValueError):self.store.store_features('d',[('F1','good',[1.,0.]),('C1','bad',[float('nan')])])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM features').fetchone()[0],0)


if __name__=='__main__':unittest.main()
