"""Local, versioned memory samples. Building is explicit; browsing is read-only."""
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path


def distance(a,b):
    lat1,lon1,lat2,lon2=map(math.radians,(*a,*b))
    h=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371*2*math.asin(min(1,math.sqrt(h)))


def group_moments(records):
    """Conservative same-day sessions; long gaps or large jumps split sessions."""
    groups=[]
    for item in sorted(records,key=lambda r:(r['ts'],r['rel'])):
        if not item.get('captured'):continue
        previous=groups[-1][-1] if groups else None
        split=previous is None or item['captured'][:10]!=previous['captured'][:10] or item['ts']-previous['ts']>3*3600
        if not split and previous.get('gps') and item.get('gps'):
            split=distance(previous['gps'],item['gps'])>35
        if split:groups.append([])
        groups[-1].append(item)
    return groups


def open_store(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path,timeout=10);c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''CREATE TABLE IF NOT EXISTS metadata(
      media TEXT PRIMARY KEY,version TEXT NOT NULL,metadata TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY,lib TEXT NOT NULL,manifest TEXT NOT NULL,created REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS story_edits(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL,updated REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS story_actions(id INTEGER PRIMARY KEY,lib TEXT NOT NULL,payload TEXT NOT NULL,undone INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS photo_scores(media TEXT PRIMARY KEY,version TEXT NOT NULL,data TEXT NOT NULL);''')
    return c


def group_experiences(records):
    moments=group_moments(records);areas={}
    for r in records:
        if r.get('gps'):
            cell=tuple(round(n,1) for n in r['gps']);areas.setdefault(cell,set()).add(r['captured'][:10])
    frequent={cell for cell,days in areas.items() if len(days)>=6 and (max(days)>min(days) and (time.mktime(time.strptime(max(days),'%Y-%m-%d'))-time.mktime(time.strptime(min(days),'%Y-%m-%d')))>60*86400)}
    def center(group):
        points=[r['gps'] for r in group if r.get('gps')]
        return [sorted(p[i] for p in points)[len(points)//2] for i in (0,1)] if len(points)>=5 else None
    experiences=[]
    for group in moments:
        old=experiences[-1] if experiences else None
        a=center(old) if old else None;b=center(group)
        join=bool(old and a and b and old[-1]['captured'][:10]!=group[0]['captured'][:10]
          and 0<group[0]['ts']-old[-1]['ts']<30*3600 and group[-1]['ts']-old[0]['ts']<7*86400
          and distance(a,b)<25 and tuple(round(n,1) for n in a) not in frequent and tuple(round(n,1) for n in b) not in frequent)
        if join:old.extend(group)
        else:experiences.append(list(group))
    return experiences


def select_photos(photos,layout='auto',limit=22):
    if not photos:return []
    ordered=sorted(photos,key=lambda p:(p.get('captured',''),p['rel']))
    def score(p):
        w,h=p.get('width',0),p.get('height',0)
        preferred=(h>w*1.1 if layout=='portrait' else w>h*1.1 if layout=='landscape' else False)
        return p.get('quality',0)+4*preferred
    if len(ordered)<=limit:return ordered
    return [max(ordered[i*len(ordered)//limit:(i+1)*len(ordered)//limit],key=score) for i in range(limit)]


def story_id(library,records):
    return hashlib.sha256(json.dumps([library,[r['media'] for r in records]],sort_keys=True).encode()).hexdigest()[:20]


def read_samples(path,library,items,identity=None,root=None,layout='auto',mode='selected'):
    """Only return photos still in this library with the same source version."""
    if not Path(path).is_file():return [] if identity is None else None
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        rows=c.execute('SELECT manifest FROM stories WHERE lib=?'+(' AND id=?' if identity else '')+' ORDER BY created DESC,id',((library,identity) if identity else (library,))).fetchall()
        has_edits=c.execute("SELECT 1 FROM sqlite_master WHERE name='story_edits'").fetchone()
        edits={r[0]:(r[1],json.loads(r[2])) for r in c.execute('SELECT id,revision,data FROM story_edits')} if has_edits else {}
    finally:c.close()
    current={it['p']:it for it in items if it['k']=='img'};result=[]
    for row in rows:
        raw=json.loads(row[0]);revision,edit=edits.get(raw['id'],(0,{}));raw.update(edit);valid={}
        if raw.get('hidden'):continue
        pool=raw.get('all_photos',raw['photos'])
        for photo in pool:
            item=current.get(photo['rel'])
            if item is None:continue
            # Size is available in the public item projection. File mtime is
            # checked against the original media binding below by the handler.
            if item['z']!=photo['version'][0]:continue
            if root is not None:
                folder=Path(root).resolve();source=(folder/photo['rel']).resolve()
                if not source.is_relative_to(folder):continue
                try:stat=source.stat()
                except OSError:continue
                if [stat.st_size,stat.st_mtime_ns]!=photo['version']:continue
            valid[photo['rel']]=dict(photo,item=item)
        all_photos=sorted(valid.values(),key=lambda p:(p.get('captured',''),p['rel']))
        candidates=[dict(p,item=valid[p['rel']]['item']) for p in raw.get('candidates',raw['photos']) if p['rel'] in valid and p['version']==valid[p['rel']]['version']]
        selected=all_photos if mode=='full' else select_photos(candidates,layout)
        if len(selected)<3:continue
        record={key:raw[key] for key in ('id','title','place','date','people','theme','source_count','start','end')}
        cover=valid.get(raw.get('cover_rel',''))
        if 'cover_rel' not in edit and not raw.get('manual') and layout in ('portrait','landscape'):
            preferred=[p for p in candidates if (p.get('height',0)>p.get('width',0)*1.1 if layout=='portrait' else p.get('width',0)>p.get('height',0)*1.1)]
            if preferred:cover=max(preferred,key=lambda p:p.get('quality',0))
        if cover is None:cover=selected[min(raw.get('cover_index',0),len(selected)-1)]
        record.update(photos=selected,all_photos=all_photos,candidates=candidates,cover=cover['item'],cover_rel=cover['rel'],count=len(selected),full_count=len(all_photos),revision=revision,layout=layout,mode=mode)
        result.append(record)
    result.sort(key=lambda r:r['start'],reverse=True)
    return (result[0] if result else None) if identity else result


def edit_story(path,library,identity,changes,expected_revision):
    if not isinstance(changes,dict) or set(changes)-{'title','place','theme','cover_rel'}:raise ValueError('Invalid opening fields')
    for key,value in changes.items():
        if not isinstance(value,str) or len(value)>(300 if key=='cover_rel' else 80):raise ValueError('Invalid opening text')
    if 'title' in changes and not changes['title'].strip():raise ValueError('Title is required')
    if 'theme' in changes and changes['theme'] not in ('ink','amber','sage'):raise ValueError('Unknown style')
    c=open_store(path)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT manifest FROM stories WHERE id=? AND lib=?',(identity,library)).fetchone()
            if not row:raise ValueError('Unknown memory')
            raw=json.loads(row[0]);old=c.execute('SELECT revision,data FROM story_edits WHERE id=?',(identity,)).fetchone();revision=old[0] if old else 0
            if revision!=expected_revision:raise RuntimeError('Memory changed; reopen before saving')
            if 'cover_rel' in changes and changes['cover_rel'] not in {p['rel'] for p in raw.get('all_photos',raw['photos'])}:raise ValueError('Cover must belong to this experience')
            data=json.loads(old[1]) if old else {};data.update({k:v if k=='cover_rel' else v.strip() for k,v in changes.items()})
            c.execute('INSERT OR REPLACE INTO story_edits VALUES(?,?,?,?)',(identity,revision+1,json.dumps(data,ensure_ascii=False),time.time()))
        return revision+1
    finally:c.close()


def organize_stories(path,library,records,action,revisions,entities,assignments,split_at=''):
    """Manual regrouping hides source events, preserves originals, and is undoable."""
    if action not in ('merge','split','undo'):raise ValueError('Unknown event action')
    c=open_store(path)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            if action=='undo':
                receipt=c.execute('SELECT id,payload FROM story_actions WHERE lib=? AND undone=0 ORDER BY id DESC LIMIT 1',(library,)).fetchone()
                if not receipt:raise ValueError('没有可撤销的事件整理')
                history=json.loads(receipt[1])
                for identity in history['created']:
                    if c.execute('SELECT 1 FROM story_edits WHERE id=?',(identity,)).fetchone():raise RuntimeError('新片段已有编辑，不能直接撤销')
                for identity,previous in history['previous'].items():
                    now=c.execute('SELECT revision FROM story_edits WHERE id=?',(identity,)).fetchone()
                    if not now or now[0]!=(previous[0]+1 if previous else 1):raise RuntimeError('原片段已有修改')
                    if previous:c.execute('UPDATE story_edits SET revision=?,data=?,updated=? WHERE id=?',(*previous,identity))
                    else:c.execute('DELETE FROM story_edits WHERE id=?',(identity,))
                c.executemany('DELETE FROM stories WHERE id=?',[(identity,) for identity in history['created']]);c.execute('UPDATE story_actions SET undone=1 WHERE id=?',(receipt[0],))
                return []
            if len(records)!=(2 if action=='merge' else 1):raise ValueError('请选择需要整理的回忆')
            previous={}
            for record in records:
                row=c.execute('SELECT revision,data,updated FROM story_edits WHERE id=?',(record['id'],)).fetchone()
                current=row[0] if row else 0
                if current!=revisions.get(record['id']):raise RuntimeError('回忆已更新')
                previous[record['id']]=list(row) if row else None
            photos={p['rel']:p for r in records for p in r['all_photos']};pool=sorted(photos.values(),key=lambda p:(p['captured'],p['rel']))
            if action=='split':
                if not split_at or len(split_at)<16:raise ValueError('请选择拆分时间')
                groups=[[p for p in pool if p['captured']<split_at],[p for p in pool if p['captured']>=split_at]]
                if any(len(g)<3 for g in groups):raise ValueError('拆分后的每段至少需要 3 张照片，请调整时间')
            else:groups=[pool]
            candidates={p['rel']:p for r in records for p in r['candidates']};created=[];base=records[0]
            for index,group in enumerate(groups):
                keep={p['rel'] for p in group};ranked=[candidates[p['rel']] for p in group if p['rel'] in candidates] or group;selected=select_photos(ranked)
                if len(selected)<3:selected=select_photos(group)
                identity='manual-'+hashlib.sha256((str(time.time_ns())+str(index)).encode()).hexdigest()[:20]
                def plain(p):return {k:v for k,v in p.items() if k!='item'}
                people={eid for p in group for eid in assignments.get(p['rel'],())};start=group[0]['captured'];end=group[-1]['captured']
                manifest=dict(id=identity,title=base['title']+(' · '+str(index+1) if action=='split' else ''),place=base['place'],theme=base['theme'],start=start,end=end,date=start[:10] if start[:10]==end[:10] else start[:10]+' — '+end[:10],people=[dict(id=eid,name=entities[eid]['name']) for eid in sorted(people) if eid in entities],source_count=len(group),cover_rel=base['cover_rel'] if base['cover_rel'] in keep else selected[0]['rel'],cover_index=0,all_photos=[plain(p) for p in group],candidates=[plain(p) for p in ranked],photos=[plain(p) for p in selected],manual=True)
                c.execute('INSERT INTO stories VALUES(?,?,?,?)',(identity,library,json.dumps(manifest,ensure_ascii=False),time.time()));created.append(identity)
            for record in records:
                old=previous[record['id']];data=json.loads(old[1]) if old else {};data['hidden']=True
                c.execute('INSERT OR REPLACE INTO story_edits VALUES(?,?,?,?)',(record['id'],(old[0] if old else 0)+1,json.dumps(data),time.time()))
            c.execute('INSERT INTO story_actions(lib,payload) VALUES(?,?)',(library,json.dumps(dict(created=created,previous=previous))))
            return created
    finally:c.close()
