"""Summarize exported human labels; never infer accuracy from unlabelled pairs."""
import argparse
import hashlib
import json
from pathlib import Path


def dataset_id(result):
    signature={'samples':[(s['id'],s.get('source_version')) for s in result['samples']], 'models':[(m['file'],m['sha256']) for m in result.get('models',[])], 'protocol':result.get('protocol')}
    return hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()[:16]


def evaluate(result, labels):
    if labels.get('dataset_id')!=dataset_id(result):raise ValueError('Review labels belong to another dataset')
    detections={p['id']:p for p in result['pets']};checks=labels.get('animal_checks',{})
    reviewed=[(detections[k],v) for k,v in checks.items() if k in detections and v in ('cat','dog','not_animal')]
    report={'detection_review':{'reviewed':len(reviewed),'correct_class_and_box':sum(p['kind']==v for p,v in reviewed),'total_candidates':len(detections),'note':'Only reviewed detections; misses/recall and whole-library accuracy are not measured.'},'pet_models':{}}
    for model in ('small','base'):
        pairs={}
        for row in result['comparisons']:
            if row['model']!=model:continue
            for match in row['matches']:
                key='|'.join(sorted([row['anchor'],match['id']]))
                truth=labels.get('pet_pairs',{}).get(key)
                if truth not in ('same','different'):continue
                pairs[key]=(match,truth)
        groups={}
        for name,subset in [('all',list(pairs.values())),('without_near_duplicates',[v for v in pairs.values() if not v[0]['near_duplicate']])]:
            groups[name]={'reviewed_unique_pairs':len(subset),'same_pet':sum(v=='same' for _,v in subset),'different_pet':sum(v=='different' for _,v in subset)}
        report['pet_models'][model]=groups
    report['limitations']='Selected top-3 candidates only; no calibrated decision threshold, unbiased recall, or model winner. Review a shared, diverse labelled set before choosing a model.'
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);parser.add_argument('--labels',type=Path,required=True);parser.add_argument('--out',type=Path)
    args=parser.parse_args();result=json.loads((args.run/'results.private.json').read_text(encoding='utf-8'));labels=json.loads(args.labels.read_text(encoding='utf-8-sig'))
    report=evaluate(result,labels);payload=json.dumps(report,ensure_ascii=False,indent=2)
    if args.out:args.out.write_text(payload,encoding='utf-8')
    print(payload)
