"""Combine offline runs without reading photo pixels again. First run wins duplicates."""
import argparse
import copy
import json
import os
from pathlib import Path
import numpy as np
from run import conservative_groups, isolated_output
from prepare import verify
from review import build_review


def combine(folders, out, models):
    manifest=verify(models)
    runs=[json.loads((folder/'results.private.json').read_text(encoding='utf-8')) for folder in folders]
    sources={}; owners={}; samples=[]; faces=[]; pets=[]; fv=[]; pv={'small':[], 'base':[]}; chosen=[]
    for index,(folder,result) in enumerate(zip(folders,runs)):
        private=json.loads((folder/'samples.private.json').read_text(encoding='utf-8'))
        for source in private:
            sources.setdefault(source['id'],source)
        if result.get('models') and result['models']!=manifest:raise ValueError('Incompatible models')
        def asset(path):return os.path.relpath(folder/path,out).replace('\\','/')
        accepted=set()
        prefix=f'R{index+1}-'
        for original in result['samples']:
            if original['id'] in owners:continue
            owners[original['id']]=index;accepted.add(original['id'])
            s=copy.deepcopy(original);s['preview']=asset(s['preview'])
            source=sources[s['id']];s['source_version']=[source['size'],source['mtime_ns']]
            if not s['captured']:s['date_source']='unknown'
            s['faces']=[prefix+x for x in s['faces']];s['pets']=[prefix+x for x in s['pets']]
            samples.append(s)
        face_vectors=np.load(folder/'face_vectors.npy')
        for i,original in enumerate(result['faces']):
            if original['sample'] not in accepted:continue
            face=copy.deepcopy(original);face['id']=prefix+face['id'];face['crop']=asset(face['crop'])
            faces.append(face);fv.append(face_vectors[i])
        pet_map={}
        for original in result['pets']:
            if original['sample'] not in accepted:continue
            pet=copy.deepcopy(original);pet['id']=prefix+pet['id'];pet['crop']=asset(pet['crop'])
            pets.append(pet);pet_map[original['id']]=pet
        ids_path=folder/'pet_vector_ids.json'
        # Earlier runner stored anchors in precisely the vector extraction order.
        ids=json.loads(ids_path.read_text()) if ids_path.exists() else [r['anchor'] for r in result['comparisons'] if r['model']=='small']
        vectors={m:np.load(folder/f'pet_{m}_vectors.npy') for m in pv} if ids else {}
        if any(len(v)!=len(ids) for v in vectors.values()):raise ValueError('Vector ID alignment mismatch')
        for i,old_id in enumerate(ids):
            if old_id not in pet_map:continue
            chosen.append(pet_map[old_id])
            for m in pv:pv[m].append(vectors[m][i])
    # The output may reference previews, but must never be written into an original library.
    isolated_output(out,[Path(s['path']).parent for s in sources.values()])
    out.mkdir(parents=True,exist_ok=True)
    fv=np.asarray(fv,np.float32);np.save(out/'face_vectors.npy',fv)
    groups=conservative_groups(faces,fv,.55)
    face_groups=[dict(id=f'P{i+1:03}',members=[faces[j]['id'] for j in g],status='unverified') for i,g in enumerate(groups)]
    lookup={s['id']:s for s in samples};comparisons=[];pet_groups={}
    for m,rows in pv.items():
        if not rows:continue
        vectors=np.stack(rows);np.save(out/f'pet_{m}_vectors.npy',vectors);sim=vectors@vectors.T
        for i,a in enumerate(chosen):
            matches=[]
            for j,b in enumerate(chosen):
                if i==j or a['sample']==b['sample'] or a['kind']!=b['kind']:continue
                sa,sb=lookup[a['sample']],lookup[b['sample']]
                matches.append(dict(id=b['id'],cosine=round(float(sim[i,j]),4),near_duplicate=(int(sa['dhash'],16)^int(sb['dhash'],16)).bit_count()<=4,same_date=bool(sa['captured'] and sa['captured'][:10]==sb['captured'][:10])))
            matches.sort(key=lambda x:(x['near_duplicate'],-x['cosine']))
            comparisons.append(dict(model=m,anchor=a['id'],matches=matches[:3]))
        pet_groups[m]=[dict(id=f'{m}-T{i+1:03}',kind=chosen[g[0]]['kind'],members=[chosen[j]['id'] for j in g],status='unverified') for i,g in enumerate(conservative_groups(chosen,vectors,.8))]
    changed=[]
    for source in sources.values():
        try:
            st=Path(source['path']).stat()
            if [st.st_size,st.st_mtime_ns]!=[source['size'],source['mtime_ns']]:changed.append(source['id'])
        except OSError:changed.append(source['id'])
    if changed:raise RuntimeError('Sources changed since runs; do not combine stale results')
    summary=dict(selected=len(sources),decoded=len(samples),face_images=sum(s['face_scanned'] for s in samples),faces=len(faces),face_groups=len(groups),multi_face_groups=sum(len(g)>1 for g in groups),animals=len(pets),cats=sum(p['kind']=='cat' for p in pets),dogs=sum(p['kind']=='dog' for p in pets),pet_comparison_count=len(chosen),failures=len(sources)-len(samples),unchanged_sources=len(sources),changed_or_unavailable_sources=changed,providers=['CPUExecutionProvider'],seconds=round(sum(r['summary']['seconds'] for r in runs),2),timings={},timing_scope='sum_of_source_runs_not_combined_wall_time',source_runs=[r['summary'] for r in runs],accuracy='not_measured_requires_human_labels')
    result=dict(summary=summary,samples=samples,faces=faces,pets=pets,face_groups=face_groups,pet_groups=pet_groups,comparisons=comparisons,failures=[],models=manifest,protocol='seeklive-vision-lab-1-combined',model_verification='current_files_verified_at_combine; earlier_run_models_recorded_after_run')
    for name,value in [('results.private.json',result),('summary.json',summary),('samples.private.json',list(sources.values())),('pet_vector_ids.json',[p['id'] for p in chosen])]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    build_review(out,result)
    print(json.dumps({k:v for k,v in summary.items() if k!='source_runs'},ensure_ascii=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('runs',nargs='+',type=Path);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--models',type=Path,default=Path('tmp/vision-lab/models'))
    args=parser.parse_args();combine([p.resolve() for p in args.runs],args.out.resolve(),args.models)
