"""One-time local cache migration; preserve current SQLite review state verbatim."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vision_db import VisionDB
from vision_inference import model_versions


def main():
    root=Path(__file__).resolve().parents[1];base=root/'tmp/vision-lab/combined';target=root/'data/vision/models'
    manifest=json.loads((root/'vision_models.json').read_text(encoding='utf-8'));target.mkdir(parents=True,exist_ok=True)
    for item in manifest:
        source=root/'tmp/vision-lab/models'/item['file'];out=target/item['file']
        if not out.exists():shutil.copyfile(source,out)
    versions=model_versions(target)
    result=json.loads((base/'results.private.json').read_text(encoding='utf-8'))
    import numpy as np
    vectors=np.load(base/'face_vectors.npy');faces={s['id']:[] for s in result['samples'] if s['face_scanned']}
    for face,vector in zip(result['faces'],vectors):faces[face['sample']].append(dict(id=face['id'],box=face['box'],vector=vector.tolist()))
    sample_versions={s['id']:s['source_version'] for s in result['samples']}
    rows={sid:(sid,json.dumps(sample_versions[sid],separators=(',',':')),versions['face'],json.dumps(items)) for sid,items in faces.items()}
    legacy_signature=hashlib.sha256(json.dumps({'models':[(m['file'],m['sha256']) for m in manifest[:2]],'decode':'1600-exif-draft-v1','detector':[.85,.3,28]},sort_keys=True).encode()).hexdigest()
    for folder in (root/'tmp/vision-lab').glob('discovery-*/cache'):
        for path in folder.glob('*.json'):
            cached=json.loads(path.read_text(encoding='utf-8'))
            if cached.get('signature')!=legacy_signature:continue
            if any(len(f['vector'])!=128 or not all(math.isfinite(v) for v in f['vector']) for f in cached['faces']):continue
            rows[path.stem]=(path.stem,json.dumps(cached['version'],separators=(',',':')),versions['face'],json.dumps(cached['faces']))
    store=VisionDB(root/'data/vision.db')
    try:
        before=store.db.execute('SELECT dataset,revision,state FROM review_state').fetchall()
        with store.db:
            store.db.executemany('INSERT OR IGNORE INTO vision_face_cache VALUES(?,?,?,?)',rows.values())
            for dataset,_,_ in before:
                raw=store.db.execute('SELECT catalog FROM datasets WHERE id=?',(dataset,)).fetchone();catalog=json.loads(raw[0])
                for obs in catalog['observations']:
                    if obs['sample'] in sample_versions:obs.setdefault('source_version',sample_versions[obs['sample']])
                    store.db.execute('UPDATE observations SET metadata=? WHERE dataset=? AND id=?',(json.dumps(obs,ensure_ascii=False),dataset,obs['id']))
                store.db.execute('UPDATE datasets SET catalog=? WHERE id=?',(json.dumps(catalog,ensure_ascii=False),dataset))
        after=store.db.execute('SELECT dataset,revision,state FROM review_state').fetchall()
        assert [tuple(r) for r in before]==[tuple(r) for r in after]
        print(json.dumps(dict(imported_face_caches=store.db.execute('SELECT count(*) FROM vision_face_cache').fetchone()[0],review_state_unchanged=True,models_verified=True)))
    finally:store.close()


if __name__=='__main__':main()
