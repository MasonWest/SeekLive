import json
import tempfile
import time
import unittest
from pathlib import Path
from memory_stories import distance,group_moments,group_experiences,select_photos,open_store,read_samples,edit_story,organize_stories


def seed_stories(path,lib):
    photos=[]
    for i,item in enumerate(it for it in lib['items'] if it['k']=='img'):
        stat=(Path(lib['root'])/item['p']).stat()
        from PIL import Image
        try:
            with Image.open(Path(lib['root'])/item['p']) as image:w,h=image.size
        except Exception:w,h=64,48
        photos.append(dict(media='fixture-'+str(i),rel=item['p'],version=[stat.st_size,stat.st_mtime_ns],captured='2024-10-02T12:'+str(i//60).zfill(2)+':'+str(i%60).zfill(2),width=w,height=h,quality=1))
    store=open_store(path)
    for index,theme in enumerate(['ink','amber','sage']):
        manifest=dict(id='fixture-story-'+theme,title=['相聚的时光','风景里的我们','把这一天珍藏'][index],place='示例城市附近',date='2024-10-02',start='2024-10-02T10:00:00',end='2024-10-02T17:00:00',people=[dict(id='P',name='Alice'),dict(id='Q',name='Bob')],theme=theme,source_count=max(19,len(photos)),cover_index=0,all_photos=photos,candidates=photos,photos=select_photos(photos))
        with store:store.execute('INSERT OR REPLACE INTO stories VALUES(?,?,?,?)',(manifest['id'],lib['id'],json.dumps(manifest),index))
    store.close()


class MemoryTests(unittest.TestCase):
    def record(self,t,gps=None,day='2025-02-01'):
        return dict(ts=t,captured=day+'T12:00:00',gps=gps,rel=str(t))
    def test_events_split_at_day_gap_or_geographic_jump(self):
        records=[self.record(0,[30,110]),self.record(600,[30,110.01]),self.record(1200,[35,110]),self.record(13000,[35,110]),self.record(14000,[35,110],'2025-02-02')]
        self.assertEqual([len(g) for g in group_moments(records)],[2,1,1,1])
        self.assertLess(distance([30,110],[30,110.01]),2)
    def test_missing_dates_do_not_become_fabricated_events(self):
        self.assertEqual(group_moments([dict(ts=0,captured='',rel='unknown')]),[])
    def test_samples_are_scoped_and_source_changes_remove_stale_photos(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab') as folder:
            root=Path(folder);items=[]
            for i in range(5):
                name=str(i)+'.jpg';(root/name).write_bytes(b'fixture');items.append(dict(p=name,k='img',z=7,t=i))
            lib=dict(id='t',root=str(root),items=items);db=root/'memories.db';seed_stories(db,lib)
            self.assertEqual(len(read_samples(db,'t',items,root=root)),3)
            self.assertEqual(read_samples(db,'other',items,root=root),[])
            self.assertIsNone(read_samples(db,'t',items,'unknown',root=root))
            # Same-sized replacement must also invalidate the old source version.
            (root/'0.jpg').write_bytes(b'changed')
            self.assertEqual(read_samples(db,'t',items,'fixture-story-ink',root=root)['count'],4)
            for i in (1,2):(root/(str(i)+'.jpg')).unlink()
            self.assertIsNone(read_samples(db,'t',items,'fixture-story-ink',root=root))
    def test_missing_database_is_read_only(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab') as folder:
            path=Path(folder)/'missing.db';self.assertEqual(read_samples(path,'t',[]),[]);self.assertFalse(path.exists())
    def test_orientation_selection_preserves_order_and_falls_back(self):
        photos=[dict(rel=str(i),captured=str(i).zfill(3),width=300 if i%2 else 600,height=600 if i%2 else 300,quality=1) for i in range(44)]
        portrait=select_photos(photos,'portrait');landscape=select_photos(photos,'landscape')
        self.assertTrue(all(p['height']>p['width'] for p in portrait));self.assertTrue(all(p['width']>p['height'] for p in landscape))
        self.assertEqual(len(select_photos(photos[::2],'portrait')),22)
    def test_consecutive_days_can_form_one_experience(self):
        first=[self.record(100+i*60,[30,110],'2025-02-01') for i in range(6)]
        second=[self.record(86400+i*60,[30,110.01],'2025-02-02') for i in range(6)]
        self.assertEqual(len(group_experiences(first+second)),1)
        second=[dict(p,gps=[40,110]) for p in second]
        self.assertEqual(len(group_experiences(first+second)),2)
    def test_opening_edits_persist_conflict_and_do_not_replace_generated_manifest(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab') as folder:
            root=Path(folder);items=[]
            for i in range(5):
                name=str(i)+'.jpg';(root/name).write_bytes(b'fixture');items.append(dict(p=name,k='img',z=7,t=i))
            db=root/'memories.db';seed_stories(db,dict(id='t',root=str(root),items=items))
            self.assertEqual(edit_story(db,'t','fixture-story-ink',dict(title='Edited',theme='sage',cover_rel='2.jpg'),0),1)
            story=read_samples(db,'t',items,'fixture-story-ink',root=root)
            self.assertEqual((story['title'],story['theme'],story['cover']['p']),('Edited','sage','2.jpg'))
            with self.assertRaises(RuntimeError):edit_story(db,'t','fixture-story-ink',dict(title='Stale'),0)
            with self.assertRaises(ValueError):edit_story(db,'t','fixture-story-ink',dict(cover_rel='../outside'),1)
            seed_stories(db,dict(id='t',root=str(root),items=items))
            self.assertEqual(read_samples(db,'t',items,'fixture-story-ink',root=root)['title'],'Edited')
    def test_merge_split_and_undo_preserve_source_events(self):
        with tempfile.TemporaryDirectory(dir='tmp/vision-lab') as folder:
            root=Path(folder);items=[]
            for i in range(10):
                name=str(i)+'.jpg';(root/name).write_bytes(b'fixture');items.append(dict(p=name,k='img',z=7,t=i))
            db=root/'memories.db';seed_stories(db,dict(id='t',root=str(root),items=items))
            records=read_samples(db,'t',items,root=root,mode='full');chosen=records[:2];revs={r['id']:r['revision'] for r in chosen}
            merged=organize_stories(db,'t',chosen,'merge',revs,{},{});self.assertEqual(len(merged),1)
            self.assertEqual(len(read_samples(db,'t',items,root=root)),2)
            self.assertEqual(read_samples(db,'t',items,merged[0],root=root)['full_count'],10)
            organize_stories(db,'t',[],'undo',{}, {},{});self.assertEqual(len(read_samples(db,'t',items,root=root)),3)
            record=read_samples(db,'t',items,'fixture-story-ink',root=root,mode='full')
            split=organize_stories(db,'t',[record],'split',{record['id']:0},{},{},'2024-10-02T12:00:05')
            self.assertEqual([read_samples(db,'t',items,identity,root=root)['full_count'] for identity in split],[5,5])
            edit_story(db,'t',split[0],dict(title='Keep this edit'),0)
            with self.assertRaises(RuntimeError):organize_stories(db,'t',[],'undo',{}, {},{})


if __name__=='__main__':unittest.main()
