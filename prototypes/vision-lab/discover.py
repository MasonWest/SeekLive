"""Offline incremental discovery for explicitly followed, human-confirmed people.

No automatic identity assignments. Cached per-image features survive interruption.
"""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import time
import cv2
import numpy as np
from run import inventory,spread,read_image,jpeg,crop,unit,isolated_output,log
from prepare import verify
from evaluate import dataset_id


def profiles(result,labels,confirmed=None):
    if labels.get('dataset_id')!=dataset_id(result):raise ValueError('Labels belong to another dataset')
    groups={g['id']:g for g in result['face_groups']};links=labels.get('person_links',{})
    def root(g):
        seen=set()
        while links.get(g,g)!=g:
            if g in seen or links[g] not in groups:raise ValueError('Invalid identity links')
            seen.add(g);g=links[g]
        return g
    people={}
    for g in groups.values():people.setdefault(root(g['id']),[]).extend(g['members'])
    for face in confirmed or []:
        if face['person'] not in groups:raise ValueError('Unknown confirmed person')
        if labels.get('face_checks',{}).get(face['id'],'keep')=='keep':people.setdefault(root(face['person']),[]).append(face['id'])
    out=[]
    for pid,members in people.items():
        name=str(labels.get('group_names',{}).get(pid,'')).strip()
        status=labels.get('person_interest',{}).get(pid,'followed' if name else 'other')
        extra_ids={f['id'] for f in confirmed or []}
        refs=[f for f in members if labels.get('face_checks',{}).get(f,'keep' if f in extra_ids else '')=='keep']
        if status=='followed' and refs:out.append(dict(id=pid,name=name or pid,references=refs))
    return out


def rank_face(vector,people,reference_vectors):
    scores=[]
    for person in people:
        similarities=sorted((float(vector@v) for v in reference_vectors[person['id']]),reverse=True)
        # Two reference embeddings support a candidate; a single-reference
        # person needs a higher score. These are uncalibrated shortlist rules.
        best=similarities[0];second=similarities[1] if len(similarities)>1 else None
        score=best if second is None else .8*best+.2*second
        scores.append(dict(person=person['id'],score=score,best=best,second=second))
    scores.sort(key=lambda x:x['score'],reverse=True)
    if not scores:return None
    match=scores[0];margin=match['score']-(scores[1]['score'] if len(scores)>1 else -1)
    eligible=(match['best']>=.62 and margin>=.12) if match['second'] is None else (match['score']>=.55 and match['second']>=.45 and margin>=.08)
    if not eligible:return None
    return dict(person=match['person'],score=round(match['score'],4),margin=round(margin,4),priority='higher' if match['score']>=.72 and margin>=.15 else 'review',status='unverified')


def select_sources(sources,excluded,count):
    strata=collections.defaultdict(list)
    for source in sources:
        if source['id'] not in excluded:strata[(source['namespace'],str(Path(source['rel']).parent))].append(source)
    # Spread within each directory, then round-robin across directories.
    size=max(1,int(np.ceil(count/max(1,len(strata)))))
    buckets=[spread(v,min(len(v),size*3)) for _,v in sorted(strata.items())]
    selected=[]
    for depth in range(max(map(len,buckets),default=0)):
        for bucket in buckets:
            if depth<len(bucket):selected.append(bucket[depth])
            if len(selected)>=count:return selected
    return selected


def cache_valid(cached,version,signature,out):
    try:
        if not cached or cached['version']!=version or cached['signature']!=signature:return False
        if not (out/cached['preview']).is_file():return False
        for face in cached['faces']:
            vector=np.asarray(face['vector'])
            if vector.shape!=(128,) or not np.isfinite(vector).all() or not (out/face['crop']).is_file():return False
        return True
    except (KeyError,TypeError,ValueError):return False


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',type=Path,default=Path('tmp/vision-lab/combined'));parser.add_argument('--labels',type=Path,required=True);parser.add_argument('--out',type=Path,default=Path('tmp/vision-lab/discovery-001'));parser.add_argument('--count',type=int,default=800);parser.add_argument('--models',type=Path,default=Path('tmp/vision-lab/models'));parser.add_argument('--config',type=Path,default=Path('libraries.json'));parser.add_argument('--exclude-run',type=Path,action='append',default=[])
    parser.add_argument('--no-publish',action='store_true',help='Prepare a batch for aggregation without replacing the review page')
    args=parser.parse_args();started=time.perf_counter();out=args.out.resolve()
    config=json.loads(args.config.read_text(encoding='utf-8-sig'));isolated_output(out,[x['root'] for x in config['libraries']])
    if args.count<1:raise ValueError('count must be positive')
    models=verify(args.models);result=json.loads((args.base/'results.private.json').read_text(encoding='utf-8'));labels=json.loads(args.labels.read_text(encoding='utf-8-sig'))
    confirmed_path=args.base/'confirmed_discoveries.private.json'
    confirmed=json.loads(confirmed_path.read_text(encoding='utf-8')) if confirmed_path.exists() else dict(records=[],base_dataset_id=dataset_id(result))
    if confirmed['base_dataset_id']!=dataset_id(result):raise ValueError('Confirmed membership dataset mismatch')
    people=profiles(result,labels,confirmed['records'])
    expected={m['file']:m['sha256'] for m in result.get('models',[])}
    if any(expected.get(m['file'])!=m['sha256'] for m in models[:2]):raise ValueError('Reference/model mismatch')
    if not people:raise ValueError('No confirmed followed people')
    face_vectors=np.load(args.base/'face_vectors.npy');index={f['id']:i for i,f in enumerate(result['faces'])}
    extra_vectors={f['id']:unit(f['vector']) for f in confirmed['records']}
    integrated_ids=set(extra_vectors)
    refs={p['id']:[face_vectors[index[f]] if f in index else extra_vectors[f] for f in p['references']] for p in people}
    for folder in ('cache','previews','faces'):(out/folder).mkdir(parents=True,exist_ok=True)
    sources,stats=inventory(config,1000000);excluded={s['id'] for s in result['samples'] if s['face_scanned']}
    source_versions={s['id']:[s['size'],s['mtime_ns']] for s in sources}
    for folder in args.exclude_run:
        previous=json.loads((folder/'discovery.private.json').read_text(encoding='utf-8'))
        if previous['models']!=models[:2]:raise ValueError('Previous scan model mismatch')
        failed={x['sample'] for x in previous['failures']}
        for source in json.loads((folder/'samples.private.json').read_text(encoding='utf-8')):
            if source['id'] not in failed and source_versions.get(source['id'])==[source['size'],source['mtime_ns']]:excluded.add(source['id'])
    selected=select_sources(sources,excluded,args.count)
    (out/'samples.private.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'profiles.private.json').write_text(json.dumps(dict(base_dataset_id=dataset_id(result),people=[dict(p,vectors=[v.tolist() for v in refs[p['id']]]) for p in people],models=models[:2],protocol='confirmed-profiles-1'),ensure_ascii=False),encoding='utf-8')
    signature=hashlib.sha256(json.dumps({'models':[(m['file'],m['sha256']) for m in models[:2]],'decode':'1600-exif-draft-v1','detector':[.85,.3,28]},sort_keys=True).encode()).hexdigest()
    detector=cv2.FaceDetectorYN_create(str(args.models/'yunet.onnx'),'',(320,320),.85,.3,1000);recognizer=cv2.FaceRecognizerSF_create(str(args.models/'sface.onnx'),'')
    observations=[];failures=[];hits=0;decoded=0;detected_faces=0
    log(stage='discovery_start',selected=len(selected),followed=len(people),references=sum(len(p['references']) for p in people))
    for seq,source in enumerate(selected):
        path=out/'cache'/(source['id']+'.json');version=[source['size'],source['mtime_ns']]
        try:
            current=Path(source['path']).stat()
            if [current.st_size,current.st_mtime_ns]!=version:raise ValueError('Source changed before analysis')
            try:cached=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
            except (ValueError,OSError):cached=None
            if cache_valid(cached,version,signature,out):hits+=1
            else:
                image,captured,dhash=read_image(source['path']);bgr=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)
                preview='previews/'+source['id']+'.jpg';jpeg(image,out/preview)
                detector.setInputSize((image.width,image.height));_,detections=detector.detect(bgr);faces=[]
                for detection in ([] if detections is None else detections):
                    x,y,w,h=map(float,detection[:4])
                    if min(w,h)<28:continue
                    vector=unit(recognizer.feature(recognizer.alignCrop(bgr,detection)))
                    fid='N'+source['id']+'-'+str(len(faces));asset='faces/'+fid+'.jpg';crop(image,[x,y,x+w,y+h],out/asset,pad=.2)
                    faces.append(dict(id=fid,sample=source['id'],crop=asset,face_pixels=round(min(w,h)),box=[max(0,x)/image.width,max(0,y)/image.height,min(image.width,x+w)/image.width,min(image.height,y+h)/image.height],vector=vector.tolist()))
                cached=dict(version=version,signature=signature,preview=preview,captured=captured,dhash=dhash,faces=faces)
                current=Path(source['path']).stat()
                if [current.st_size,current.st_mtime_ns]!=version:raise ValueError('Source changed during analysis')
                partial=path.with_suffix('.part');partial.write_text(json.dumps(cached),encoding='utf-8');partial.replace(path)
            decoded+=1;detected_faces+=len(cached['faces'])
            # Only one candidate per followed identity within a source photo.
            per_person={}
            for face in cached['faces']:
                if face['id'] in integrated_ids:continue
                if face['face_pixels']<40:continue
                match=rank_face(unit(face['vector']),people,refs)
                if not match:continue
                record={k:v for k,v in face.items() if k!='vector'}
                record.update(match,preview=cached['preview'],captured=cached['captured'],dhash=cached['dhash'],source_version=version)
                if record['person'] not in per_person or per_person[record['person']]['score']<record['score']:per_person[record['person']]=record
            observations.extend(per_person.values())
        except Exception as exc:failures.append(dict(sample=source['id'],error=type(exc).__name__))
        if (seq+1)%25==0:log(stage='discovery',processed=seq+1,candidates=len(observations),failed=len(failures),cache_hits=hits,seconds=round(time.perf_counter()-started,1))
    shortlist=[];duplicates=0
    for person in people:
        kept=[]
        for record in sorted([x for x in observations if x['person']==person['id']],key=lambda x:-x['score']):
            if any((int(record['dhash'],16)^int(other['dhash'],16)).bit_count()<=4 for other in kept):duplicates+=1;continue
            kept.append(record)
        shortlist.extend(kept[:10])
    changed=[]
    for source in selected:
        try:
            st=Path(source['path']).stat()
            if [st.st_size,st.st_mtime_ns]!=[source['size'],source['mtime_ns']]:changed.append(source['id'])
        except OSError:changed.append(source['id'])
    if changed:raise RuntimeError('Sources changed; refuse to publish stale discovery')
    baseline_ids={s['id'] for s in result['samples']}
    summary=dict(selected=len(selected),decoded=decoded,new_to_sample=sum(s['id'] not in baseline_ids for s in selected),detected_faces=detected_faces,followed=len(people),references=sum(len(p['references']) for p in people),candidates=len(observations),shortlisted=len(shortlist),near_duplicates_suppressed=duplicates,failed=len(failures),cache_hits=hits,unchanged_sources=len(selected),seconds=round(time.perf_counter()-started,2),accuracy='unmeasured',automatic_assignments=0)
    export=dict(base_dataset_id=dataset_id(result),reference_signature=hashlib.sha256(json.dumps({'people':people,'models':models[:2]},sort_keys=True).encode()).hexdigest()[:16],people=people,summary=summary,candidates=shortlist,failures=failures,models=models[:2],protocol='followed-discovery-1')
    (out/'discovery.private.json').write_text(json.dumps(export,ensure_ascii=False,indent=2),encoding='utf-8');(out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    # Atomic pointer publication. No mutation of the original results or IDs.
    if not args.no_publish:
        pointer=args.base/'person_discovery.private.json';partial=pointer.with_suffix('.part');partial.write_text(json.dumps({'path':os.path.relpath(out/'discovery.private.json',args.base).replace('\\','/')}),encoding='utf-8');partial.replace(pointer)
        from review import build_review
        build_review(args.base,result)
    log(stage='discovery_complete',**summary)


if __name__=='__main__':main()
