"""One recoverable CPU worker. Work leases and runtime fences live in SQLite."""
import argparse
import json
from pathlib import Path
import time
import uuid
from vision_db import VisionDB,Conflict
from vision_pipeline import references,publish,version,ReferenceCache
from vision_inference import Engine,model_versions,match


class Deferred(Exception):pass


def run(database,models,assets,runtime):
    store=VisionDB(database);versions=model_versions(models);engine=None;last_heartbeat=0;reference_cache=ReferenceCache()
    def heartbeat():
        nonlocal last_heartbeat
        if time.time()-last_heartbeat>1:
            with store.db:store.db.execute('UPDATE vision_control SET worker_heartbeat=? WHERE runtime=?',(time.time(),runtime))
            last_heartbeat=time.time()
    try:
        while True:
            heartbeat()
            control=store.db.execute('SELECT * FROM vision_control WHERE id=1').fetchone()
            if control['runtime']!=runtime or time.time()-control['heartbeat']>8:return
            if control['paused'] or control['foreground_until']>time.time():time.sleep(.25);continue
            owner=runtime+':'+uuid.uuid4().hex;job=store.claim(owner,seconds=90,kind='vision')
            if job is None:time.sleep(.5);continue
            last_renew=time.time()
            def idle():
                nonlocal last_renew
                while True:
                    heartbeat()
                    state=store.db.execute('SELECT * FROM vision_control WHERE id=1').fetchone()
                    if state['runtime']!=runtime or time.time()-state['heartbeat']>8 or state['paused']:raise Deferred()
                    if time.time()-last_renew>10:store.renew(job['id'],owner,90);last_renew=time.time()
                    if state['foreground_until']<=time.time():return
                    time.sleep(.2)
            try:
                p=job['payload'];source=p['source'];stamp=version(source)
                if p['model']!=versions['full']:
                    store.finish(job['id'],owner);continue # Superseded work is not requeued.
                reference,refs=reference_cache.get(store,p['dataset'])
                p['reference']=reference # Queued work follows the latest confirmed references.
                path=(Path(source['root'])/source['rel']).resolve();root=Path(source['root']).resolve()
                if not path.is_relative_to(root):raise ValueError('Invalid source')
                stat=path.stat()
                if [stat.st_size,stat.st_mtime_ns]!=[source['size'],source['mtime_ns']]:store.finish(job['id'],owner);continue
                previous=store.db.execute('SELECT * FROM vision_scans WHERE media=?',(source['id'],)).fetchone()
                idle()
                if previous and previous['version']==stamp and previous['model']==versions['full'] and previous['reference']==reference:
                    store.finish(job['id'],owner);continue # Duplicate queued reference snapshots already satisfied.
                if previous and previous['version']==stamp and previous['model']==versions['full']:
                    observations=json.loads(previous['observations']) # Only reference matching changes.
                else:
                    if engine is None:engine=Engine(models,assets)
                    cached=store.db.execute('SELECT * FROM vision_face_cache WHERE media=?',(source['id'],)).fetchone()
                    hint=json.loads(cached['faces']) if cached and cached['version']==stamp and cached['model']==versions['face'] else None
                    upgrade=bool(previous and previous['version']==stamp and previous['model']==versions.get('pet_upgrade_from'))
                    if upgrade:
                        old=json.loads(previous['observations']);source['_animal_upgrade']=True
                        source['_previous_people']=[o for o in old if o['kind']=='person'];source['_previous_animals']=[o for o in old if o['kind']!='person']
                        source['_upgrade_captured']=next((o['captured'] for o in old if o.get('captured')),'') or (time.strftime('%Y:%m:%d %H:%M:%S',time.localtime(source['timestamp'])) if source.get('timestamp') else '')
                        hint=source['_previous_people']
                    legacy=[json.loads(r[0]) for r in store.db.execute('SELECT metadata FROM observations WHERE dataset=? AND sample=?',(p['dataset'],source['id']))]
                    observations=engine.analyse(source,hint,idle,legacy)
                    if hint is None or upgrade:
                        faces=[o for o in observations if o['kind']=='person']
                        with store.db:store.db.execute('INSERT OR REPLACE INTO vision_face_cache VALUES(?,?,?,?)',(source['id'],stamp,versions['face'],json.dumps(faces)))
                idle();suggestions=match(observations,refs);publish(store,job,owner,runtime,observations,suggestions,reference_cache=reference_cache)
            except Deferred:store.release(job['id'],owner);time.sleep(.2)
            except Conflict:
                try:store.finish(job['id'],owner)
                except Conflict:pass
            except Exception as error:
                code='SourceUnavailable' if isinstance(error,(OSError,FileNotFoundError)) else type(error).__name__
                try:store.finish(job['id'],owner,error=code)
                except Conflict:pass
    finally:store.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True,type=Path);parser.add_argument('--models',required=True,type=Path);parser.add_argument('--assets',required=True,type=Path);parser.add_argument('--runtime',required=True)
    args=parser.parse_args();run(args.database,args.models,args.assets,args.runtime)
