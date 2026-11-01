import json
import tempfile
import time
import unittest
from pathlib import Path
from vision_db import VisionDB
from vision_albums import snapshot,directory,album


def seed(path,items,library='t'):
    items=[it for it in items if it['k']=='img']
    store=VisionDB(path)
    state=dict(dataset_id='d',group_names={'P':'Alice','Q':'Bob'},face_checks={'F1':'keep','F2':'keep','F3':'keep','F4':'keep'},animal_entities={'PET':dict(id='PET',kind='cat',name='Milo',status='followed',members=['C'])})
    records=[dict(id=oid,sample=media,kind=kind,source_version=[100,200]) for oid,media,kind in [('F1','m1','person'),('F2','m1','person'),('F3','m2','person'),('F4','m1','person'),('C','m1','cat'),('DRAFT','m2','person')]]
    catalog=dict(groups=[dict(id='P',members=['F1','F2','F3','DRAFT']),dict(id='Q',members=['F4'])],observations=records,confirmed=[])
    store.save('d',state,0,catalog=catalog)
    with store.db:
        for i,it in enumerate(items[:2],1):store.db.execute('INSERT INTO vision_media VALUES(?,?,?,?,?,?,1)',('m'+str(i),library,it['p'],'synthetic',100,200))
    store.close()


class ConfirmedAlbumsTests(unittest.TestCase):
    def test_growth_periods_and_validated_persistent_cover(self):
        from vision_albums import growth,set_growth_cover
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab') as root:
            db=Path(root)/'growth.db'
            items=[dict(p='a.jpg',t=time.mktime((2024,1,1,12,0,0,0,0,-1))),dict(p='b.jpg',t=time.mktime((2024,1,20,12,0,0,0,0,-1))),dict(p='c.jpg',t=time.mktime((2025,2,1,12,0,0,0,0,-1))),dict(p='unknown.jpg',t=0)]
            stages=growth(db,'lib','P',items)
            self.assertEqual([s['period'] for s in stages],['2024-01','2025-02','unknown'])
            self.assertEqual(stages[0]['count'],2)
            set_growth_cover(db,'lib','P','2024-01','a.jpg',items)
            self.assertEqual(growth(db,'lib','P',items)[0]['cover']['p'],'a.jpg')
            self.assertEqual(growth(db,'other','P',items)[0]['cover']['p'],'b.jpg')
            with self.assertRaises(ValueError):set_growth_cover(db,'lib','P','2024-01','c.jpg',items)
            with self.assertRaises(ValueError):set_growth_cover(db,'lib','P','2024-01','outside.jpg',items)
            self.assertEqual(growth(db,'lib','P',[]),[])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='tmp/vision-lab');self.path=Path(self.temp.name)/'vision.db'
        self.items=[dict(p='new.jpg',k='img',t=time.mktime((2025,6,12,12,0,0,0,0,-1))),dict(p='old.jpg',k='img',t=time.mktime((2024,6,12,12,0,0,0,0,-1)))]
        seed(self.path,self.items)
    def tearDown(self):self.temp.cleanup()
    def test_unique_photos_and_joint_appearance(self):
        e,a=snapshot(self.path,'t',self.items);cards=directory(e,a,self.items)
        self.assertEqual(next(c['count'] for c in cards if c['id']=='P'),2)
        result=album(e,a,self.items,['P','Q']);self.assertEqual([it['p'] for it in result['items']],['new.jpg'])
        self.assertEqual(len(album(e,a,self.items,['P','PET'])['items']),1)
        self.assertEqual(album(e,a,self.items,['P'])['partners'][0]['count'],1)
        self.assertEqual(len(album(e,a,self.items,['P'],'2024-06')['items']),1)
        self.assertEqual(album(e,a,self.items,['P'],'2024-06')['partners'],[])
    def test_only_followed_confirmed_current_and_indexed_photos(self):
        e,a=snapshot(self.path,'other',self.items);self.assertFalse(e)
        e,a=snapshot(self.path,'t',self.items[:1]);self.assertEqual(len(a),1)
        store=VisionDB(self.path)
        with store.db:
            store.db.execute("UPDATE entities SET status='unfollowed' WHERE id='Q'")
            store.db.execute("UPDATE vision_media SET size=101 WHERE id='m2'")
        store.close();e,a=snapshot(self.path,'t',self.items)
        self.assertNotIn('Q',e);self.assertEqual(len(a),1)
    def test_memories_need_no_identity_and_use_calendar_date(self):
        today=time.struct_time((2026,6,12,0,0,0,0,0,-1))
        self.assertEqual(len(album({}, {},self.items,[],memory='today',today=today)['items']),2)
        self.assertEqual(len(album({}, {},self.items,[],'2024-06','month')['items']),1)
        with self.assertRaises(ValueError):album({}, {},self.items,['unknown'])
    def test_missing_database_is_not_created(self):
        missing=self.path.parent/'missing.db';self.assertEqual(snapshot(missing,'t',self.items),({},{}));self.assertFalse(missing.exists())


if __name__=='__main__':unittest.main()
