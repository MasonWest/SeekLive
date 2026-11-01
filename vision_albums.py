"""Read-only confirmed albums projected onto the current library index."""
import json
from contextlib import closing
import sqlite3
import time
from pathlib import Path


def snapshot(database, library, items):
    entities, assignments = {}, {}
    path = Path(database)
    if not path.is_file():return entities, assignments
    connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        row=connection.execute('SELECT dataset FROM review_state ORDER BY updated DESC LIMIT 1').fetchone()
        if not row:return entities, assignments
        current={it['p'] for it in items if it['k']=='img'}
        rows=connection.execute('''SELECT e.id,e.kind,e.name,v.rel,v.size,v.mtime_ns,o.metadata
          FROM memberships m JOIN entities e ON e.dataset=m.dataset AND e.id=m.entity
          JOIN observations o ON o.dataset=m.dataset AND o.id=m.observation
          JOIN vision_media v ON v.id=o.sample
          WHERE m.dataset=? AND m.confirmation='confirmed' AND e.status='followed'
          AND v.lib_id=? AND v.available=1''',(row['dataset'],library))
        for row in rows:
            if row['rel'] not in current:continue
            stamp=json.loads(row['metadata']).get('source_version')
            if stamp and stamp!=[row['size'],row['mtime_ns']]:continue
            entities[row['id']]=dict(id=row['id'],kind=row['kind'],name=row['name'] or row['id'])
            assignments.setdefault(row['rel'],set()).add(row['id'])
    finally:connection.close()
    return entities,assignments


def directory(entities, assignments, items):
    albums=[]
    for entity in entities.values():
        matches=[it for it in items if entity['id'] in assignments.get(it['p'],())]
        if matches:albums.append(dict(**entity,count=len(matches),cover=matches[0],first=matches[-1]['t'],last=matches[0]['t']))
    return sorted(albums,key=lambda e:(-e['count'],e['name'],e['id']))


def album(entities, assignments, items, ids, month='', memory='', today=None):
    if len(ids)>2 or len(set(ids))!=len(ids) or any(eid not in entities for eid in ids):raise ValueError('Unknown album')
    if not ids and memory not in ('today','month'):raise ValueError('Choose an album')
    if memory and memory not in ('today','month'):raise ValueError('Unknown memory')
    if memory and ids:raise ValueError('Choose album or memory')
    matches=[it for it in items if all(eid in assignments.get(it['p'],()) for eid in ids)]
    months={}
    for it in matches:
        ym=time.strftime('%Y-%m',time.localtime(it['t']));months[ym]=months.get(ym,0)+1
    if month:matches=[it for it in matches if growth_period(it)==month]
    partners=[]
    if len(ids)==1:
        counts={}
        for it in matches:
            for eid in assignments.get(it['p'],()):
                if eid!=ids[0]:counts[eid]=counts.get(eid,0)+1
        partners=[dict(**entities[eid],count=count) for eid,count in sorted(counts.items(),key=lambda p:(-p[1],p[0]))]
    if memory=='today':
        now=today or time.localtime()
        matches=[it for it in matches if (time.localtime(it['t']).tm_mon,time.localtime(it['t']).tm_mday)==(now.tm_mon,now.tm_mday) and time.localtime(it['t']).tm_year<now.tm_year]
    return dict(items=matches,months=[dict(ym=ym,count=n) for ym,n in sorted(months.items(),reverse=True)],partners=partners,
                title='与'.join(entities[eid]['name'] for eid in ids) if ids else ('往年今日' if memory=='today' else month+' · 时光回忆'))


def growth_period(item):
    return time.strftime('%Y-%m',time.localtime(item['t'])) if item.get('t',0)>0 else 'unknown'


def growth(database,library,entity,items):
    groups={}
    for item in items:groups.setdefault(growth_period(item),[]).append(item)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute('CREATE TABLE IF NOT EXISTS growth_covers(library TEXT,entity TEXT,period TEXT,rel TEXT,PRIMARY KEY(library,entity,period))')
        covers=dict(connection.execute('SELECT period,rel FROM growth_covers WHERE library=? AND entity=?',(library,entity)))
    result=[]
    for period,photos in sorted(groups.items()):
        photos=sorted(photos,key=lambda p:(p['t'],p['p']))
        preview=[photos[i] for i in sorted({0,len(photos)//2,len(photos)-1})]
        cover=next((p for p in photos if p['p']==covers.get(period)),photos[len(photos)//2])
        result.append(dict(period=period,count=len(photos),cover=cover,previews=preview))
    return result


def set_growth_cover(database,library,entity,period,rel,items):
    if not any(p['p']==rel and growth_period(p)==period for p in items):raise ValueError('Cover must belong to this confirmed period')
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute('CREATE TABLE IF NOT EXISTS growth_covers(library TEXT,entity TEXT,period TEXT,rel TEXT,PRIMARY KEY(library,entity,period))')
        connection.execute('INSERT INTO growth_covers VALUES(?,?,?,?) ON CONFLICT(library,entity,period) DO UPDATE SET rel=excluded.rel',(library,entity,period,rel))
