"""Extend original review samples with bird detections using local previews only."""
import json
import cv2
from pathlib import Path
from PIL import Image
import numpy as np
from run import session,detect_pets,crop
from prepare import verify
from evaluate import dataset_id


def main():
    base=Path('tmp/vision-lab/combined');out=base/'animal-assets';out.mkdir(exist_ok=True)
    result=json.loads((base/'results.private.json').read_text(encoding='utf-8'));models=verify(Path('tmp/vision-lab/models'))
    detector=session(Path('tmp/vision-lab/models/yolox_nano.onnx'));extra=[]
    for sample in result['samples']:
        with Image.open(base/sample['preview']) as opened:image=opened.convert('RGB')
        bgr=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)
        for seq,(box,score,kind) in enumerate(detect_pets(detector,bgr,kinds=(14,))):
            aid='B-'+sample['id']+'-'+str(seq);asset='animal-assets/'+aid+'.jpg';crop(image,box,base/asset,pad=0)
            extra.append(dict(id=aid,sample=sample['id'],kind=kind,score=round(score,4),box=[float(box[0]/image.width),float(box[1]/image.height),float(box[2]/image.width),float(box[3]/image.height)],crop=asset,date=sample.get('captured',''),identity_model=None))
    payload=dict(base_dataset_id=dataset_id(result),pets=extra,model=next(m for m in models if m['file']=='yolox_nano.onnx'),note='Bird detection only; no bird identity embedding used; derived from existing previews')
    (base/'animal_detections.private.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(previews_processed=len(result['samples']),bird_candidates=len(extra))))


if __name__=='__main__':main()
