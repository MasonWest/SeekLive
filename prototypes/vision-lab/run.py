"""Offline-only, read-only photo analysis prototype. Outputs must stay outside git.

No imports from the production server; no network operations; CPU sessions only.
"""
import argparse
import base64
import collections
import datetime as dt
import gc
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics
import time

os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('HF_HUB_OFFLINE', '1')
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass
ort.disable_telemetry_events()
cv2.setNumThreads(2)

EXTS={'.jpg','.jpeg','.heic','.heif','.png','.webp'}
SKIP={'.git','cache','data','trash','$recycle.bin','system volume information','@eadir'}


def log(**data):
    print(json.dumps(data,ensure_ascii=True),flush=True)


def spread(values, count):
    if len(values)<=count:return list(values)
    return [values[min(len(values)-1,int((i+.5)*len(values)/count))] for i in range(count)]


def inventory(config, count):
    groups=collections.defaultdict(list);seen=set();errors=[]
    for pos,lib in enumerate(config['libraries']):
        root=Path(lib['root']).resolve()
        namespace=hashlib.sha256(str(root).casefold().encode()).hexdigest()[:12]
        if not root.is_dir():errors.append({'library':pos,'error':'offline'});continue
        def onerror(exc):errors.append({'library':pos,'error':type(exc).__name__})
        for folder,dirs,files in os.walk(root,followlinks=False,onerror=onerror):
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d.casefold() not in SKIP and not Path(folder,d).is_symlink())
            for name in sorted(files):
                path=Path(folder,name)
                if path.suffix.casefold() not in EXTS or path.is_symlink():continue
                absolute=str(path).casefold()
                if absolute in seen:continue
                seen.add(absolute)
                try:st=path.stat()
                except OSError as exc:errors.append({'library':pos,'error':type(exc).__name__});continue
                rel=path.relative_to(root).as_posix()
                uid=hashlib.sha256((namespace+'\0'+rel).encode()).hexdigest()[:16]
                groups[(pos,str(Path(rel).parent))].append(dict(id=uid,library=pos,namespace=namespace,path=str(path),rel=rel,size=st.st_size,mtime_ns=st.st_mtime_ns))
    # Round-robin over directory strata so one large imported folder cannot dominate.
    keys=sorted(groups);selected=[];used=set();depth=0
    per_group={k:spread(v,min(len(v),max(12,math.ceil(count/max(1,len(keys))))*3)) for k,v in groups.items()}
    while len(selected)<count:
        added=0
        for k in keys:
            if depth<len(per_group[k]):
                item=per_group[k][depth]
                if item['id'] not in used:selected.append(item);used.add(item['id']);added+=1
                if len(selected)>=count:break
        if not added:break
        depth+=1
    return selected,dict(total_images=sum(map(len,groups.values())),directories=len(groups),sources=len(config['libraries']),errors=errors)


def session(path):
    opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
    return ort.InferenceSession(str(path),sess_options=opts,providers=['CPUExecutionProvider'])


def jpeg(image, path, side=640):
    im=image.copy();im.thumbnail((side,side),Image.Resampling.LANCZOS)
    im.save(path,'JPEG',quality=82)


def read_image(path):
    with Image.open(path) as original:
        ex=original.getexif();stamp=''
        try:
            exif=ex.get_ifd(34665)
            stamp=exif.get(36867) or ex.get(306) or ''
        except Exception:pass
        # Draft decoding reduces JPEG working set; HEIC still requires decoding.
        original.draft('RGB',(1800,1800))
        image=ImageOps.exif_transpose(original).convert('RGB')
    image.thumbnail((1600,1600),Image.Resampling.LANCZOS)
    tiny=np.asarray(image.resize((9,8)).convert('L'))
    bits=(tiny[:,1:]>tiny[:,:-1]).ravel()
    dhash=sum(int(v)<<i for i,v in enumerate(bits))
    return image,str(stamp),f'{dhash:016x}'


def crop(image, box, path, pad=.12):
    x1,y1,x2,y2=box;w=x2-x1;h=y2-y1
    region=image.crop((max(0,x1-w*pad),max(0,y1-h*pad),min(image.width,x2+w*pad),min(image.height,y2+h*pad)))
    jpeg(region,path,224)
    return region


def boxes_nms(boxes,scores,iou=.45):
    order=np.argsort(scores)[::-1];keep=[]
    while len(order):
        i=int(order[0]);keep.append(i)
        rest=order[1:]
        if not len(rest):break
        lo=np.maximum(boxes[i,:2],boxes[rest,:2]);hi=np.minimum(boxes[i,2:],boxes[rest,2:])
        intersection=np.prod(np.maximum(0,hi-lo),axis=1)
        area=np.prod(np.maximum(0,boxes[i,2:]-boxes[i,:2]))
        other=np.prod(np.maximum(0,boxes[rest,2:]-boxes[rest,:2]),axis=1)
        overlap=intersection/np.maximum(area+other-intersection,1e-6)
        order=rest[overlap<=iou]
    return keep


def detect_pets(net,bgr,kinds=(14,15,16)):
    h,w=bgr.shape[:2];scale=min(416/h,416/w)
    resized=cv2.resize(bgr,(int(w*scale),int(h*scale)))
    padded=np.full((416,416,3),114,dtype=np.uint8);padded[:resized.shape[0],:resized.shape[1]]=resized
    tensor=np.ascontiguousarray(padded.transpose(2,0,1)[None],dtype=np.float32)
    predictions=net.run(None,{net.get_inputs()[0].name:tensor})[0][0].copy()
    grids=[];strides=[]
    for stride in (8,16,32):
        yy,xx=np.meshgrid(np.arange(416//stride),np.arange(416//stride),indexing='ij')
        grids.append(np.stack((xx,yy),axis=-1).reshape(-1,2));strides.append(np.full((len(grids[-1]),1),stride))
    grid=np.concatenate(grids);stride=np.concatenate(strides)
    if len(predictions)!=len(grid):raise ValueError('Unexpected YOLOX output shape')
    predictions[:,:2]=(predictions[:,:2]+grid)*stride
    predictions[:,2:4]=np.exp(np.clip(predictions[:,2:4],-20,20))*stride
    probabilities=predictions[:,4,None]*predictions[:,5:]
    classes=probabilities.argmax(axis=1);scores=probabilities.max(axis=1)
    mask=np.isin(classes,kinds)&(scores>=.40)
    centers=predictions[mask,:2];sizes=predictions[mask,2:4];scores=scores[mask];classes=classes[mask]
    boxes=np.concatenate((centers-sizes/2,centers+sizes/2),axis=1)/scale
    boxes[:,[0,2]]=np.clip(boxes[:,[0,2]],0,w);boxes[:,[1,3]]=np.clip(boxes[:,[1,3]],0,h)
    result=[]
    for kind in kinds:
        idx=np.flatnonzero(classes==kind)
        for local in boxes_nms(boxes[idx],scores[idx]):
            i=idx[local];box=boxes[i]
            if min(box[2:]-box[:2])<36:continue
            result.append((box,float(scores[i]),{14:'bird',15:'cat',16:'dog'}[kind]))
    return result


def unit(vector):
    vector=np.asarray(vector,dtype=np.float32).ravel()
    if not np.all(np.isfinite(vector)):raise ValueError('Nonfinite embedding')
    return vector/max(float(np.linalg.norm(vector)),1e-12)


def conservative_groups(records,vectors,threshold):
    # Complete-link at this sample size prevents chaining. Same-image ambiguity is
    # left split for human review (mirrors/collages can be merged by the reviewer).
    groups=[]
    for i,record in enumerate(records):
        best=None;best_score=-2
        for group in groups:
            if any(records[j]['sample']==record['sample'] for j in group):continue
            if record['kind']!=records[group[0]]['kind']:continue
            score=min(float(vectors[i]@vectors[j]) for j in group)
            if score>=threshold and score>best_score:best=group;best_score=score
        if best is None:groups.append([i])
        else:best.append(i)
    return sorted(groups,key=lambda g:(-len(g),g[0]))


def pet_embedding(net,region):
    rgb=np.asarray(region.resize((224,224),Image.Resampling.BICUBIC),dtype=np.float32)/255
    rgb=(rgb-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
    tensor=np.ascontiguousarray(rgb.transpose(2,0,1)[None])
    return unit(net.run(None,{net.get_inputs()[0].name:tensor})[0])


def summarize(values):
    return dict(n=len(values),p50=round(float(np.percentile(values,50)),4),p95=round(float(np.percentile(values,95)),4),total=round(sum(values),3)) if values else {'n':0}


def isolated_output(out,roots):
    for root in roots:
        if out.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('Prototype output cannot be inside a source photo library')


def peak_working_set():
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('peak','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
    stats=Counters();stats.cb=ctypes.sizeof(stats)
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
    psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(stats),stats.cb):return stats.peak
    return None


def main():
    wall_started=time.perf_counter()
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=Path('libraries.json'))
    parser.add_argument('--out',type=Path,default=Path('tmp/vision-lab/run-001'))
    parser.add_argument('--models',type=Path,default=Path('tmp/vision-lab/models'))
    parser.add_argument('--screen',type=int,default=480)
    parser.add_argument('--faces',type=int,default=180)
    parser.add_argument('--max-pets',type=int,default=60)
    parser.add_argument('--sample-list',type=Path,help='Private manifest of an explicitly selected subset')
    args=parser.parse_args();out=args.out.resolve()
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    isolated_output(out,[lib['root'] for lib in config['libraries']])
    from prepare import verify
    models=verify(args.models)
    out.mkdir(parents=True,exist_ok=True)
    for sub in ('previews','faces','pets'): (out/sub).mkdir(exist_ok=True)
    if args.sample_list:
        selected=json.loads(args.sample_list.read_text(encoding='utf-8'))
        roots=[Path(lib['root']).resolve() for lib in config['libraries']]
        if any(not any(Path(item['path']).resolve().is_relative_to(root) for root in roots) for item in selected):
            raise ValueError('Selected photo is outside configured source libraries')
        inventory_stats=dict(total_images=len(selected),directories=len({str(Path(x['path']).parent) for x in selected}),sources=len({x['namespace'] for x in selected}),errors=[],selection='explicit_subset')
    else:selected,inventory_stats=inventory(config,args.screen)
    (out/'samples.private.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
    log(stage='inventory',selected=len(selected),**{k:v for k,v in inventory_stats.items() if k!='errors'})
    face_ids=set(x['id'] for x in spread(selected,min(args.faces,len(selected))))
    detector=cv2.FaceDetectorYN_create(str(args.models/'yunet.onnx'),'',(320,320),.85,.3,1000)
    recognizer=cv2.FaceRecognizerSF_create(str(args.models/'sface.onnx'),'')
    pet_net=session(args.models/'yolox_nano.onnx')
    samples=[];faces=[];pets=[];face_vectors=[];timings=collections.defaultdict(list);failures=[]
    started=time.perf_counter()
    for seq,source in enumerate(selected):
        t_image=t=time.perf_counter()
        face_start,pet_start,vector_start=len(faces),len(pets),len(face_vectors)
        try:
            image,captured,dhash=read_image(source['path']);bgr=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)
            timings['decode'].append(time.perf_counter()-t)
            sample=dict(id=source['id'],library=source['library'],captured=captured,date_source='exif' if captured else 'unknown',source_version=[source['size'],source['mtime_ns']],dhash=dhash,width=image.width,height=image.height,preview='previews/'+source['id']+'.jpg',faces=[],pets=[],face_scanned=source['id'] in face_ids)
            jpeg(image,out/sample['preview'])
            if source['id'] in face_ids:
                t=time.perf_counter();detector.setInputSize((image.width,image.height));_,detections=detector.detect(bgr)
                for detection in ([] if detections is None else detections):
                    x,y,w,h=map(float,detection[:4])
                    if min(w,h)<28:continue
                    aligned=recognizer.alignCrop(bgr,detection);vector=unit(recognizer.feature(aligned))
                    fid='F'+str(len(faces)+1).zfill(4);box=[x,y,x+w,y+h];path='faces/'+fid+'.jpg'
                    crop(image,box,out/path,pad=.20)
                    face=dict(id=fid,sample=source['id'],kind='person',score=round(float(detection[-1]),4),box=[max(0,x)/image.width,max(0,y)/image.height,min(image.width,x+w)/image.width,min(image.height,y+h)/image.height],crop=path,face_pixels=round(min(w,h)),date=captured)
                    faces.append(face);face_vectors.append(vector);sample['faces'].append(fid)
                timings['face'].append(time.perf_counter()-t)
            t=time.perf_counter();detections=detect_pets(pet_net,bgr);timings['animal_detection'].append(time.perf_counter()-t)
            for box,score,kind in detections:
                pid='A'+str(len(pets)+1).zfill(4);path='pets/'+pid+'.jpg';crop(image,box,out/path,pad=0)
                pet=dict(id=pid,sample=source['id'],kind=kind,score=round(score,4),box=[float(box[0]/image.width),float(box[1]/image.height),float(box[2]/image.width),float(box[3]/image.height)],crop=path,date=captured)
                pets.append(pet);sample['pets'].append(pid)
            current=os.stat(source['path'])
            if current.st_size!=source['size'] or current.st_mtime_ns!=source['mtime_ns']:raise RuntimeError('Source changed while reading; discarded sample')
            samples.append(sample)
            timings['image_total'].append(time.perf_counter()-t_image)
        except Exception as exc:
            del faces[face_start:];del pets[pet_start:];del face_vectors[vector_start:]
            failures.append({'sample':source['id'],'type':type(exc).__name__,'message':str(exc)[:300]})
        if (seq+1)%20==0:log(stage='screening',processed=seq+1,faces=len(faces),animals=len(pets),failed=len(failures),seconds=round(time.perf_counter()-started,1))
        # Incremental checkpoint survives a failed model or interrupted later stage.
        if (seq+1)%40==0:
            (out/'checkpoint.json').write_text(json.dumps({'samples':samples,'faces':faces,'pets':pets,'failures':failures},ensure_ascii=False),encoding='utf-8')
    face_vectors=np.asarray(face_vectors,dtype=np.float32)
    np.save(out/'face_vectors.npy',face_vectors)
    groups=conservative_groups(faces,face_vectors,.55) if len(faces) else []
    face_groups=[{'id':'P'+str(i+1).zfill(3),'members':[faces[j]['id'] for j in group],'status':'unverified'} for i,group in enumerate(groups)]
    # Space the animal subset across the sampled sequence, rather than selecting
    # only the first burst. Keep cat/dog strata represented.
    cats=[p for p in pets if p['kind']=='cat'];dogs=[p for p in pets if p['kind']=='dog']
    chosen=spread(cats,min(len(cats),args.max_pets//2))+spread(dogs,min(len(dogs),args.max_pets//2))
    used={p['id'] for p in chosen}
    chosen+=spread([p for p in pets if p['id'] not in used and p['kind'] in ('cat','dog')],max(0,args.max_pets-len(chosen))) if len(chosen)<args.max_pets else []
    del detector,recognizer,pet_net;gc.collect()
    (out/'pet_vector_ids.json').write_text(json.dumps([p['id'] for p in chosen]),encoding='utf-8')
    vectors_by_model={}
    for model_name in ('small','base'):
        if not chosen:break
        net=session(args.models/f'pet_{model_name}.onnx');vectors=[]
        for i,animal in enumerate(chosen):
            t=time.perf_counter()
            with Image.open(out/animal['crop']) as region:vector=pet_embedding(net,region.convert('RGB'))
            vectors.append(vector);timings['pet_'+model_name].append(time.perf_counter()-t)
            if (i+1)%10==0:log(stage='pet_'+model_name,processed=i+1,total=len(chosen))
        vectors_by_model[model_name]=np.stack(vectors);np.save(out/f'pet_{model_name}_vectors.npy',vectors_by_model[model_name])
        del net;gc.collect()
    comparisons=[];pet_groups={}
    sample_lookup={s['id']:s for s in samples}
    for model_name,vectors in vectors_by_model.items():
        similarities=vectors@vectors.T
        for i,animal in enumerate(chosen):
            candidates=[]
            for j,other in enumerate(chosen):
                if i==j or animal['sample']==other['sample'] or animal['kind']!=other['kind']:continue
                a=sample_lookup[animal['sample']];b=sample_lookup[other['sample']]
                near_duplicate=(int(a['dhash'],16)^int(b['dhash'],16)).bit_count()<=4
                candidates.append(dict(id=other['id'],cosine=round(float(similarities[i,j]),4),near_duplicate=near_duplicate,same_date=bool(a['captured'] and a['captured'][:10]==b['captured'][:10])))
            candidates.sort(key=lambda x:(x['near_duplicate'],-x['cosine']))
            comparisons.append(dict(model=model_name,anchor=animal['id'],matches=candidates[:3]))
        clusters=conservative_groups(chosen,vectors,.80)
        pet_groups[model_name]=[{'id':model_name+'-T'+str(i+1).zfill(3),'kind':chosen[g[0]]['kind'],'members':[chosen[j]['id'] for j in g],'status':'unverified'} for i,g in enumerate(clusters)]
    unchanged=0;changed=[]
    for source in selected:
        try:
            st=os.stat(source['path'])
            if st.st_size==source['size'] and st.st_mtime_ns==source['mtime_ns']:unchanged+=1
            else:changed.append(source['id'])
        except OSError:changed.append(source['id'])
    summary=dict(inventory=inventory_stats,selected=len(selected),decoded=len(samples),face_images=sum(s['face_scanned'] for s in samples),faces=len(faces),face_groups=len(groups),multi_face_groups=sum(len(g)>1 for g in groups),animals=len(pets),cats=len(cats),dogs=len(dogs),pet_comparison_count=len(chosen),failures=len(failures),unchanged_sources=unchanged,changed_or_unavailable_sources=changed,providers=['CPUExecutionProvider'],seconds=round(time.perf_counter()-wall_started,2),analysis_seconds=round(time.perf_counter()-started,2),timings={k:summarize(v) for k,v in timings.items()},versions=dict(opencv=cv2.__version__,onnxruntime=ort.__version__,numpy=np.__version__),thresholds=dict(face_complete_link=.55,pet_complete_link=.80),accuracy='not_measured_requires_human_labels')
    summary['peak_working_set_bytes']=peak_working_set()
    result=dict(summary=summary,samples=samples,faces=faces,pets=pets,face_groups=face_groups,pet_groups=pet_groups,comparisons=comparisons,failures=failures,models=models,protocol='seeklive-vision-lab-1')
    (out/'results.private.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    from review import build_review
    build_review(out,result)
    log(stage='complete',**{k:summary[k] for k in ('decoded','faces','animals','cats','dogs','face_groups','pet_comparison_count','seconds','failures')})


if __name__=='__main__':main()
