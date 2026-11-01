"""Build a self-contained local review page. No network dependencies or requests."""
import base64
import json
from pathlib import Path
from evaluate import dataset_id


def build_review(out,result):
    images={}
    for kind in ('samples','faces','pets'):
        for item in result[kind]:
            path=item.get('preview') or item['crop']
            if path not in images:images[path]='data:image/jpeg;base64,'+base64.b64encode((out/path).read_bytes()).decode('ascii')
    data=dict(result)
    data['images']=images
    data['dataset_id']=dataset_id(result)
    animal_path=out/'animal_detections.private.json'
    if animal_path.exists():
        animals=json.loads(animal_path.read_text(encoding='utf-8'))
        if animals['base_dataset_id']!=data['dataset_id']:raise ValueError('Animal dataset mismatch')
        data['pets']=result['pets']+animals['pets']
        for pet in animals['pets']:
            asset=pet['crop'];images[asset]='data:image/jpeg;base64,'+base64.b64encode((out/asset).read_bytes()).decode('ascii')
    confirmed_path=out/'confirmed_discoveries.private.json'
    confirmed_records=[]
    if confirmed_path.exists():
        confirmed=json.loads(confirmed_path.read_text(encoding='utf-8'))
        if confirmed['base_dataset_id']!=data['dataset_id']:raise ValueError('Confirmed membership dataset mismatch')
        confirmed_records=confirmed['records']
        data['confirmed_discoveries']=[{k:v for k,v in face.items() if k!='vector'} for face in confirmed_records]
        for face in confirmed_records:
            for field in ('crop','preview'):
                asset=face[field]
                if asset not in images:images[asset]='data:image/jpeg;base64,'+base64.b64encode((out/asset).read_bytes()).decode('ascii')
    seed_path=out/'labels.seed.private.json'
    if seed_path.exists():
        seed=json.loads(seed_path.read_text(encoding='utf-8-sig'))
        if seed.get('dataset_id')!=data['dataset_id']:raise ValueError('Seed labels belong to another dataset')
        data['initial_labels']=seed
    discovery_pointer=out/'person_discovery.private.json'
    if discovery_pointer.exists():
        pointer=json.loads(discovery_pointer.read_text(encoding='utf-8'))
        discovery_path=(out/pointer['path']).resolve()
        discovery=json.loads(discovery_path.read_text(encoding='utf-8'))
        if discovery['base_dataset_id']!=data['dataset_id']:raise ValueError('Discovery dataset mismatch')
        for candidate in discovery['candidates']:
            for field in ('crop','preview'):
                asset=discovery_path.parent/candidate[field]
                key='discovery:'+candidate['id']+':'+field
                images[key]='data:image/jpeg;base64,'+base64.b64encode(asset.read_bytes()).decode('ascii')
                candidate[field]=key
        data['person_discovery']=discovery
    # Embeddings remain local; only the pairwise scores needed by the reviewer
    # are embedded. No model download or inference takes place in the browser.
    vectors_path=out/'face_vectors.npy'
    if vectors_path.exists() and result['faces']:
        import numpy as np
        vectors=np.load(vectors_path)
        if len(vectors)!=len(result['faces']):raise ValueError('Face vector alignment mismatch')
        if confirmed_records:vectors=np.vstack([vectors,np.asarray([f['vector'] for f in confirmed_records],dtype=np.float32)])
        data['face_similarity']=np.round(np.clip(vectors@vectors.T,-1,1),4).tolist()
    animal_ids=out/'pet_vector_ids.json'
    if animal_ids.exists():
        import numpy as np
        ids=json.loads(animal_ids.read_text(encoding='utf-8'));matrices={}
        for name in ('small','base'):
            path=out/f'pet_{name}_vectors.npy'
            if path.exists():
                vectors=np.load(path)
                if len(vectors)!=len(ids):raise ValueError('Pet vector alignment mismatch')
                matrices[name]=np.round(np.clip(vectors@vectors.T,-1,1),4).tolist()
        data['animal_similarity']=dict(ids=ids,**matrices)
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    template=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; object-src 'none'; base-uri 'none'">
<title>人物与宠物 · 本机样本核对</title>
<style>
:root{--bg:#f7f5ef;--paper:#fffdf8;--ink:#222922;--sub:#687066;--accent:#ce4b2c;--line:#dddcd2}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,'Microsoft YaHei',sans-serif}header{padding:28px max(20px,calc((100vw - 1200px)/2));background:var(--paper);border-bottom:1px solid var(--line)}h1{font-size:28px;letter-spacing:-1px;margin:0 0 5px}h2{font-size:20px;margin:0}p{margin:7px 0;color:var(--sub)}main{max-width:1240px;padding:22px;margin:auto}button,select,input{font:inherit;border:1px solid var(--line);border-radius:8px;background:var(--paper);padding:9px 12px;color:var(--ink)}button{cursor:pointer;min-height:42px}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--accent)}.actions,.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.tabs button.on,.primary{background:var(--ink);color:white}.note{padding:14px 18px;border:1px solid #e4c79e;background:#fff6e6;border-radius:10px;margin-bottom:20px}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.stat,.group{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:18px}.stat b{display:block;font-size:30px;line-height:1.2}.stat span,small{color:var(--sub);font-size:12px}.faces{display:grid;grid-template-columns:repeat(auto-fill,minmax(125px,1fr));gap:10px;margin-top:12px}.item{min-width:0}.item img{width:100%;aspect-ratio:1;object-fit:contain;background:#eeeae0;display:block;border-radius:8px;cursor:zoom-in}.item button{padding:0;border:0;width:100%;display:block}.item select{font-size:12px;width:100%;padding:6px 2px;margin-top:5px}.group-hd{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.group-hd input{width:220px}.pairs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:12px}.pairs .item:first-child{border:2px solid var(--accent);padding:5px;border-radius:9px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}.grid img{width:100%;aspect-ratio:4/3;object-fit:contain;background:#eeeae0;border-radius:8px;cursor:zoom-in}.muted{color:var(--sub)}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:9px;text-align:left;border-bottom:1px solid var(--line)}dialog{border:0;border-radius:12px;max-width:96vw;max-height:95vh;background:var(--paper)}dialog::backdrop{background:#000b}.picture{display:inline-block;position:relative;line-height:0}.picture img{display:block;max-width:88vw;max-height:75vh;width:auto;height:auto}.picture svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.counts{font-size:12px;color:var(--sub);margin-top:9px}.saved{color:var(--accent);font-size:12px}.empty{padding:50px 15px;text-align:center}.label{display:inline-block;background:#eeece3;padding:3px 7px;border-radius:5px;font-size:11px;margin:3px 0}.error{color:#a23522}@media(max-width:600px){.pairs{grid-template-columns:repeat(2,minmax(0,1fr))}.faces{grid-template-columns:repeat(3,minmax(0,1fr))}main{padding:14px}header{padding:20px 16px}.group{padding:12px}.group-hd input{width:160px}.stats{grid-template-columns:repeat(2,1fr)}}
</style>
<header><h1>人物与宠物 · 样本核对</h1><p>独立本机原型。这里的分组和匹配均为候选，需要你确认。</p>
<div class="actions"><button id="export" class="primary">导出核对结果</button><button id="import">导入核对结果</button><input type="file" accept="application/json,.json" id="file" hidden><span class="saved" id="saveState"></span></div>
<nav class="tabs" aria-label="核对内容"><button data-tab="summary" class="on">概况</button><button data-tab="faces">人物候选</button><button data-tab="animals">猫狗检测</button><button data-tab="pairs">宠物匹配</button><button data-tab="samples">抽样照片</button></nav></header>
<main><div class="note">没有上传接口，也不会联网分析。自动分组尚未证明正确；相似度分数不是准确率。首次核对建议先看猫狗检测是否框对，再看是否为同一只宠物。</div><div id="content"></div></main>
<dialog id="viewer"><button id="close">关闭</button><div id="photo"></div><p id="photoInfo"></p></dialog>
<script id="dataset" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('dataset').textContent),$=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const byId=new Map([...D.faces,...D.pets].map(x=>[x.id,x])),samples=new Map(D.samples.map(x=>[x.id,x]));
const store='seeklive-vision-review-'+D.dataset_id;let labels={dataset_id:D.dataset_id,face_checks:{},group_names:{},animal_checks:{},pet_pairs:{},notes:''};
try{const old=JSON.parse(localStorage.getItem(store));if(old?.dataset_id===D.dataset_id)labels=old}catch{}
let current='summary',model='base';
function save(){try{localStorage.setItem(store,JSON.stringify(labels));$('#saveState').textContent='已保存在此浏览器；建议同时导出'}catch{$('#saveState').textContent='浏览器未保存，请导出结果'}}
function pairKey(a,b){return [a,b].sort().join('|')}
function choices(values,value){return values.map(([v,t])=>`<option value="${v}" ${v===value?'selected':''}>${t}</option>`).join('')}
function imageCard(id,select=''){const r=byId.get(id);if(!r)return '';return `<div class="item"><button data-open="${id}" aria-label="查看 ${id} 原照片"><img loading="lazy" src="${D.images[r.crop]}" alt="${id}"></button><small>${id} · ${esc(r.date||'拍摄日期未知')}</small>${select}</div>`}
function openPhoto(id){const r=byId.get(id),s=r?samples.get(r.sample):samples.get(id);if(!s)return;const box=r?.box;$('#photo').innerHTML=`<div class="picture"><img src="${D.images[s.preview]}" alt="抽样照片">${box?`<svg viewBox="0 0 1 1" preserveAspectRatio="none"><rect x="${box[0]}" y="${box[1]}" width="${box[2]-box[0]}" height="${box[3]-box[1]}" fill="none" stroke="#ff5133" stroke-width="0.006"/></svg>`:''}</div>`;$('#photoInfo').textContent=`样本 ${s.id} · ${s.captured||'EXIF 拍摄时间未知'} · 框仅用于核对检测位置`;$('#viewer').showModal()}
function render(){const root=$('#content');document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('on',b.dataset.tab===current));
 if(current==='summary'){const s=D.summary;root.innerHTML=`<div class="stats">${[['成功读取',s.decoded],['做人脸分析的照片',s.face_images],['人脸候选',s.faces],['含多张脸的候选组',s.multi_face_groups],['猫实例',s.cats],['狗实例',s.dogs],['宠物对比样本',s.pet_comparison_count],['读取失败',s.failures]].map(([n,v])=>`<div class="stat"><b>${v}</b><span>${n}</span></div>`).join('')}</div><div class="group"><h2>当前能说明什么</h2><p>已运行本地检测、特征提取和候选分组。尚未提供人工真值，因此没有“识别准确率”结论。</p><p>样本包含目录分层抽样和指定月份补充，数量不代表整个图库的人物或宠物分布。全部原图以只读方式处理。</p><p>人脸候选组 ${s.face_groups} 个，其中至少两张脸的组 ${s.multi_face_groups} 个。过严阈值会拆分同一人，过松阈值会合错人。</p><p>猫狗识别判断的是类别；宠物匹配另用专门模型。当前宠物身份聚类阈值只是实验参数。</p></div><div class="group"><h2>运行记录</h2><p>CPU 推理，${s.source_runs?'两批原始运行合计':'本批耗时'} ${s.seconds} 秒；被检查的源文件中 ${s.unchanged_sources} 个大小和修改时间保持一致。</p><table><tr><th>步骤</th><th>次数</th><th>中位耗时</th><th>P95</th></tr>${Object.entries(s.timings).map(([k,v])=>`<tr><td>${esc(({decode:'读取照片',face:'人脸分析',animal_detection:'猫狗检测',image_total:'单张处理',pet_small:'宠物轻量模型',pet_base:'宠物标准模型'})[k]||k)}</td><td>${v.n}</td><td>${((v.p50||0)*1000).toFixed(0)} ms</td><td>${((v.p95||0)*1000).toFixed(0)} ms</td></tr>`).join('')}</table></div>`}
 if(current==='faces'){root.innerHTML=`<p>先为候选组写一个本机别名，再逐张标记。相同的人可能被拆成多个组，组内也可能混入其他人；名字仅作为你的核对记录。</p>`+D.face_groups.map(g=>`<section class="group"><div class="group-hd"><h2>${g.id} <small>${g.members.length} 张脸</small></h2><input aria-label="${g.id} 本机别名" data-group="${g.id}" placeholder="可选：填写人物别名" value="${esc(labels.group_names[g.id]||'')}"></div><div class="faces">${g.members.map(id=>imageCard(id,`<select aria-label="核对 ${id}" data-face="${id}">${choices([['','待核对'],['keep','属于这个人'],['reject','不是同一个人'],['ignore','非真实人脸／忽略'],['unsure','不确定']],labels.face_checks[id]||'')}</select>`)).join('')}</div></section>`).join('')}
 if(current==='animals'){root.innerHTML=`<p>先核对动物类别和框的位置。点图片可以查看完整照片与检测框。</p><div class="faces">${D.pets.map(a=>imageCard(a.id,`<span class="label">预测：${a.kind==='cat'?'猫':'狗'} · 检测分数 ${a.score}</span><select aria-label="核对 ${a.id}" data-animal="${a.id}">${choices([['','待核对'],['cat','确实是猫'],['dog','确实是狗'],['not_animal','不是猫狗／框错'],['unsure','不确定']],labels.animal_checks[a.id]||'')}</select>`)).join('')}</div>${!D.pets.length?'<div class="empty">这批抽样没有检测到猫狗，尚不能评估宠物个体识别。</div>':''}`}
 if(current==='pairs'){const rows=D.comparisons.filter(p=>p.model===model);root.innerHTML=`<div class="actions"><button data-model="small">轻量模型 small</button><button data-model="base">标准模型 base</button></div><p>当前：${model}。左侧为参照，右侧为模型推荐；请判断“同一只”，不是同品种或相似毛色。两套模型共用同一份人工核对记录。</p>`+rows.map(row=>`<section class="group"><h2>${row.anchor} <small>${byId.get(row.anchor).kind==='cat'?'猫':'狗'}候选匹配</small></h2><div class="pairs">${imageCard(row.anchor)}${row.matches.map(m=>imageCard(m.id,`<small>相似度 ${m.cosine}${m.near_duplicate?' · 近重复候选':''}${m.same_date?' · 同一天':''}</small><select aria-label="核对 ${row.anchor} 与 ${m.id}" data-pair="${pairKey(row.anchor,m.id)}">${choices([['','待核对'],['same','同一只宠物'],['different','不同宠物'],['unsure','不确定']],labels.pet_pairs[pairKey(row.anchor,m.id)]||'')}</select>`)).join('')}</div></section>`).join('')+(!rows.length?'<div class="empty">宠物样本不足，暂时没有可核对的匹配。</div>':'')}
 if(current==='samples')root.innerHTML=`<p>点击查看预览。人脸仅在分层选出的子集运行；未做人脸分析的照片不能据此判定为没有人物。</p><div class="grid">${D.samples.map(s=>`<div><img loading="lazy" data-open="${s.id}" src="${D.images[s.preview]}" alt="样本 ${s.id}"><div class="counts">${s.id}<br>${esc(s.captured||'拍摄时间未知')}<br>${s.face_scanned?'脸候选 '+s.faces.length:'未做人脸分析'} · 动物候选 ${s.pets.length}</div></div>`).join('')}</div>`;
}
document.addEventListener('click',e=>{const open=e.target.closest('[data-open]');if(open)openPhoto(open.dataset.open);const tab=e.target.closest('[data-tab]');if(tab){current=tab.dataset.tab;render()}const button=e.target.closest('[data-model]');if(button){model=button.dataset.model;render()}});
document.addEventListener('change',e=>{const el=e.target;if(el.dataset.face)labels.face_checks[el.dataset.face]=el.value;if(el.dataset.group)labels.group_names[el.dataset.group]=el.value;if(el.dataset.animal)labels.animal_checks[el.dataset.animal]=el.value;if(el.dataset.pair)labels.pet_pairs[el.dataset.pair]=el.value;save()});
$('#export').onclick=()=>{labels.exported_at=new Date().toISOString();const url=URL.createObjectURL(new Blob([JSON.stringify(labels,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='vision-review-'+D.dataset_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
$('#import').onclick=()=>$('#file').click();$('#file').onchange=async e=>{try{const v=JSON.parse(await e.target.files[0].text());if(v.dataset_id!==D.dataset_id)throw Error('这不是当前样本的核对文件');labels={...labels,...v};save();render()}catch(err){alert(err.message)}};
$('#close').onclick=()=>$('#viewer').close();render();
</script></html>'''
    face_script='\n'.join(Path(__file__).with_name(name).read_text(encoding='utf-8') for name in ('face_review.js','discovery_review.js','animal_review.js','database_review.js','formal_review.js'))
    start=template.index(" if(current==='faces')")
    end=template.index(" if(current==='animals')",start)
    template=template[:start]+" if(current==='faces')renderFaces(root);\n"+template[end:]
    start=template.index(" if(current==='animals')");end=template.index(" if(current==='pairs')",start)
    template=template[:start]+" if(current==='animals')renderAnimals(root);\n"+template[end:]
    start=template.index(" if(current==='pairs')");end=template.index(" if(current==='samples')",start)
    template=template[:start]+template[end:]
    template=template.replace('<button data-tab="pairs">宠物匹配</button>','')
    template=template.replace(" if(current==='animals')", " if(current==='discoveries')renderDiscoveries(root);\n if(current==='animals')")
    template=template.replace('<button data-tab="faces">人物候选</button>', '<button data-tab="faces">人物候选</button><button data-tab="discoveries">关注人物的新照片</button>')
    template=template.replace("$('#close').onclick=()=>$('#viewer').close();render();",face_script+"\n$('#close').onclick=()=>$('#viewer').close();render();")
    template=template.replace("labels=old}catch{}","labels={...labels,...old}}catch{}")
    template=template.replace("let current='summary',model='base';", "const dbBoot=document.getElementById('database-bootstrap');const liveDatabase=dbBoot?JSON.parse(dbBoot.textContent):null;if(liveDatabase)labels=liveDatabase.state;\nlet current='summary',model='base';")
    template=template.replace("try{const old=JSON.parse(localStorage.getItem(store));", "if(D.initial_labels?.dataset_id===D.dataset_id)labels={...labels,...D.initial_labels};\ntry{const old=JSON.parse(localStorage.getItem(store));")
    template=template.replace("if(el.dataset.face)labels.face_checks[el.dataset.face]=el.value;", "if(el.dataset.face){personCheckpoint();labels.face_checks[el.dataset.face]=el.value;}")
    template=template.replace("if(el.dataset.group)labels.group_names[el.dataset.group]=el.value;", "if(el.dataset.group){personCheckpoint();labels.group_names[el.dataset.group]=el.value;}")
    template=template.replace("if(el.dataset.pair)labels.pet_pairs[el.dataset.pair]=el.value;save()", "if(el.dataset.pair)labels.pet_pairs[el.dataset.pair]=el.value;if(el.dataset.face||el.dataset.group||el.dataset.animal||el.dataset.pair)save()")
    template=template.replace("labels={...labels,...v};save();render()", "const previous=labels;labels={...labels,person_interest:{},...v};try{personNormalize();animalNormalize()}catch(err){labels=previous;throw err}selectedPeople.clear();personView='followed';personSearch='';save();render()")
    template=template.replace(".error{color:",".person-layout{display:grid;grid-template-columns:minmax(210px,1fr) minmax(0,3fr);gap:18px}.person-list{max-height:75vh;overflow:auto}.person-list button{display:block;width:100%;text-align:left;margin-bottom:7px}.person-list .active{border:2px solid var(--accent)}.person-list img{width:44px;height:44px;object-fit:cover;vertical-align:middle;margin-right:8px}.person-candidates{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.person-candidates .faces{grid-template-columns:repeat(3,minmax(0,1fr))}.merge-bar{position:sticky;bottom:0;padding:12px;background:var(--paper);border:1px solid var(--line);border-radius:10px;z-index:2}.error{color:")
    template=template.replace('尚未提供人工真值，因此没有“识别准确率”结论。','当前已保留人工确认记录；尚未开展完整准确率与漏检评估。')
    template=template.replace('>猫狗检测</button>','>猫狗鸟档案</button>')
    template=template.replace('D.comparisons.filter(p=>p.model===model)', "D.comparisons.filter(p=>p.model===model&&['cat','dog'].includes(animalKind(p.anchor))).map(p=>({...p,matches:p.matches.filter(m=>animalKind(m.id)===animalKind(p.anchor))})).filter(p=>p.matches.length)")
    template=template.replace("byId.get(row.anchor).kind==='cat'", "animalKind(row.anchor)==='cat'")
    template=template.replace('@media(max-width:600px){','@media(max-width:600px){.person-layout{grid-template-columns:1fr}.person-list{max-height:180px}')
    template=template.replace('</style>', '.animal-group-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,310px),1fr));gap:14px}.animal-group-grid .group{margin-bottom:0}.animal-group-grid .faces{grid-template-columns:repeat(3,minmax(0,1fr))}.animal-candidate input[type="checkbox"]{width:18px;height:18px;vertical-align:middle}.animal-candidate .actions input{min-width:0;width:100%}.animal-group-grid+.merge-bar{margin-top:16px}</style>')
    (out/'review.html').write_text(template.replace('__DATA__',payload),encoding='utf-8')
