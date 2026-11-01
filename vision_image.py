"""Local CPU image primitives, shared protocol with the validated prototype."""
import os
os.environ.setdefault('OMP_NUM_THREADS','2')
os.environ.setdefault('OPENCV_LOG_LEVEL','ERROR')
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image,ImageOps
try:
 import pillow_heif
 pillow_heif.register_heif_opener()
except ImportError:pass
ort.disable_telemetry_events()
cv2.setNumThreads(2)

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

def detect_pets(net,bgr,kinds=(14,15,16),input_side=416,score_threshold=.40,min_side=36):
    h,w=bgr.shape[:2];scale=min(input_side/h,input_side/w)
    resized=cv2.resize(bgr,(int(w*scale),int(h*scale)))
    padded=np.full((input_side,input_side,3),114,dtype=np.uint8);padded[:resized.shape[0],:resized.shape[1]]=resized
    tensor=np.ascontiguousarray(padded.transpose(2,0,1)[None],dtype=np.float32)
    predictions=net.run(None,{net.get_inputs()[0].name:tensor})[0][0].copy()
    grids=[];strides=[]
    for stride in (8,16,32):
        yy,xx=np.meshgrid(np.arange(input_side//stride),np.arange(input_side//stride),indexing='ij')
        grids.append(np.stack((xx,yy),axis=-1).reshape(-1,2));strides.append(np.full((len(grids[-1]),1),stride))
    grid=np.concatenate(grids);stride=np.concatenate(strides)
    if len(predictions)!=len(grid):raise ValueError('Unexpected YOLOX output shape')
    predictions[:,:2]=(predictions[:,:2]+grid)*stride
    predictions[:,2:4]=np.exp(np.clip(predictions[:,2:4],-20,20))*stride
    probabilities=predictions[:,4,None]*predictions[:,5:]
    classes=probabilities.argmax(axis=1);scores=probabilities.max(axis=1)
    mask=np.isin(classes,kinds)&(scores>=score_threshold)
    centers=predictions[mask,:2];sizes=predictions[mask,2:4];scores=scores[mask];classes=classes[mask]
    boxes=np.concatenate((centers-sizes/2,centers+sizes/2),axis=1)/scale
    boxes[:,[0,2]]=np.clip(boxes[:,[0,2]],0,w);boxes[:,[1,3]]=np.clip(boxes[:,[1,3]],0,h)
    result=[]
    for kind in kinds:
        idx=np.flatnonzero(classes==kind)
        for local in boxes_nms(boxes[idx],scores[idx]):
            i=idx[local];box=boxes[i]
            if min(box[2:]-box[:2])<min_side:continue
            result.append((box,float(scores[i]),{14:'bird',15:'cat',16:'dog'}[kind]))
    return result


def combine_pet_detections(primary,fallback):
    """Keep stronger-model boxes; retain baseline detections it missed."""
    combined=list(primary)
    for box,score,kind in fallback:
        duplicate=False
        for other,_,other_kind in combined:
            if kind!=other_kind:continue
            lo=np.maximum(box[:2],other[:2]);hi=np.minimum(box[2:],other[2:]);intersection=float(np.prod(np.maximum(0,hi-lo)))
            area=float(np.prod(box[2:]-box[:2]));other_area=float(np.prod(other[2:]-other[:2]))
            if intersection/max(area+other_area-intersection,1e-9)>.45 or intersection/max(min(area,other_area),1e-9)>.8:duplicate=True;break
        if not duplicate:combined.append((box,score,kind))
    return combined

def unit(vector):
    vector=np.asarray(vector,dtype=np.float32).ravel()
    if not np.all(np.isfinite(vector)):raise ValueError('Nonfinite embedding')
    return vector/max(float(np.linalg.norm(vector)),1e-12)

def pet_embedding(net,region):
    rgb=np.asarray(region.resize((224,224),Image.Resampling.BICUBIC),dtype=np.float32)/255
    rgb=(rgb-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
    tensor=np.ascontiguousarray(rgb.transpose(2,0,1)[None])
    return unit(net.run(None,{net.get_inputs()[0].name:tensor})[0])
