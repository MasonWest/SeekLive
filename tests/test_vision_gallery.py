import copy
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from vision_db import VisionDB,Conflict
from vision_gallery import summary,photos,decide,image


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='tmp/vision-lab');self.root=Path(self.temp.name);self.library=self.root/'photos';self.library.mkdir()
        self.photo=self.library/'photo.jpg';Image.new('RGB',(80,100),(30,80,120)).save(self.photo);stat=self.photo.stat();self.stamp=json.dumps([stat.st_size,stat.st_mtime_ns],separators=(',',':'))
        self.store=VisionDB(self.root/'vision.db')
        state=dict(dataset_id='d',group_names={'P':'Name'},face_checks={'F':'keep'},animal_entities={'PET':dict(id='PET',kind='cat',name='Cat',status='followed',members=['C'])})
        catalog=dict(groups=[dict(id='P',members=['F'])],observations=[dict(id='F',sample='m',kind='person'),dict(id='C',sample='m',kind='cat')],confirmed=[])
        self.store.save('d',state,0,catalog=catalog)
        with self.store.db:
            self.store.db.execute('INSERT INTO vision_media VALUES(?,?,?,?,?,?,1)',('m','lib','photo.jpg',str(self.library.resolve()),stat.st_size,stat.st_mtime_ns))
            for oid,kind,entity in [('N1','person','P'),('N2','person','P'),('NC','cat','PET')]:
                metadata=dict(id=oid,sample='m',kind=kind,box=[.1,.1,.7,.7],source_version=[stat.st_size,stat.st_mtime_ns])
                self.store.db.execute('INSERT INTO observations VALUES(?,?,?,?,?)',('d',oid,'m',kind,json.dumps(metadata)))
                self.store.db.execute('INSERT INTO vision_suggestions VALUES(?,?,?,?,?,?,?,?)',('d',oid,entity,'m',self.stamp,'refs',.9,'pending'))
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_live_counts_and_pages_are_not_prototype_counts(self):
        result=summary(self.store,'d');self.assertEqual(result['pending'],3)
        self.assertEqual(photos(self.store,'d','P',limit=1)['total'],2)
        self.assertEqual(len(photos(self.store,'d','P',limit=1)['items']),1)
    def test_confirm_moves_photos_into_identity_and_undo_is_durable(self):
        result=decide(self.store,'d','P',['N1','N2'],'confirm',1)
        self.assertEqual(result['revision'],2)
        self.assertEqual(photos(self.store,'d','P','confirmed')['total'],3)
        self.assertEqual(photos(self.store,'d','P')['total'],0)
        self.store.save('d',result['state'],2) # Later ordinary saves must preserve assignments.
        result=decide(self.store,'d','P',[],'undo',3)
        self.assertEqual(photos(self.store,'d','P','confirmed')['total'],1)
        self.assertEqual(photos(self.store,'d','P')['total'],2)
    def test_pet_batch_archive_updates_existing_pet(self):
        result=decide(self.store,'d','PET',['NC'],'confirm',1)
        self.assertEqual(result['state']['animal_entities']['PET']['members'],['C','NC'])
        decide(self.store,'d','PET',[],'undo',2)
        self.assertEqual(photos(self.store,'d','PET','confirmed')['total'],1)
    def test_reject_and_undo_do_not_change_confirmed_people(self):
        decide(self.store,'d','P',['N1'],'reject',1)
        self.assertEqual(photos(self.store,'d','P')['total'],1)
        decide(self.store,'d','P',[],'undo',2)
        self.assertEqual(photos(self.store,'d','P')['total'],2)
    def test_conflicting_or_invalid_batch_has_no_partial_archive(self):
        with self.assertRaises(Conflict):decide(self.store,'d','P',['N1'],'confirm',0)
        with self.assertRaises(Conflict):decide(self.store,'d','P',['N1','NC'],'confirm',1)
        self.assertEqual(self.store.load('d')['revision'],1)
        self.assertEqual(photos(self.store,'d','P')['total'],2)
    def test_source_version_changes_reject_confirmation(self):
        with self.store.db:self.store.db.execute('UPDATE vision_media SET size=size+1')
        with self.assertRaises(Conflict):decide(self.store,'d','P',['N1'],'confirm',1)
    def test_private_thumbnail_falls_back_to_source(self):
        picture=image(self.store,'d','N1',False,self.root)
        self.assertTrue(picture.startswith(b'\xff\xd8'))
        with self.assertRaises(ValueError):image(self.store,'d','missing',False,self.root)


if __name__=='__main__':unittest.main()
