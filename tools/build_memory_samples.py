"""Explicit local EXIF/event sampling; never uploads photos or coordinates."""
import argparse
import collections
import datetime
import io
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from memory_stories import open_store,group_experiences,story_id,distance,select_photos


def degrees(values):return float(values[0])+float(values[1])/60+float(values[2])/3600


def metadata(path):
    from PIL import Image
    with Image.open(path) as image:
        ex=image.getexif();sub=ex.get_ifd(34665) if 34665 in ex else {};gps=ex.get_ifd(34853) if 34853 in ex else {}
        w,h=image.size
        if ex.get(274) in (5,6,7,8):w,h=h,w
        date=sub.get(36867) or ex.get(36867);result=dict(width=w,height=h,captured='',gps=None,ts=0)
        if date:
            try:
                stamp=datetime.datetime.strptime(str(date)[:19],'%Y:%m:%d %H:%M:%S');result.update(captured=stamp.isoformat(),ts=stamp.timestamp())
            except ValueError:pass
        if gps.get(2) and gps.get(4):
            lat=degrees(gps[2])*(-1 if gps.get(1)=='S' else 1);lon=degrees(gps[4])*(-1 if gps.get(3)=='W' else 1)
            if -90<=lat<=90 and -180<=lon<=180 and (lat or lon):result['gps']=[lat,lon]
        return result


def cities(path):
    if not Path(path).is_file():return []
    result=[]
    with zipfile.ZipFile(path) as archive:
        for line in archive.read('cities15000.txt').decode('utf-8').splitlines():
            parts=line.split('\t')
            # Prefer an actual Chinese alternate name when available.
            labels=[s for s in parts[3].split(',') if re.fullmatch(r'[\u4e00-\u9fff]{2,8}',s)]
            label=next((s for s in labels if s.endswith('市')),labels[0] if labels else parts[1])
            result.append((float(parts[4]),float(parts[5]),label))
    return result


def place_name(records,gazetteer):
    positions=[r['gps'] for r in records if r.get('gps')]
    if not positions or not gazetteer:return ''
    center=[sorted(p[i] for p in positions)[len(positions)//2] for i in (0,1)]
    nearest=min(gazetteer,key=lambda p:distance(center,p[:2]));km=distance(center,nearest[:2])
    return nearest[2]+'附近' if km<60 else ''


def choose_photos(records,roots,limit=22,store=None,assets=None):
    """Sample across the event, favor readable images and avoid near duplicates."""
    from PIL import Image,ImageOps,ImageStat,ImageFilter
    candidates=records if len(records)<=100 else [records[round(i*(len(records)-1)/99)] for i in range(100)]
    selected=[];hashes=[]
    for record in candidates:
        try:
            stamp=json.dumps(record['version'],separators=(',',':'))
            cached=store.execute('SELECT data FROM photo_scores WHERE media=? AND version=?',(record['media'],stamp)).fetchone() if store else None
            if cached:details=json.loads(cached[0]);finger=int(details['hash'],16)
            else:
                path=roots[record['media']]
                if assets:
                    key=hashlib.sha256(stamp.encode()).hexdigest()[:12];preview=Path(assets)/record['media']/key/'preview.jpg'
                    if preview.is_file():path=preview
                with Image.open(path) as original:
                    original.draft('RGB',(320,320));picture=ImageOps.exif_transpose(original).convert('RGB');picture.thumbnail((256,256))
                gray=picture.convert('L');tiny=gray.resize((9,8));v=list(tiny.get_flattened_data() if hasattr(tiny,'get_flattened_data') else tiny.getdata());finger=sum((v[y*9+x]>v[y*9+x+1])<<(y*8+x) for y in range(8) for x in range(8))
                mean=ImageStat.Stat(gray).mean[0];sharp=ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]
                details=dict(quality=math.log1p(sharp)+max(0,1-abs(mean-125)/125),width=picture.width,height=picture.height,hash=f'{finger:016x}')
                if store:
                    with store:store.execute('INSERT OR REPLACE INTO photo_scores VALUES(?,?,?)',(record['media'],stamp,json.dumps(details)))
            if any((finger^old).bit_count()<7 for old in hashes):continue
            selected.append(dict(record,**details));hashes.append(finger)
        except Exception:continue
    # Select a good representative in each time-ordered bin, keeping the arc.
    if limit and len(selected)>limit:
        selected=[max(selected[i*len(selected)//limit:(i+1)*len(selected)//limit],key=lambda r:r['quality']) for i in range(limit)]
    return sorted(selected,key=lambda r:r['ts'])


def build(args):
    import pillow_heif
    pillow_heif.register_heif_opener()
    source=sqlite3.connect(Path(args.vision).resolve().as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    rows=source.execute('SELECT * FROM vision_media WHERE available=1 ORDER BY lib_id,id').fetchall()
    stored=open_store(args.database);roots={};records=collections.defaultdict(list);read=0;cached=0;failures=0
    if stored.execute('SELECT count(*) FROM stories').fetchone()[0]:
        backup=Path(args.database).parent/'vision'/'backups'/('memories-before-expansion-'+time.strftime('%Y%m%d-%H%M%S')+'.db');backup.parent.mkdir(parents=True,exist_ok=True)
        destination=sqlite3.connect(backup);stored.backup(destination);destination.close()
    for index,row in enumerate(rows):
        stamp=json.dumps([row['size'],row['mtime_ns']],separators=(',',':'));root=Path(row['root']).resolve();path=(root/row['rel']).resolve()
        if not path.is_relative_to(root):continue
        try:
            stat=path.stat()
            if [stat.st_size,stat.st_mtime_ns]!=[row['size'],row['mtime_ns']]:continue
            old=stored.execute('SELECT metadata FROM metadata WHERE media=? AND version=?',(row['id'],stamp)).fetchone()
            if old:meta=json.loads(old[0]);cached+=1
            else:
                meta=metadata(path);read+=1
                with stored:stored.execute('INSERT OR REPLACE INTO metadata VALUES(?,?,?)',(row['id'],stamp,json.dumps(meta)))
            roots[row['id']]=path
            if meta.get('captured'):records[row['lib_id']].append(dict(meta,media=row['id'],rel=row['rel'],version=[row['size'],row['mtime_ns']]))
        except Exception:failures+=1
        if (index+1)%500==0:print(json.dumps(dict(examined=index+1,read=read,cached=cached,failed=failures)),flush=True)
    gazetteer=cities(args.cities);saved=[]
    dataset=source.execute('SELECT dataset FROM review_state ORDER BY updated DESC LIMIT 1').fetchone()[0]
    for library,photos in records.items():
        owners=collections.defaultdict(set);names={}
        for row in source.execute('''SELECT v.rel,e.id,e.name FROM memberships m JOIN entities e ON e.dataset=m.dataset AND e.id=m.entity
          JOIN observations o ON o.dataset=m.dataset AND o.id=m.observation JOIN vision_media v ON v.id=o.sample
          WHERE m.dataset=? AND m.confirmation='confirmed' AND e.status='followed' AND v.lib_id=?''',(dataset,library)):
            owners[row['rel']].add(row['id']);names[row['id']]=row['name']
        groups=[g for g in group_experiences(photos) if len(g)>=12 and sum(bool(r['gps']) for r in g)>=5 and g[-1]['ts']-g[0]['ts']>=20*60]
        # Busy photo days with location evidence and familiar people are useful
        # samples; they are not automatically asserted to be holidays or trips.
        groups.sort(key=lambda g:len(g)+min(15,sum(bool(owners[r['rel']]) for r in g))*2,reverse=True)
        chosen=[];active=[]
        for group in groups:
            day=group[0]['captured'][:10]
            if any(group[0]['captured'][:10]==old[0]['captured'][:10] for old in chosen):continue
            candidates=choose_photos(group,roots,limit=None,store=stored,assets=Path(args.vision).parent/'vision'/'media')
            selected=select_photos(candidates)
            if len(selected)<8:continue
            place=place_name(group,gazetteer);counts=collections.Counter(eid for r in group for eid in owners[r['rel']])
            people=[dict(id=eid,name=names[eid],count=n) for eid,n in counts.most_common(5) if names[eid]]
            cover=max(range(len(selected)),key=lambda i:selected[i]['quality']+(2 if owners[selected[i]['rel']] else 0))
            title=['相聚的时光','风景里的我们','把这一天珍藏','沿途的光'][len(chosen)%4] if people else '沿途的光'
            end_day=group[-1]['captured'][:10];date=day if day==end_day else day+' — '+end_day
            fields=('media','rel','version','captured','width','height')
            manifest=dict(id=story_id(library,group),title=title,place=place,date=date,start=group[0]['captured'],end=group[-1]['captured'],people=people,
                theme=['ink','amber','sage'][len(chosen)%3],source_count=len(group),cover_index=cover,
                cover_rel=selected[cover]['rel'],all_photos=[{k:r[k] for k in fields} for r in group],
                candidates=[{k:r[k] for k in (*fields,'quality')} for r in candidates],photos=[{k:r[k] for k in fields} for r in selected])
            with stored:stored.execute('INSERT OR REPLACE INTO stories VALUES(?,?,?,?)',(manifest['id'],library,json.dumps(manifest,ensure_ascii=False),time.time()))
            chosen.append(group);saved.append(dict(library=library,photos=len(selected),source_count=len(group),has_place=bool(place),people=len(people)))
            active.append(manifest['id'])
            if len(chosen)>=args.count:break
        if active:
            with stored:stored.execute("DELETE FROM stories WHERE lib=? AND id NOT LIKE 'manual-%' AND id NOT IN ("+','.join('?' for _ in active)+') AND id NOT IN (SELECT id FROM story_edits)',(library,*active))
    source.close();stored.close();print(json.dumps(dict(complete=True,examined=len(rows),read=read,cached=cached,failed=failures,samples=saved)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--vision',default='data/vision.db');parser.add_argument('--database',default='data/memories.db');parser.add_argument('--cities',default='data/vision/cities15000.zip');parser.add_argument('--count',type=int,default=12)
    build(parser.parse_args())
