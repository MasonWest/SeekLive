"""Index-to-vision bridge and transactional result publication. No inference imports."""
import array
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from vision_db import VisionDB,Conflict

EXTENSIONS={'.jpg','.jpeg','.heic','.heif','.png','.webp'}
MATCH_POLICY='review-matching-2-pet-recall'


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def source_id(root,rel):return hashlib.sha256((hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:12]+'\0'+rel).encode()).hexdigest()[:16]
def version(source):return json.dumps([source['size'],source['mtime_ns']],separators=(',',':'))


def references(store,dataset):
    rows=store.db.execute('''SELECT m.observation,m.entity,e.kind,f.model,f.vector,o.metadata,v.size,v.mtime_ns FROM memberships m
      JOIN entities e ON e.dataset=m.dataset AND e.id=m.entity
      JOIN features f ON f.dataset=m.dataset AND f.observation=m.observation
      JOIN observations o ON o.dataset=m.dataset AND o.id=m.observation
      LEFT JOIN vision_media v ON v.id=o.sample
      WHERE m.dataset=? AND m.confirmation='confirmed' AND e.status='followed'
      AND ((e.kind='person' AND f.model='sface-2021dec') OR (e.kind IN ('cat','dog') AND f.model='pet-small'))
      ORDER BY m.entity,m.observation''',(dataset,)).fetchall()
    data=[]
    for row in rows:
        old_version=json.loads(row['metadata']).get('source_version')
        if old_version and row['size'] is not None and old_version!=[row['size'],row['mtime_ns']]:continue
        values=array.array('f');values.frombytes(row['vector'])
        data.append(dict(id=row['observation'],entity=row['entity'],kind=row['kind'],model=row['model'],vector=list(values)))
    # Matching policy changes rematch cached observations without rerunning models.
    return digest([MATCH_POLICY,data]),data



class ReferenceCache:
    def __init__(self):self.entries={}
    def get(self,store,dataset):
        token=store.db.execute('SELECT generation FROM vision_reference_clock WHERE id=1').fetchone()[0];entry=self.entries.get(dataset)
        if entry is None or entry[0]!=token:self.entries[dataset]=(token,references(store,dataset))
        return self.entries[dataset][1]


def active_dataset(store):
    row=store.db.execute('SELECT dataset FROM review_state ORDER BY updated DESC LIMIT 1').fetchone()
    if row:return row[0]
    state=dict(dataset_id='main',group_names={},face_checks={},animal_entities={})
    store.save('main',state,0,source='vision_init',catalog=dict(groups=[],observations=[],confirmed=[]))
    return 'main'


def refresh_pet_review_policy(store):
    """Upgrade v1 matching provenance using cached pets, preserving human state.

    Only scans with exactly the same source/model/reference vectors qualify.
    Person matching did not change. Other scans stay for normal worker handling.
    """
    from vision_inference import match
    dataset=active_dataset(store);before=store.load(dataset);reference,refs=references(store,dataset)
    old_reference=digest(refs);pets=[r for r in refs if r['kind'] in ('cat','dog')]
    current_model=store.db.execute('SELECT model FROM vision_control WHERE id=1').fetchone()[0]
    rows=store.db.execute('''SELECT s.* FROM vision_scans s JOIN vision_media v ON v.id=s.media
      WHERE v.available=1 AND s.version='['||v.size||','||v.mtime_ns||']' AND s.model=? AND s.reference=?''',(current_model,old_reference)).fetchall()
    manual={r[0] for r in store.db.execute("SELECT observation FROM memberships WHERE dataset=? AND confirmation='confirmed'",(dataset,))}
    proposals=[]
    for row in rows:
        observations=[o for o in json.loads(row['observations']) if o['kind'] in ('cat','dog')]
        proposals.append((row,match(observations,pets) if observations else []))
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if store.load(dataset)!=before:raise Conflict('Review changed during pet refresh')
        for row,suggestions in proposals:
            # Do not race an independent worker that published a newer scan.
            current=store.db.execute('SELECT version,model,reference FROM vision_scans WHERE media=?',(row['media'],)).fetchone()
            if tuple(current)!=(row['version'],row['model'],old_reference):raise Conflict('Scan changed during pet refresh')
            store.db.execute("""UPDATE vision_suggestions SET status='stale' WHERE dataset=? AND media=? AND status='pending'
              AND observation IN (SELECT id FROM observations WHERE dataset=? AND kind IN ('cat','dog'))""",(dataset,row['media'],dataset))
            for suggestion in suggestions:
                if suggestion['observation'] in manual:continue
                store.db.execute('''INSERT INTO vision_suggestions VALUES(?,?,?,?,?,?,?,'pending')
                  ON CONFLICT(dataset,observation,entity) DO UPDATE SET version=excluded.version,reference=excluded.reference,score=excluded.score,
                  status=CASE WHEN vision_suggestions.status IN ('rejected','confirmed') THEN vision_suggestions.status ELSE 'pending' END''',
                  (dataset,suggestion['observation'],suggestion['entity'],row['media'],row['version'],reference,suggestion['score']))
            store.db.execute('UPDATE vision_suggestions SET reference=? WHERE dataset=? AND media=? AND version=? AND reference=?',(reference,dataset,row['media'],row['version'],old_reference))
            store.db.execute('UPDATE vision_scans SET reference=? WHERE media=?',(reference,row['media']))
        if store.load(dataset)!=before:raise Conflict('Human review must remain unchanged')
    return dict(scans_updated=len(rows),human_revision=before['revision'],pending_pets=store.db.execute("SELECT count(*) FROM vision_suggestions s JOIN entities e ON e.dataset=s.dataset AND e.id=s.entity WHERE s.dataset=? AND s.status='pending' AND e.kind IN ('cat','dog')",(dataset,)).fetchone()[0])


def index_sources(index_path):
    if not Path(index_path).is_file():return []
    connection=sqlite3.connect(Path(index_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5);connection.row_factory=sqlite3.Row
    try:
        columns={row[1] for row in connection.execute('PRAGMA table_info(files)')}
        time_column='f.ts' if 'ts' in columns else '0'
        rows=connection.execute("SELECT f.lib_id,f.rel,l.root,l.root_exists,"+time_column+" timestamp FROM files f JOIN libs l ON l.lib_id=f.lib_id WHERE f.kind='img'").fetchall()
    finally:connection.close()
    result=[]
    for row in rows:
        if not row['root_exists'] or Path(row['rel']).suffix.lower() not in EXTENSIONS:continue
        root=Path(row['root']).resolve();path=(root/row['rel']).resolve()
        if not path.is_relative_to(root) or not path.is_file():continue
        try:stat=path.stat()
        except OSError:continue
        result.append(dict(id=source_id(root,row['rel']),lib_id=row['lib_id'],rel=row['rel'],root=str(root),size=stat.st_size,mtime_ns=stat.st_mtime_ns,timestamp=row['timestamp']))
    return result


def repair_unreviewed_species(store):
    """Reconcile legacy unreviewed animal IDs against their current scan."""
    rows=store.db.execute("""SELECT DISTINCT o.dataset,o.id,o.sample,o.kind,sc.observations,sc.version,v.size,v.mtime_ns FROM observations o
      JOIN vision_scans sc ON sc.media=o.sample JOIN vision_media v ON v.id=o.sample
      JOIN vision_suggestions p ON p.dataset=o.dataset AND p.observation=o.id
      JOIN entities e ON e.dataset=p.dataset AND e.id=p.entity
      WHERE o.kind<>e.kind AND v.available=1 AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.dataset=o.dataset AND m.observation=o.id AND m.confirmation='confirmed')""").fetchall()
    catalogs={};changed=0
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        for row in rows:
            if store.db.execute("SELECT 1 FROM memberships WHERE dataset=? AND observation=? AND confirmation='confirmed'",(row['dataset'],row['id'])).fetchone():continue
            if row['version']!=version(row):continue
            item=next((o for o in json.loads(row['observations']) if o['id']==row['id']),None)
            if not item or item['kind']==row['kind'] or {item['kind'],row['kind']}-{'cat','dog','bird'}:continue
            metadata={k:v for k,v in item.items() if k!='vector'}
            store.db.execute('UPDATE observations SET kind=?,metadata=? WHERE dataset=? AND id=?',(item['kind'],json.dumps(metadata),row['dataset'],row['id']))
            if item.get('vector') is not None:
                values=array.array('f',item['vector']);store.db.execute("UPDATE features SET vector=?,dimension=? WHERE dataset=? AND observation=? AND model='pet-small'",(values.tobytes(),len(values),row['dataset'],row['id']))
            if row['dataset'] not in catalogs:catalogs[row['dataset']]=json.loads(store.db.execute('SELECT catalog FROM datasets WHERE id=?',(row['dataset'],)).fetchone()[0])
            for old in catalogs[row['dataset']]['observations']:
                if old['id']==row['id']:old.update(metadata)
            changed+=1
        for dataset,catalog in catalogs.items():store.db.execute('UPDATE datasets SET catalog=? WHERE id=?',(json.dumps(catalog,ensure_ascii=False),dataset))
    return changed


def synchronize(store,index_path,model,review_delay=0,refresh_completed=True):
    repair_unreviewed_species(store)
    dataset=active_dataset(store);sources=index_sources(index_path);queued=0
    with store.db:
        store.db.execute('UPDATE vision_media SET available=0')
        for source in sources:
            existing=store.db.execute('SELECT * FROM vision_media WHERE lib_id=? AND rel=?',(source['lib_id'],source['rel'])).fetchone()
            if existing:source['id']=existing['id']
            else:
                collision=store.db.execute('SELECT lib_id,rel FROM vision_media WHERE id=?',(source['id'],)).fetchone()
                if collision and tuple(collision)!=(source['lib_id'],source['rel']):source['id']=digest([source['lib_id'],source['rel'],source['id']])[:16]
            store.db.execute('''INSERT INTO vision_media(id,lib_id,rel,root,size,mtime_ns) VALUES(:id,:lib_id,:rel,:root,:size,:mtime_ns)
              ON CONFLICT(id) DO UPDATE SET root=excluded.root,size=excluded.size,mtime_ns=excluded.mtime_ns,available=1''',source)
        reference,_=references(store,dataset)
        review_updated=store.db.execute('SELECT updated FROM review_state WHERE dataset=?',(dataset,)).fetchone()[0]
        settling=time.time()-review_updated<review_delay
        # Old reference snapshots can leave redundant queued matches. A current
        # completed scan already satisfies those jobs; do not rematch it again.
        complete={row['media']:(row['version'],row['model'],row['reference']) for row in store.db.execute('SELECT media,version,model,reference FROM vision_scans')}
        current_sources={source['id']:source for source in sources}
        current_versions={source['id']:version(source) for source in sources};redundant=[];pending=set()
        for row in store.db.execute("SELECT id,payload FROM jobs WHERE kind='vision' AND status='pending'"):
            payload=json.loads(row['payload']);sid=payload['source']['id']
            if payload['dataset']!=dataset:continue
            if not refresh_completed and not payload.get('refresh_existing') and complete.get(sid,())[:2]==(current_versions.get(sid),model):redundant.append((row['id'],));continue
            if payload['model']!=model or payload['version']!=current_versions.get(sid):redundant.append((row['id'],));continue
            if complete.get(sid)==(current_versions.get(sid),model,reference):redundant.append((row['id'],));continue
            if payload['version']==current_versions.get(sid) and payload['model']==model:
                if sid in pending:redundant.append((row['id'],));continue
                pending.add(sid);payload.update(source=current_sources[sid],reference=reference)
                store.db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
        store.db.executemany("UPDATE jobs SET status='done',owner=NULL,lease_until=NULL WHERE id=? AND status='pending'",redundant)
        for source in sources:
            stamp=version(source);done=store.db.execute('SELECT version,model,reference FROM vision_scans WHERE media=?',(source['id'],)).fetchone()
            if done and tuple(done)==(stamp,model,reference):continue
            if not refresh_completed and done and tuple(done)[:2]==(stamp,model):continue
            if settling and done and tuple(done)[:2]==(stamp,model):continue # Coalesce consecutive review batches before rematching completed photos.
            key='vision:'+digest([source['id'],stamp,model,reference,dataset])
            payload=dict(source=source,version=stamp,model=model,reference=reference,dataset=dataset,refresh_existing=refresh_completed)
            changed=0 if source['id'] in pending else store.db.execute("""INSERT INTO jobs(key,kind,payload,status,priority) VALUES(?,'vision',?,'pending',0)
              ON CONFLICT(key) DO UPDATE SET status='pending',payload=excluded.payload,error=NULL
              WHERE jobs.status='done' OR (jobs.status='failed' AND jobs.error='SourceUnavailable')""",(key,json.dumps(payload))).rowcount
            queued+=changed
            store.db.execute("UPDATE vision_suggestions SET status='stale' WHERE media=? AND status='pending' AND version<>?",(source['id'],stamp))
        store.db.execute('UPDATE vision_control SET last_sync=? WHERE id=1',(time.time(),))
    return dict(eligible=len(sources),queued=queued,dataset=dataset,reference=reference)


def control(store,action):
    with store.db:
        if action in ('pause','resume'):store.db.execute('UPDATE vision_control SET paused=? WHERE id=1',(int(action=='pause'),))
        elif action=='retry':store.db.execute("UPDATE jobs SET status='pending',error=NULL,owner=NULL,lease_until=NULL WHERE kind='vision' AND status='failed'")
        elif action=='rematch':
            store.db.execute("INSERT INTO jobs(key,kind,payload,status,priority) VALUES('vision-explicit-rematch','vision-rematch','{}','pending',10) ON CONFLICT(key) DO UPDATE SET status='pending',error=NULL WHERE jobs.status IN ('done','failed')")
            store.db.execute('UPDATE vision_control SET paused=0,last_sync=0 WHERE id=1')
        elif action!='scan':raise ValueError('Unknown vision action')
        if action=='scan':
            store.db.execute("""INSERT INTO jobs(key,kind,payload,status,priority) VALUES('vision-index-refresh','vision-index','{}','pending',10)
              ON CONFLICT(key) DO UPDATE SET status='pending',error=NULL WHERE jobs.status IN ('done','failed')""")
        if action in ('scan','resume','retry'):store.db.execute('UPDATE vision_control SET last_sync=0 WHERE id=1')


def status(store):
    row=store.db.execute('SELECT * FROM vision_control WHERE id=1').fetchone()
    jobs={r['status']:r['n'] for r in store.db.execute("SELECT status,count(*) n FROM jobs WHERE kind='vision' GROUP BY status")}
    now=time.time();alive=bool(row['runtime'] and now-row['heartbeat']<8)
    return dict(available=True,paused=bool(row['paused']),running=alive and not row['paused'],yielding=alive and row['foreground_until']>now,
                workerAlive=alive and now-row['worker_heartbeat']<8,queue=jobs,media=store.db.execute('SELECT count(*) FROM vision_media WHERE available=1').fetchone()[0],
                analysed=store.db.execute("SELECT count(*) FROM vision_scans s JOIN vision_media m ON m.id=s.media WHERE m.available=1 AND s.version='['||m.size||','||m.mtime_ns||']' AND s.model=?",(row['model'],)).fetchone()[0],
                candidates=store.db.execute("SELECT count(*) FROM vision_suggestions WHERE status='pending'").fetchone()[0],
                lastSync=row['last_sync'],error=row['last_error'])


def publish(store,job,owner,runtime,observations,suggestions,reference_cache=None):
    payload=job['payload'];source=payload['source']
    root=Path(source['root']).resolve();path=(root/source['rel']).resolve()
    if not path.is_relative_to(root):raise ValueError('Source outside library')
    stat=path.stat()
    if [stat.st_size,stat.st_mtime_ns]!=[source['size'],source['mtime_ns']]:raise ValueError('Source changed during analysis')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        lease=store.db.execute("SELECT owner,lease_until,status FROM jobs WHERE id=?",(job['id'],)).fetchone()
        current=store.db.execute('SELECT runtime,heartbeat FROM vision_control WHERE id=1').fetchone()
        if not lease or lease['owner']!=owner or lease['status']!='running' or lease['lease_until']<time.time() or current['runtime']!=runtime or time.time()-current['heartbeat']>8:raise Conflict('Stale worker cannot publish')
        media=store.db.execute('SELECT * FROM vision_media WHERE id=?',(source['id'],)).fetchone()
        if not media or not media['available'] or version(media)!=payload['version']:raise Conflict('Source is no longer current in the index')
        reference,_=reference_cache.get(store,payload['dataset']) if reference_cache else references(store,payload['dataset'])
        if reference!=payload['reference']:raise Conflict('Reference identities changed during analysis')
        for item in observations:
            metadata={k:v for k,v in item.items() if k!='vector'}
            store.db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)',(payload['dataset'],item['id'],source['id'],item['kind'],json.dumps(metadata)))
            if item.get('vector') is not None:
                values=array.array('f',item['vector']);name='sface-2021dec' if item['kind']=='person' else 'pet-small'
                store.db.execute('INSERT OR IGNORE INTO features VALUES(?,?,?,?,?)',(payload['dataset'],item['id'],name,len(values),values.tobytes()))
        store.db.execute('''INSERT INTO vision_scans VALUES(?,?,?,?,?,?) ON CONFLICT(media) DO UPDATE SET
          version=excluded.version,model=excluded.model,reference=excluded.reference,observations=excluded.observations,completed=excluded.completed''',
          (source['id'],payload['version'],payload['model'],payload['reference'],json.dumps(observations),time.time()))
        store.db.execute("UPDATE vision_suggestions SET status='stale' WHERE dataset=? AND media=? AND status='pending'",(payload['dataset'],source['id']))
        confirmed={r[0] for r in store.db.execute("SELECT observation FROM memberships WHERE dataset=? AND confirmation='confirmed'",(payload['dataset'],))}
        confirmed_entities={r[0] for r in store.db.execute("SELECT m.entity FROM memberships m JOIN observations o ON o.dataset=m.dataset AND o.id=m.observation WHERE m.dataset=? AND m.confirmation='confirmed' AND o.sample=?",(payload['dataset'],source['id']))}
        for suggestion in suggestions:
            if suggestion['observation'] in confirmed or suggestion['entity'] in confirmed_entities:continue
            store.db.execute('''INSERT INTO vision_suggestions VALUES(?,?,?,?,?,?,?,'pending')
              ON CONFLICT(dataset,observation,entity) DO UPDATE SET version=excluded.version,reference=excluded.reference,score=excluded.score,
              status=CASE WHEN vision_suggestions.status='rejected' THEN 'rejected' ELSE 'pending' END''',
              (payload['dataset'],suggestion['observation'],suggestion['entity'],source['id'],payload['version'],reference,suggestion['score']))
        store.db.execute("UPDATE jobs SET status='done',owner=NULL,lease_until=NULL,error=NULL WHERE id=?",(job['id'],))
