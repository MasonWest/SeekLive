"""Integrate only explicitly user-confirmed discovery batches, preserving base IDs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from evaluate import dataset_id
from run import unit
from review import build_review


def integrate(base,discovery_folder,labels_path):
    result=json.loads((base/'results.private.json').read_text(encoding='utf-8'));labels=json.loads(labels_path.read_text(encoding='utf-8-sig'));discovery=json.loads((discovery_folder/'discovery.private.json').read_text(encoding='utf-8'))
    signature=dataset_id(result)
    if discovery['base_dataset_id']!=signature or labels['dataset_id']!=signature:raise ValueError('Dataset mismatch')
    groups={g['id'] for g in result['face_groups']}
    def root(pid):
        visited=set()
        while labels.get('person_links',{}).get(pid,pid)!=pid:
            if pid in visited:raise ValueError('Cyclic person links')
            visited.add(pid);pid=labels['person_links'][pid]
        if pid not in groups:raise ValueError('Unknown person')
        return pid
    path=base/'confirmed_discoveries.private.json'
    saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(base_dataset_id=signature,records=[],receipts=[])
    if saved['base_dataset_id']!=signature:raise ValueError('Confirmed membership dataset mismatch')
    records={f['id']:f for f in saved['records']};added=0
    for candidate in discovery['candidates']:
        pid=root(candidate['person'])
        run=discovery_folder.parent/candidate['source_run']
        sources=json.loads((run/'samples.private.json').read_text(encoding='utf-8'))
        source=next(s for s in sources if s['id']==candidate['sample']);st=Path(source['path']).stat()
        version=[st.st_size,st.st_mtime_ns]
        cache=json.loads((run/'cache'/(candidate['sample']+'.json')).read_text(encoding='utf-8'))
        if version!=candidate['source_version'] or version!=cache['version']:raise ValueError('Source version changed')
        face=next(f for f in cache['faces'] if f['id']==candidate['id']);vector=unit(face['vector'])
        if vector.shape!=(128,):raise ValueError('Unexpected face feature')
        if candidate['id'] in records:
            old=records[candidate['id']]
            if root(old['person'])!=pid or old['source_version']!=version:raise ValueError('Conflicting membership')
            continue
        record={k:v for k,v in candidate.items() if k not in ('score','margin','priority','status','source_run')}
        for field in ('crop','preview'):
            asset=(discovery_folder/record[field]).resolve()
            if not asset.is_file():raise ValueError('Missing confirmed preview')
            record[field]=os.path.relpath(asset,base).replace('\\','/')
        record.update(person=pid,vector=vector.tolist(),kind='person',date=candidate.get('captured',''),confirmation='user_explicit_batch_confirmation',confirmed_date='2026-11-01')
        records[record['id']]=record;added+=1
    receipt_id=hashlib.sha256(json.dumps([(c['id'],c['person'],c['source_version']) for c in discovery['candidates']],sort_keys=True).encode()).hexdigest()[:16]
    if not any(r['id']==receipt_id for r in saved['receipts']):saved['receipts'].append(dict(id=receipt_id,date='2026-11-01',count=len(discovery['candidates']),source='conversation_explicit_confirmation',reference_signature=discovery['reference_signature']))
    saved['records']=list(records.values());part=path.with_suffix('.part');part.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8');part.replace(path)
    build_review(base,result)
    print(json.dumps(dict(added=added,total_confirmed_discoveries=len(records),receipts=len(saved['receipts']))))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,default=Path('tmp/vision-lab/combined'));p.add_argument('--discovery',type=Path,required=True);p.add_argument('--labels',type=Path,required=True)
    a=p.parse_args();integrate(a.base.resolve(),a.discovery.resolve(),a.labels)
