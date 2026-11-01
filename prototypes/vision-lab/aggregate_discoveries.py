"""Publish several compatible discovery batches without replacing old results."""
import argparse
import copy
import json
import os
from pathlib import Path
from evaluate import dataset_id
from review import build_review
from run import isolated_output


def curate(records,limit=12):
    # Alternate batches, newest first, to keep new discoveries visible even
    # when older easy matches have higher scores. Collapse coarse duplicates.
    buckets={}
    for record in records:buckets.setdefault(record['source_run'],[]).append(record)
    for bucket in buckets.values():bucket.sort(key=lambda x:-x['score'])
    chosen=[];seen=set();order=list(buckets)[::-1]
    while len(chosen)<limit:
        added=False
        for name in order:
            bucket=buckets[name]
            while bucket:
                record=bucket.pop(0)
                if record['id'] in seen:continue
                if any((int(record['dhash'],16)^int(other['dhash'],16)).bit_count()<=4 for other in chosen):continue
                chosen.append(record);seen.add(record['id']);added=True;break
            if len(chosen)>=limit:break
        if not added:break
    return chosen


def aggregate(folders,out,base):
    result=json.loads((base/'results.private.json').read_text(encoding='utf-8'))
    inputs=[json.loads((folder/'discovery.private.json').read_text(encoding='utf-8')) for folder in folders]
    if not inputs:raise ValueError('No discovery inputs')
    reference=inputs[0]
    for run in inputs:
        if run['base_dataset_id']!=dataset_id(result) or run['reference_signature']!=reference['reference_signature'] or run['models']!=reference['models']:
            raise ValueError('Discovery references/models differ; reprofile cached batches first')
    sources={};records=[];ids=set()
    for folder,run in zip(folders,inputs):
        for source in json.loads((folder/'samples.private.json').read_text(encoding='utf-8')):
            if source['id'] in sources:raise ValueError('Overlapping batches; avoid double-counting scan coverage')
            sources[source['id']]=source
        for original in run['candidates']:
            if original['id'] in ids:raise ValueError('Duplicate face observation')
            ids.add(original['id']);record=copy.deepcopy(original);record['source_run']=folder.name
            for field in ('crop','preview'):record[field]=os.path.relpath(folder/record[field],out).replace('\\','/')
            records.append(record)
    isolated_output(out,[Path(source['path']).parent for source in sources.values()])
    for source in sources.values():
        stat=Path(source['path']).stat()
        if [stat.st_size,stat.st_mtime_ns]!=[source['size'],source['mtime_ns']]:raise ValueError('Source changed since analysis')
    candidates=[]
    for person in reference['people']:candidates.extend(curate([r for r in records if r['person']==person['id']]))
    summaries=[r['summary'] for r in inputs];old_ids={s['id'] for s in result['samples']}
    summary=dict(selected=len(sources),decoded=sum(s['decoded'] for s in summaries),new_to_sample=sum(uid not in old_ids for uid in sources),detected_faces=sum(s['detected_faces'] for s in summaries),followed=len(reference['people']),references=summaries[0]['references'],shortlisted=len(candidates),candidates=sum(s['candidates'] for s in summaries),failed=sum(s['failed'] for s in summaries),batch_count=len(inputs),new_batch_images=summaries[-1]['selected'],unchanged_sources=len(sources),automatic_assignments=0,accuracy='unmeasured',source_runs=summaries)
    export=dict(base_dataset_id=dataset_id(result),reference_signature=reference['reference_signature'],people=reference['people'],summary=summary,candidates=candidates,models=reference['models'],protocol='followed-discovery-aggregate-1')
    out.mkdir(parents=True,exist_ok=True)
    (out/'discovery.private.json').write_text(json.dumps(export,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (out/'samples.private.json').write_text(json.dumps(list(sources.values()),ensure_ascii=False),encoding='utf-8')
    pointer=base/'person_discovery.private.json';part=pointer.with_suffix('.part');part.write_text(json.dumps({'path':os.path.relpath(out/'discovery.private.json',base).replace('\\','/')}),encoding='utf-8');part.replace(pointer)
    build_review(base,result)
    print(json.dumps({k:v for k,v in summary.items() if k!='source_runs'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('runs',nargs='+',type=Path);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--base',type=Path,default=Path('tmp/vision-lab/combined'))
    args=parser.parse_args();aggregate([p.resolve() for p in args.runs],args.out.resolve(),args.base.resolve())
