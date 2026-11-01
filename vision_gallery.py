"""Paginated live identity photos and explicit, reversible human decisions."""
import copy
import json
import time
import io
import threading
from pathlib import Path
from vision_db import Conflict
from vision_pipeline import status,references,version

IMAGE_SLOTS=threading.BoundedSemaphore(2)


def image(store,dataset,oid,full,base):
    row=store.db.execute('SELECT * FROM observations WHERE dataset=? AND id=?',(dataset,oid)).fetchone()
    if not row:raise ValueError('Unknown observation')
    metadata=json.loads(row['metadata']);field='preview' if full else 'crop';asset=metadata.get(field)
    if asset:
        asset=Path(asset);asset=(asset if asset.is_absolute() else base/asset).resolve()
        allowed=[base.resolve().parent,(base.resolve().parents[2]/'data'/'vision'/'media').resolve()]
        if any(asset.is_relative_to(root) for root in allowed) and asset.suffix.lower() in ('.jpg','.jpeg') and asset.is_file():return asset.read_bytes()
    media=store.db.execute('SELECT * FROM vision_media WHERE id=?',(row['sample'],)).fetchone()
    if not media or not media['available']:raise ValueError('Photo is offline')
    root=Path(media['root']).resolve();path=(root/media['rel']).resolve()
    if not path.is_relative_to(root):raise ValueError('Invalid source path')
    expected=metadata.get('source_version');stat=path.stat()
    if expected and expected!=[stat.st_size,stat.st_mtime_ns]:raise Conflict('Photo changed since detection')
    from PIL import Image,ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:pass
    with IMAGE_SLOTS:
        with Image.open(path) as original:
            original.draft('RGB',(1800,1800));picture=ImageOps.exif_transpose(original).convert('RGB')
        picture.thumbnail((1600,1600))
        if not full:
            b=metadata['box'];picture=picture.crop((b[0]*picture.width,b[1]*picture.height,b[2]*picture.width,b[3]*picture.height))
        picture.thumbnail((1000,1000) if full else (224,224));output=io.BytesIO();picture.save(output,'JPEG',quality=82);return output.getvalue()


def summary(store,dataset):
    progress=status(store)
    entities=[]
    for row in store.db.execute('SELECT * FROM entities WHERE dataset=? ORDER BY kind,name,id',(dataset,)):
        item=dict(row)
        item['confirmed']=store.db.execute("SELECT count(*) FROM memberships WHERE dataset=? AND entity=? AND confirmation='confirmed'",(dataset,row['id'])).fetchone()[0]
        item['pending']=store.db.execute('''SELECT count(*) FROM vision_suggestions s JOIN vision_media v ON v.id=s.media
          WHERE s.dataset=? AND s.entity=? AND s.status='pending' AND v.available=1
          AND s.version='['||v.size||','||v.mtime_ns||']'
          AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.dataset=s.dataset AND m.observation=s.observation AND m.confirmation='confirmed')''',(dataset,row['id'])).fetchone()[0]
        entities.append(item)
    detections={row['kind']:row['n'] for row in store.db.execute('SELECT kind,count(*) n FROM observations WHERE dataset=? GROUP BY kind',(dataset,))}
    return dict(progress=progress,entities=entities,detections=detections,confirmed=sum(e['confirmed'] for e in entities),pending=sum(e['pending'] for e in entities if e['status']=='followed'))


def photos(store,dataset,entity,mode='pending',offset=0,limit=24):
    record=store.db.execute('SELECT * FROM entities WHERE dataset=? AND id=?',(dataset,entity)).fetchone()
    if not record:raise ValueError('Unknown identity')
    if mode not in ('pending','confirmed'):raise ValueError('Unknown photo view')
    offset=max(0,int(offset));limit=min(60,max(1,int(limit)))
    if mode=='pending':
        query='''SELECT o.*,s.score,s.version,s.reference FROM vision_suggestions s JOIN observations o ON o.dataset=s.dataset AND o.id=s.observation
          JOIN vision_media v ON v.id=s.media WHERE s.dataset=? AND s.entity=? AND s.status='pending' AND v.available=1
          AND s.version='['||v.size||','||v.mtime_ns||']'
          AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.dataset=s.dataset AND m.observation=s.observation AND m.confirmation='confirmed')'''
        order=' ORDER BY s.score DESC,o.id'
    else:
        query="SELECT o.* FROM memberships m JOIN observations o ON o.dataset=m.dataset AND o.id=m.observation WHERE m.dataset=? AND m.entity=? AND m.confirmation='confirmed'"
        order=" ORDER BY COALESCE(json_extract(o.metadata,'$.confirmed_at'),0) DESC,o.id"
    total=store.db.execute('SELECT count(*) FROM ('+query+')',(dataset,entity)).fetchone()[0]
    rows=store.db.execute(query+order+' LIMIT ? OFFSET ?',(dataset,entity,limit,offset)).fetchall();items=[]
    for row in rows:
        metadata=json.loads(row['metadata'])
        items.append(dict(id=row['id'],sample=row['sample'],kind=row['kind'],captured=metadata.get('captured') or metadata.get('date') or '',box=metadata.get('box'),score=row['score'] if mode=='pending' else None))
    return dict(entity=entity,kind=record['kind'],name=record['name'],mode=mode,total=total,offset=offset,limit=limit,items=items)


def decide(store,dataset,entity,ids,action,expected_revision):
    if action not in ('confirm','reject','undo','remove'):raise ValueError('Unknown review action')
    if not isinstance(ids,list) or len(ids)>120 or any(not isinstance(i,str) for i in ids):raise ValueError('Invalid selection')
    ids=list(dict.fromkeys(ids))
    with store.db:
        store.db.execute('BEGIN IMMEDIATE');loaded=store.load(dataset)
        if not loaded or loaded['revision']!=expected_revision:raise Conflict('Records changed; refresh before confirming')
        record=store.db.execute('SELECT * FROM entities WHERE dataset=? AND id=?',(dataset,entity)).fetchone()
        if not record or record['status']!='followed':raise ValueError('Identity is not followed')
        state=copy.deepcopy(loaded['state']);catalog=json.loads(store.db.execute('SELECT catalog FROM datasets WHERE id=?',(dataset,)).fetchone()[0]);history=state.setdefault('formal_history',[])
        if action=='remove':
            if record['kind']=='person':raise ValueError('This action is for animal archives')
            if not ids:raise ValueError('Select photos first')
            for oid in ids:
                member=store.db.execute("SELECT entity,confirmation FROM memberships WHERE dataset=? AND observation=?",(dataset,oid)).fetchone()
                if not member or tuple(member)!=(entity,'confirmed'):raise Conflict('Archived assignment changed; refresh first')
            previous={oid:(lambda r:r[0] if r else None)(store.db.execute('SELECT status FROM vision_suggestions WHERE dataset=? AND observation=? AND entity=?',(dataset,oid,entity)).fetchone()) for oid in ids}
            state['animal_entities'][entity]['members']=[oid for oid in state['animal_entities'][entity]['members'] if oid not in ids]
            history.append(dict(entity=entity,ids=ids,action='remove',at=time.time(),suggestion_status=previous));state['formal_history']=history[-30:]
            revision=store.save(dataset,state,expected_revision,source='formal_remove',catalog=catalog,_locked=True)
            for oid in ids:
                row=store.db.execute('SELECT o.sample,v.size,v.mtime_ns FROM observations o LEFT JOIN vision_media v ON v.id=o.sample WHERE o.dataset=? AND o.id=?',(dataset,oid)).fetchone()
                store.db.execute("INSERT INTO vision_suggestions VALUES(?,?,?,?,?,?,?,'rejected') ON CONFLICT(dataset,observation,entity) DO UPDATE SET status='rejected'",(dataset,oid,entity,row['sample'],version(row) if row['size'] is not None else '', '',0.))
            return dict(revision=revision,state=state,changed=len(ids),entity=entity)
        if action=='undo':
            index=next((i for i in range(len(history)-1,-1,-1) if history[i]['entity']==entity),None)
            if index is None:raise ValueError('No formal review action to undo')
            receipt=history.pop(index);ids=receipt['ids']
            if receipt['action']=='confirm':
                for oid in ids:
                    member=store.db.execute('SELECT entity,confirmation FROM memberships WHERE dataset=? AND observation=?',(dataset,oid)).fetchone()
                    if not member or tuple(member)!=(entity,'confirmed'):raise Conflict('This assignment has changed; cannot undo blindly')
                if record['kind']=='person':
                    for oid,previous in receipt['checks'].items():
                        if previous is None:state['face_checks'].pop(oid,None)
                        else:state['face_checks'][oid]=previous
                    catalog['confirmed']=[f for f in catalog.get('confirmed',[]) if f['id'] not in ids]
                    catalog['confirmed'].extend(receipt['previous_confirmed'])
                else:state['animal_entities'][entity]['members']=[oid for oid in state['animal_entities'][entity]['members'] if oid not in ids]
            if receipt['action']=='remove':
                for oid in ids:
                    if store.db.execute("SELECT 1 FROM memberships WHERE dataset=? AND observation=? AND confirmation='confirmed'",(dataset,oid)).fetchone():raise Conflict('Assignment changed; cannot undo removal')
                animal=state['animal_entities'][entity];animal['members']=list(dict.fromkeys(animal['members']+ids))
            revision=store.save(dataset,state,expected_revision,source='formal_undo',catalog=catalog,_locked=True)
            for oid,previous in receipt['suggestion_status'].items():
                if previous is None:store.db.execute('DELETE FROM vision_suggestions WHERE dataset=? AND observation=? AND entity=?',(dataset,oid,entity))
                else:store.db.execute('UPDATE vision_suggestions SET status=? WHERE dataset=? AND observation=? AND entity=?',(previous,dataset,oid,entity))
        else:
            if not ids:raise ValueError('Select photos first')
            rows=[]
            for oid in ids:
                row=store.db.execute('''SELECT o.*,s.status suggestion_status,s.version,v.size,v.mtime_ns,v.available FROM observations o
                  JOIN vision_suggestions s ON s.dataset=o.dataset AND s.observation=o.id AND s.entity=?
                  JOIN vision_media v ON v.id=s.media WHERE o.dataset=? AND o.id=?''',(entity,dataset,oid)).fetchone()
                if not row or not row['available'] or row['version']!=version(row):raise Conflict('Photo result is no longer current')
                if store.db.execute("SELECT 1 FROM memberships WHERE dataset=? AND observation=? AND confirmation='confirmed'",(dataset,oid)).fetchone():raise Conflict('Photo is already archived')
                if row['kind']!=record['kind']:raise ValueError('Different species cannot be archived together')
                rows.append(row)
            receipt=dict(entity=entity,ids=ids,action=action,at=time.time(),suggestion_status={r['id']:r['suggestion_status'] for r in rows})
            if action=='confirm':
                existing={f['id'] for f in catalog['observations']}
                for row in rows:
                    if row['id'] not in existing:catalog['observations'].append(json.loads(row['metadata']));existing.add(row['id'])
                for metadata in catalog['observations']:
                    if metadata['id'] in ids:metadata['confirmed_at']=time.time()
                if record['kind']=='person':
                    checks=state.setdefault('face_checks',{});receipt['checks']={oid:checks.get(oid) for oid in ids}
                    receipt['previous_confirmed']=[f for f in catalog.get('confirmed',[]) if f['id'] in ids]
                    catalog['confirmed']=[f for f in catalog.get('confirmed',[]) if f['id'] not in ids]
                    for row in rows:
                        metadata=json.loads(row['metadata']);metadata.update(id=row['id'],person=entity,requires_explicit_check=True,confirmation='formal_gallery_user')
                        catalog['confirmed'].append(metadata);checks[row['id']]='keep'
                else:
                    animal=state['animal_entities'][entity];animal['members']=list(dict.fromkeys(animal['members']+ids));animal['confirmed']=True
            history.append(receipt);state['formal_history']=history[-30:]
            revision=store.save(dataset,state,expected_revision,source='formal_'+action,catalog=catalog,_locked=True)
            for oid in ids:store.db.execute('UPDATE vision_suggestions SET status=? WHERE dataset=? AND observation=? AND entity=?',('confirmed' if action=='confirm' else 'rejected',dataset,oid,entity))
    return dict(revision=revision,state=state,changed=len(ids),entity=entity)
