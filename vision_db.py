"""SQLite identity storage, independent of media delivery and source photo files."""
import hashlib
import json
import sqlite3
import re
import time
from contextlib import nullcontext
from pathlib import Path

SPECIES={'person','cat','dog','bird'}


class Conflict(ValueError):pass


class VisionDB:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON');self.db.execute('PRAGMA busy_timeout=10000')
        version=self.db.execute('PRAGMA user_version').fetchone()[0]
        existing={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if version>2 or (existing and not {'datasets','review_state','observations','entities'}.issubset(existing)):self.db.close();raise ValueError('Not a supported vision database')
        self.db.execute('PRAGMA journal_mode=WAL')
        if version==0:self.db.executescript('''
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,catalog TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS review_state(dataset TEXT PRIMARY KEY REFERENCES datasets(id),revision INTEGER NOT NULL,state TEXT NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS entities(dataset TEXT NOT NULL REFERENCES datasets(id),id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('person','cat','dog','bird')),name TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('followed','other','unfollowed')),PRIMARY KEY(dataset,id));
        CREATE TABLE IF NOT EXISTS observations(dataset TEXT NOT NULL REFERENCES datasets(id),id TEXT NOT NULL,sample TEXT NOT NULL,kind TEXT NOT NULL,metadata TEXT NOT NULL,PRIMARY KEY(dataset,id));
        CREATE TABLE IF NOT EXISTS memberships(dataset TEXT NOT NULL,observation TEXT NOT NULL,entity TEXT NOT NULL,confirmation TEXT NOT NULL CHECK(confirmation IN ('candidate','confirmed')),PRIMARY KEY(dataset,observation),FOREIGN KEY(dataset,observation) REFERENCES observations(dataset,id),FOREIGN KEY(dataset,entity) REFERENCES entities(dataset,id));
        CREATE TABLE IF NOT EXISTS features(dataset TEXT NOT NULL,observation TEXT NOT NULL,model TEXT NOT NULL,dimension INTEGER NOT NULL,vector BLOB NOT NULL,PRIMARY KEY(dataset,observation,model),FOREIGN KEY(dataset,observation) REFERENCES observations(dataset,id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,dataset TEXT NOT NULL,revision INTEGER NOT NULL,source TEXT NOT NULL,state_hash TEXT NOT NULL,at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,key TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('pending','running','paused','done','failed')),priority INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,lease_until REAL,owner TEXT,error TEXT);
        CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status,priority DESC,id);
        PRAGMA user_version=1;
        COMMIT;
        ''')

        if version<2:self.db.executescript('''
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS vision_media(id TEXT PRIMARY KEY,lib_id TEXT NOT NULL,rel TEXT NOT NULL,root TEXT NOT NULL,size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,available INTEGER NOT NULL DEFAULT 1,UNIQUE(lib_id,rel));
        CREATE TABLE IF NOT EXISTS vision_scans(media TEXT PRIMARY KEY REFERENCES vision_media(id),version TEXT NOT NULL,model TEXT NOT NULL,reference TEXT NOT NULL,observations TEXT NOT NULL,completed REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS vision_face_cache(media TEXT PRIMARY KEY,version TEXT NOT NULL,model TEXT NOT NULL,faces TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS vision_suggestions(dataset TEXT NOT NULL,observation TEXT NOT NULL,entity TEXT NOT NULL,media TEXT NOT NULL,version TEXT NOT NULL,reference TEXT NOT NULL,score REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending',PRIMARY KEY(dataset,observation,entity));
        CREATE TABLE IF NOT EXISTS vision_control(id INTEGER PRIMARY KEY CHECK(id=1),paused INTEGER NOT NULL DEFAULT 0,runtime TEXT,heartbeat REAL NOT NULL DEFAULT 0,foreground_until REAL NOT NULL DEFAULT 0,last_sync REAL NOT NULL DEFAULT 0,last_error TEXT,model TEXT,worker_heartbeat REAL NOT NULL DEFAULT 0);
        INSERT OR IGNORE INTO vision_control(id) VALUES(1);
        PRAGMA user_version=2;
        COMMIT;
        ''')

        if version<2 or 'vision_reference_clock' not in existing:
            statements=['BEGIN IMMEDIATE','CREATE TABLE IF NOT EXISTS vision_reference_clock(id INTEGER PRIMARY KEY CHECK(id=1),generation INTEGER NOT NULL)','INSERT OR IGNORE INTO vision_reference_clock VALUES(1,0)']
            for table in ('entities','memberships'):
                for event in ('INSERT','UPDATE','DELETE'):
                    statements.append(f'CREATE TRIGGER IF NOT EXISTS vision_ref_{table}_{event.lower()} AFTER {event} ON {table} BEGIN UPDATE vision_reference_clock SET generation=generation+1 WHERE id=1; END')
            for table,key in (('observations','id'),('features','observation')):
                for event in ('INSERT','UPDATE','DELETE'):
                    row='OLD' if event=='DELETE' else 'NEW'
                    statements.append(f"CREATE TRIGGER IF NOT EXISTS vision_ref_{table}_{event.lower()} AFTER {event} ON {table} WHEN EXISTS(SELECT 1 FROM memberships WHERE dataset={row}.dataset AND observation={row}.{key} AND confirmation='confirmed') BEGIN UPDATE vision_reference_clock SET generation=generation+1 WHERE id=1; END")
            for event in ('INSERT','UPDATE','DELETE'):
                row='OLD' if event=='DELETE' else 'NEW';change="(OLD.size<>NEW.size OR OLD.mtime_ns<>NEW.mtime_ns) AND " if event=='UPDATE' else ''
                statements.append(f"CREATE TRIGGER IF NOT EXISTS vision_ref_media_{event.lower()} AFTER {event} ON vision_media WHEN {change}EXISTS(SELECT 1 FROM memberships m JOIN observations o ON o.dataset=m.dataset AND o.id=m.observation WHERE m.confirmation='confirmed' AND o.sample={row}.id) BEGIN UPDATE vision_reference_clock SET generation=generation+1 WHERE id=1; END")
            self.db.executescript(';'.join(statements)+';COMMIT;')

    def close(self):self.db.close()

    def load(self,dataset):
        row=self.db.execute('SELECT state,revision FROM review_state WHERE dataset=?',(dataset,)).fetchone()
        return dict(state=json.loads(row['state']),revision=row['revision']) if row else None

    def _project(self,state,catalog):
        entities={};memberships={};observations={f['id']:f for f in catalog['observations']}
        if len(observations)!=len(catalog['observations']) or any(f['kind'] not in SPECIES for f in observations.values()):raise ValueError('Invalid observation catalog')
        groups={g['id']:g for g in catalog['groups']};links=state.get('person_links',{})
        def root(g):
            seen=set()
            while links.get(g,g)!=g:
                if g in seen or links[g] not in groups:raise ValueError('Invalid person links')
                seen.add(g);g=links[g]
            return g
        for g in groups.values():
            pid=root(g['id']);name=str(state.get('group_names',{}).get(pid,'')).strip()
            status=state.get('person_interest',{}).get(pid,'followed' if name else 'other')
            if status not in ('followed','other','unfollowed'):raise ValueError('Invalid interest status')
            entities[pid]=('person',name,status)
            for oid in g['members']:
                if oid not in observations:raise ValueError('Missing person observation')
                check=state.get('face_checks',{}).get(oid,'')
                if check not in ('reject','ignore','unsure'):memberships[oid]=(pid,'confirmed' if check=='keep' else 'candidate')
        for f in catalog.get('confirmed',[]):
            pid=root(f['person'])
            if pid not in entities:raise ValueError('Unknown confirmed identity')
            default='' if f.get('requires_explicit_check') else 'keep'
            if state.get('face_checks',{}).get(f['id'],default)=='keep':memberships[f['id']]=(pid,'confirmed')
        animal_checks=state.get('animal_checks',{})
        for eid,animal in state.get('animal_entities',{}).items():
            kind=animal.get('kind');status=animal.get('status','followed')
            if eid in entities or not isinstance(eid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',eid) or animal.get('id')!=eid or kind not in ('cat','dog','bird') or status not in ('followed','other','unfollowed'):raise ValueError('Invalid animal identity')
            name=str(animal.get('name','')).strip()
            entities[eid]=(kind,name,status)
            for oid in animal.get('members',[]):
                observation=observations.get(oid)
                if not observation or observation['kind']=='person':raise ValueError('Unknown animal observation')
                actual=animal_checks.get(oid,observation['kind'])
                if actual!=kind:raise ValueError('Cannot merge different species or invalid detections')
                if oid in memberships:raise ValueError('Animal assigned more than once')
                memberships[oid]=(eid,'candidate' if animal.get('confirmed') is False else 'confirmed')
        return entities,memberships

    def save(self,dataset,state,expected_revision,source='review',catalog=None,_locked=False):
        if state.get('dataset_id')!=dataset:raise ValueError('Dataset mismatch')
        encoded=json.dumps(state,ensure_ascii=False,sort_keys=True)
        if len(encoded.encode('utf-8'))>5_000_000:raise ValueError('Review state too large')
        with (nullcontext() if _locked else self.db):
            if not _locked:self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT catalog FROM datasets WHERE id=?',(dataset,)).fetchone()
            if catalog is None:
                if not row:raise ValueError('Dataset not imported')
                catalog=json.loads(row['catalog'])
            old=self.load(dataset);revision=old['revision'] if old else 0
            if expected_revision!=revision:raise Conflict('Another tab updated these records; reload before saving')
            entities,memberships=self._project(state,catalog)
            self.db.execute('INSERT INTO datasets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET catalog=excluded.catalog',(dataset,json.dumps(catalog,ensure_ascii=False)))
            for observation in catalog['observations']:
                self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?) ON CONFLICT(dataset,id) DO UPDATE SET metadata=excluded.metadata,kind=excluded.kind',(dataset,observation['id'],observation['sample'],observation['kind'],json.dumps(observation,ensure_ascii=False)))
            self.db.execute('DELETE FROM memberships WHERE dataset=?',(dataset,));self.db.execute('DELETE FROM entities WHERE dataset=?',(dataset,))
            self.db.executemany('INSERT INTO entities VALUES(?,?,?,?,?)',[(dataset,eid,*values) for eid,values in entities.items()])
            self.db.executemany('INSERT INTO memberships VALUES(?,?,?,?)',[(dataset,oid,*values) for oid,values in memberships.items()])
            self.db.execute("UPDATE vision_suggestions SET status='stale' WHERE dataset=? AND (entity NOT IN (SELECT id FROM entities WHERE dataset=? AND status='followed') OR observation IN (SELECT observation FROM memberships WHERE dataset=? AND confirmation='confirmed'))",(dataset,dataset,dataset))
            revision+=1
            self.db.execute('INSERT INTO review_state VALUES(?,?,?,?) ON CONFLICT(dataset) DO UPDATE SET revision=excluded.revision,state=excluded.state,updated=excluded.updated',(dataset,revision,encoded,time.time()))
            self.db.execute('INSERT INTO audit(dataset,revision,source,state_hash,at) VALUES(?,?,?,?,?)',(dataset,revision,source,hashlib.sha256(encoded.encode()).hexdigest(),time.time()))
        return revision

    def store_feature(self,dataset,oid,model,vector):
        self.store_features(dataset,[(oid,model,vector)])

    def store_features(self,dataset,records):
        import array,math
        rows=[]
        for oid,model,vector in records:
            values=array.array('f',vector)
            if not values or not all(math.isfinite(v) for v in values):raise ValueError('Invalid feature')
            rows.append((dataset,oid,model,len(values),values.tobytes()))
        with self.db:self.db.executemany('INSERT OR IGNORE INTO features VALUES(?,?,?,?,?)',rows)

    def enqueue(self,key,kind,payload,priority=0):
        with self.db:self.db.execute("INSERT OR IGNORE INTO jobs(key,kind,payload,status,priority) VALUES(?,?,?,'pending',?)",(key,kind,json.dumps(payload),priority))

    def claim(self,owner,seconds=60,kind=None):
        now=time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE jobs SET status='pending',owner=NULL,lease_until=NULL WHERE status='running' AND lease_until<?",(now,))
            row=self.db.execute("SELECT * FROM jobs WHERE status='pending'"+(' AND kind=?' if kind else '')+' ORDER BY priority DESC,id LIMIT 1',((kind,) if kind else ())).fetchone()
            if not row:return None
            self.db.execute("UPDATE jobs SET status='running',attempts=attempts+1,owner=?,lease_until=? WHERE id=?",(owner,now+seconds,row['id']))
        return dict(id=row['id'],kind=row['kind'],payload=json.loads(row['payload']))

    def release(self,job,owner):
        with self.db:self.db.execute("UPDATE jobs SET status='pending',owner=NULL,lease_until=NULL WHERE id=? AND owner=? AND status='running'",(job,owner))

    def renew(self,job,owner,seconds=60):
        with self.db:
            changed=self.db.execute("UPDATE jobs SET lease_until=? WHERE id=? AND owner=? AND status='running' AND lease_until>=?",(time.time()+seconds,job,owner,time.time())).rowcount
            if not changed:raise Conflict('Job lease expired')

    def finish(self,job,owner,error=None):
        with self.db:
            changed=self.db.execute("UPDATE jobs SET status=?,error=?,lease_until=NULL,owner=NULL WHERE id=? AND status='running' AND owner=? AND lease_until>=?",('failed' if error else 'done',error,job,owner,time.time())).rowcount
            if not changed:raise Conflict('Job lease is no longer owned')
