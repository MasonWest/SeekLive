"""Independent local detection audit; no production writes, no photo uploads."""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vision_db import VisionDB
from vision_pipeline import active_dataset,references
from vision_inference import match
from vision_image import session,read_image,detect_pets,pet_embedding,np,cv2,jpeg


def run(args):
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    store=VisionDB('data/vision.db');dataset=active_dataset(store);before=store.load(dataset)
    _,refs=references(store,dataset);refs=[r for r in refs if r['kind']=='cat'];target=store.db.execute('SELECT id FROM entities WHERE id=?',(args.entity,)).fetchone()[0]
    idx=sqlite3.connect('file:data/index.db?mode=ro',uri=True)
    wanted={(r[0],r[1]) for r in idx.execute("SELECT lib_id,rel,strftime('%Y-%m',ts,'unixepoch','localtime') FROM files WHERE kind='img'") if not args.months or r[2] in args.months};idx.close()
    rows=[dict(r) for r in store.db.execute('SELECT * FROM vision_media WHERE available=1') if (r['lib_id'],r['rel']) in wanted]
    strong=session('data/vision/models/yolox_s.onnx');nano=session('data/vision/models/yolox_nano.onnx');pet=session('data/vision/models/pet_small.onnx')
    records=[];start=time.time()
    for index,source in enumerate(rows):
        # Pause between images while the main gallery is serving media.
        while True:
            state=store.db.execute('SELECT foreground_until FROM vision_control WHERE id=1').fetchone()
            if state['foreground_until']<=time.time():break
            time.sleep(.2)
        path=(Path(source['root'])/source['rel']).resolve();assert path.is_relative_to(Path(source['root']).resolve())
        image,captured,_=read_image(path);rgb=np.asarray(image);bgr=cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)
        old=json.loads(store.db.execute('SELECT observations FROM vision_scans WHERE media=?',(source['id'],)).fetchone()[0]);old_cats=[o for o in old if o['kind']=='cat']
        low=detect_pets(nano,bgr,kinds=(15,),score_threshold=.20)
        found=detect_pets(strong,rgb,kinds=(15,),input_side=640,score_threshold=.35)
        observations=[]
        for i,(box,score,kind) in enumerate(found):
            region=image.crop(tuple(float(v) for v in box));vector=pet_embedding(pet,region)
            oid='audit-'+source['id']+'-'+str(i);crop=root/(oid+'.jpg');jpeg(region,crop,224)
            observations.append(dict(id=oid,sample=source['id'],kind=kind,box=[float(box[0]/image.width),float(box[1]/image.height),float(box[2]/image.width),float(box[3]/image.height)],vector=vector.tolist(),score=score,crop=crop.name))
        proposals=match(observations,refs);preview=source['id']+'.jpg';jpeg(image,root/preview,1000)
        records.append(dict(media=source['id'],library=source['lib_id'],source_version=[source['size'],source['mtime_ns']],captured=captured,preview=preview,old_cats=len(old_cats),nano_low_cats=len(low),strong_cats=len(found),observations=observations,suggestions=proposals,target_matches=sum(p['entity']==target for p in proposals)))
        if (index+1)%10==0:print(json.dumps(dict(analysed=index+1,total=len(rows))),flush=True)
    assert store.load(dataset)==before;store.close()
    result=dict(records=records,target=target,summary=dict(photos=len(rows),old_cat_photos=sum(r['old_cats']>0 for r in records),nano_low_cat_photos=sum(r['nano_low_cats']>0 for r in records),strong_cat_photos=sum(r['strong_cats']>0 for r in records),strong_cat_detections=sum(r['strong_cats'] for r in records),target_candidate_photos=sum(r['target_matches']>0 for r in records),additional_cat_photos=sum(r['strong_cats']>0 and not r['old_cats'] for r in records),seconds=round(time.time()-start,1)))
    (root/'audit.private.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');print(json.dumps(result['summary']),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='tmp/vision-lab/pet-recall-audit');parser.add_argument('--entity',required=True);parser.add_argument('--months',nargs='*',help='Optional YYYY-MM list; omitted means all months');run(parser.parse_args())
