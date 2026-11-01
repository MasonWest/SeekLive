"""Lazy-loaded local inference used only in the low-priority worker process."""
import hashlib
import json
import shutil
from pathlib import Path
from vision_pipeline import digest


def model_versions(folder):
    manifest=json.loads(Path(__file__).with_name('vision_models.json').read_text(encoding='utf-8'))
    for item in manifest:
        h=hashlib.sha256()
        with (Path(folder)/item['file']).open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
        if h.hexdigest()!=item['sha256']:raise ValueError('Vision model verification failed: '+item['file'])
    result=dict(full=digest(['vision-cpu-1',[(m['file'],m['sha256']) for m in manifest]]),face=digest(['face-exif1600-1',[(m['file'],m['sha256']) for m in manifest[:2]]]))
    if manifest[-1]['file']=='yolox_s.onnx':result['pet_upgrade_from']=digest(['vision-cpu-1',[(m['file'],m['sha256']) for m in manifest[:-1]]])
    return result


class Engine:
    def __init__(self,folder,assets):
        from vision_image import cv2,session
        self.assets=Path(assets);self.assets.mkdir(parents=True,exist_ok=True)
        self.asset_bytes=sum(p.stat().st_size for p in self.assets.rglob('*.jpg') if p.is_file())
        self.detector=cv2.FaceDetectorYN_create(str(Path(folder)/'yunet.onnx'),'',(320,320),.85,.3,1000)
        self.recognizer=cv2.FaceRecognizerSF_create(str(Path(folder)/'sface.onnx'),'')
        self.animals=session(Path(folder)/'yolox_nano.onnx');self.pet=session(Path(folder)/'pet_small.onnx')
        self.animals_recall=session(Path(folder)/'yolox_s.onnx')

    def analyse(self,source,hint,idle,legacy):
        from vision_image import np,cv2,read_image,crop,jpeg,unit,detect_pets,pet_embedding,combine_pet_detections
        root=Path(source['root']).resolve();path=(root/source['rel']).resolve()
        if not path.is_relative_to(root) or self.assets.resolve().is_relative_to(root):raise ValueError('Invalid vision paths')
        target=self.assets/source['id']/digest([source['size'],source['mtime_ns']])[:12];target.mkdir(parents=True,exist_ok=True)
        preview_used=bool(source.get('_animal_upgrade') and (target/'preview.jpg').is_file())
        idle()
        if preview_used:
            from PIL import Image
            with Image.open(target/'preview.jpg') as cached:image=cached.convert('RGB')
            captured=source.get('_upgrade_captured','')
        else:image,captured,_=read_image(path)
        bgr=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)
        # Derived images are optional and regenerable; feature extraction continues
        # when the bounded preview budget or free-space floor is reached.
        write_assets=self.asset_bytes<512*1024**2 and shutil.disk_usage(self.assets).free>1024**3
        if write_assets and not preview_used:
            preview=target/'preview.jpg';old=preview.stat().st_size if preview.exists() else 0;jpeg(image,preview);self.asset_bytes+=preview.stat().st_size-old
        observations=[]
        def record(oid,kind,box,vector):
            asset=target/(hashlib.sha256(oid.encode()).hexdigest()[:20]+'.jpg')
            if write_assets and self.asset_bytes<512*1024**2:
                old=asset.stat().st_size if asset.exists() else 0
                crop(image,[box[0]*image.width,box[1]*image.height,box[2]*image.width,box[3]*image.height],asset,pad=.2 if kind=='person' else 0);self.asset_bytes+=asset.stat().st_size-old
            return dict(id=oid,sample=source['id'],kind=kind,box=box,face_pixels=round(min((box[2]-box[0])*image.width,(box[3]-box[1])*image.height)),crop=str(asset) if asset.exists() else None,preview=str(target/'preview.jpg') if (target/'preview.jpg').exists() else None,captured=captured,source_version=[source['size'],source['mtime_ns']],vector=unit(vector).tolist() if vector is not None else None)
        idle()
        if source.get('_animal_upgrade'):
            observations.extend(source.get('_previous_people',[]))
        elif hint is not None:
            for face in hint:
                idle();observations.append(record(face['id'],'person',face['box'],face['vector']))
        else:
            self.detector.setInputSize((image.width,image.height));_,faces=self.detector.detect(bgr)
            for i,face in enumerate([] if faces is None else faces):
                idle();x,y,w,h=map(float,face[:4])
                if min(w,h)<28:continue
                box=[max(0,x)/image.width,max(0,y)/image.height,min(image.width,x+w)/image.width,min(image.height,y+h)/image.height]
                oid='VF-'+source['id']+'-'+str(i)
                observations.append(record(oid,'person',box,self.recognizer.feature(self.recognizer.alignCrop(bgr,face))))
        idle()
        if source.get('_animal_upgrade'):
            baseline=[(np.array([o['box'][0]*image.width,o['box'][1]*image.height,o['box'][2]*image.width,o['box'][3]*image.height]),o.get('score',.4),o['kind']) for o in source.get('_previous_animals',[])]
        else:baseline=detect_pets(self.animals,bgr)
        idle()
        # OpenCV Zoo's YOLOX-S uses RGB/640; the official Nano uses BGR/416.
        stronger=detect_pets(self.animals_recall,np.asarray(image),input_side=640,score_threshold=.35,min_side=12 if preview_used else 36);idle()
        combined=combine_pet_detections(stronger,baseline)
        if preview_used and combined:
            small_w,small_h=image.size;image,original_capture,_=read_image(path);captured=original_capture or captured
            combined=[(box*np.array([image.width/small_w,image.height/small_h,image.width/small_w,image.height/small_h]),score,kind) for box,score,kind in combined]
            combined=[(box,score,kind) for box,score,kind in combined if min(box[2:]-box[:2])>=36]
        used_legacy=set()
        for i,(box,score,kind) in enumerate(combined):
            idle();normalized=[float(box[0]/image.width),float(box[1]/image.height),float(box[2]/image.width),float(box[3]/image.height)]
            oid='VA2-'+source['id']+'-'+digest([source['size'],source['mtime_ns']])[:8]+'-'+digest([kind,[round(v,3) for v in normalized]])[:10]
            # Preserve an existing manually reviewed observation when its box overlaps.
            best=0
            for old in legacy:
                if old['kind']!=kind or old['id'] in used_legacy:continue
                if old.get('source_version') and old['source_version']!=[source['size'],source['mtime_ns']]:continue
                a=normalized;b=old['box'];intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
                union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
                overlap=intersection/max(union,1e-9)
                if overlap>.50 and overlap>best:oid=old['id'];best=overlap
            if best:used_legacy.add(oid)
            region=image.crop(tuple(float(v) for v in box))
            vector=pet_embedding(self.pet,region) if kind in ('cat','dog') else None
            item=record(oid,kind,normalized,vector);item['score']=score;observations.append(item)
        # A legacy box may overlap multiple detections; keep one observation identity.
        return list({o['id']:o for o in observations}.values())


def match(observations,reference_rows):
    from vision_image import np,unit
    groups={}
    for reference in reference_rows:groups.setdefault((reference['entity'],reference['kind']),[]).append(reference['vector'])
    for key,vectors in groups.items():
        matrix=np.asarray(vectors,dtype=np.float32);groups[key]=matrix/np.maximum(np.linalg.norm(matrix,axis=1,keepdims=True),1e-12)
    suggestions=[]
    for observation in observations:
        if observation['vector'] is None or observation['kind']=='bird':continue
        if observation['kind']=='person' and observation.get('face_pixels',40)<40:continue
        vector=unit(observation['vector']);candidates=[]
        for (entity,kind),vectors in groups.items():
            if kind!=observation['kind']:continue
            scores=sorted((vectors@vector).tolist(),reverse=True)
            score=scores[0]*.8+(scores[1] if len(scores)>1 else scores[0])*.2
            candidates.append((score,scores,entity))
        candidates.sort(reverse=True)
        if not candidates:continue
        score,scores,entity=candidates[0];margin=score-(candidates[1][0] if len(candidates)>1 else -1)
        eligible=(score>=.62 and margin>=.12) if len(scores)==1 else (score>=.55 and scores[1]>=.45 and margin>=.08)
        if observation['kind'] in ('cat','dog'):
            # This produces human-review candidates, never confirmed ownership.
            # A strict .80 cutoff suppressed cross-date pet matches entirely.
            eligible=(score>=.65 and margin>=.12) if len(scores)==1 else (score>=.45 and scores[1]>=.40 and margin>=.08)
        if eligible:suggestions.append(dict(observation=observation['id'],entity=entity,score=round(score,4)))
    best={}
    for item in suggestions:
        if item['entity'] not in best or item['score']>best[item['entity']]['score']:best[item['entity']]=item
    return list(best.values())
