"""Idempotently migrate the local prototype into the SQLite identity store."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from vision_db import VisionDB
from evaluate import dataset_id


def import_data(base=Path('tmp/vision-lab/combined'),database=Path('data/vision.db')):
    result=json.loads((base/'results.private.json').read_text(encoding='utf-8'));dataset=dataset_id(result)
    state=json.loads((base/'labels.seed.private.json').read_text(encoding='utf-8'))
    confirmed_path=base/'confirmed_discoveries.private.json'
    confirmed=json.loads(confirmed_path.read_text(encoding='utf-8'))['records'] if confirmed_path.exists() else []
    animal_path=base/'animal_detections.private.json'
    additional=json.loads(animal_path.read_text(encoding='utf-8'))['pets'] if animal_path.exists() else []
    catalog=dict(groups=result['face_groups'],observations=result['faces']+result['pets']+additional+[{k:v for k,v in f.items() if k!='vector'} for f in confirmed],confirmed=[{k:v for k,v in f.items() if k!='vector'} for f in confirmed])
    versions={s['id']:s.get('source_version') for s in result['samples']}
    for observation in catalog['observations']:
        if versions.get(observation['sample']):observation.setdefault('source_version',versions[observation['sample']])
    for face in confirmed:state.setdefault('face_checks',{}).setdefault(face['id'],'keep')
    store=VisionDB(database)
    try:
        existing=store.load(dataset)
        if existing:state=existing['state']
        revision=store.save(dataset,state,existing['revision'] if existing else 0,source='prototype_migration',catalog=catalog)
        import numpy as np
        features=[(face['id'],'sface-2021dec',vector) for face,vector in zip(result['faces'],np.load(base/'face_vectors.npy'))]
        features.extend((face['id'],'sface-2021dec',face['vector']) for face in confirmed)
        ids_path=base/'pet_vector_ids.json'
        if ids_path.exists():
            ids=json.loads(ids_path.read_text())
            for model in ('small','base'):
                features.extend((oid,'pet-'+model,vector) for oid,vector in zip(ids,np.load(base/f'pet_{model}_vectors.npy')))
        store.store_features(dataset,features)
        counts={table:store.db.execute('SELECT count(*) FROM '+table+' WHERE dataset=?',(dataset,)).fetchone()[0] for table in ('entities','observations','memberships','features')}
        print(json.dumps(dict(revision=revision,**counts)))
        return dataset
    finally:store.close()


if __name__=='__main__':import_data()
