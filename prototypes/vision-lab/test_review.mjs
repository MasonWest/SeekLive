// Exercise the actual offline page script using synthetic records only.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';

const sample={id:'sample',preview:'preview',captured:'2025:02:01',faces:['F1'],pets:['A1'],face_scanned:true};
const data={dataset_id:'synthetic',summary:{decoded:2,face_images:2,faces:1,multi_face_groups:0,face_groups:1,cats:2,dogs:0,pet_comparison_count:2,failures:0,seconds:1,unchanged_sources:2,timings:{}},samples:[sample,{...sample,id:'other'}],faces:[{id:'F1',sample:'sample',crop:'face',box:[.1,.1,.3,.4]}],pets:[{id:'A1',sample:'sample',kind:'cat',crop:'cat1',box:[.1,.1,.7,.8]},{id:'A2',sample:'other',kind:'cat',crop:'cat2',box:[.1,.1,.7,.8]}],face_groups:[{id:'P001',members:['F1']}],comparisons:['small','base'].map(model=>({model,anchor:'A1',matches:[{id:'A2',cosine:.85}]})),images:{preview:'data:image/jpeg;base64,',face:'data:image/jpeg;base64,',cat1:'data:image/jpeg;base64,',cat2:'data:image/jpeg;base64,'}};
data.pets.push({id:'A3',sample:'cat3',kind:'cat',crop:'cat1'},{id:'A4',sample:'cat4',kind:'cat',crop:'cat2'},{id:'B1',sample:'bird1',kind:'bird',crop:'cat1'},{id:'D1',sample:'dog1',kind:'dog',crop:'cat2'});
data.pet_groups={base:[{id:'T1',kind:'cat',members:['A1','A2']},{id:'T2',kind:'cat',members:['A3']},{id:'T3',kind:'cat',members:['A4']}]};
data.animal_similarity={ids:['A1','A2','A3','A4'],small:[[1,.9,.85,.8],[.9,1,.8,.75],[.85,.8,1,.7],[.8,.75,.7,1]]};
// Build the real HTML with synthetic assets, including the injected person UI.
data.faces.push({id:'F2',sample:'other',crop:'face',face_pixels:80},{id:'F3',sample:'third',crop:'face',face_pixels:70});
data.face_groups.push({id:'P002',members:['F2']},{id:'P003',members:['F3']});
const built=spawnSync('tmp/vision-lab/venv/Scripts/python.exe',['-c',`
import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,'prototypes/vision-lab')
from review import build_review
from evaluate import dataset_id
from PIL import Image
import numpy as np
result=json.load(sys.stdin)
with tempfile.TemporaryDirectory(dir='tmp/vision-lab') as folder:
 out=Path(folder)
 for name in ['preview','face','cat1','cat2']:Image.new('RGB',(24,24),(50,100,150)).save(out/name,format='JPEG')
 np.save(out/'face_vectors.npy',np.array([[1,0],[.8,.6],[0,1]],np.float32))
 discovery=dict(base_dataset_id=dataset_id(result),reference_signature='test-refs',people=[dict(id='P001')],summary=dict(decoded=8,references=1,batch_count=2,new_batch_images=4),candidates=[dict(id='N'+str(i),person='P001',crop='face',preview='preview',captured='2025:01:01',score=.8,source_version=[100+i,200],box=[.1,.1,.4,.4]) for i in range(4)])
 (out/'discovery.private.json').write_text(json.dumps(discovery),encoding='utf-8')
 (out/'person_discovery.private.json').write_text(json.dumps(dict(path='discovery.private.json')),encoding='utf-8')
 build_review(out,result)
 sys.stdout.buffer.write((out/'review.html').read_bytes())
`],{input:JSON.stringify(data),encoding:'utf8',maxBuffer:2e6});
assert.equal(built.status,0,built.stderr);
const html=built.stdout,script=html.split('<script>')[1].split('</script>')[0];
const payload=html.split('<script id="dataset" type="application/json">')[1].split('</script>')[0];
const builtData=JSON.parse(payload),store='seeklive-vision-review-'+builtData.dataset_id;
const elements=new Map(),listeners={},saved=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',dataset:{},classList:{toggle(){}},click(){},focus(){},setSelectionRange(){},showModal(){this.open=true},close(){this.open=false}});return elements.get(id)}
element('dataset').textContent=payload;
const ctx=vm.createContext({document:{querySelector:s=>element(s.slice(1)),getElementById:id=>id==='database-bootstrap'?null:element(id),querySelectorAll:()=>[],addEventListener:(name,fn)=>(listeners[name]??=[]).push(fn),createElement:()=>({click(){}})},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},Blob,URL,setTimeout,alert:msg=>{ctx.lastAlert=msg}});
vm.runInContext(script,ctx);
const dispatch=(name,event)=>{for(const fn of listeners[name]||[])fn(event)};
const mapping={'data-animal-expand':'animalExpand','data-animal-split':'animalSplit','data-animal-action':'animalAction','data-animal-view':'animalView','data-animal-focus':'animalFocus','data-animal-remove':'animalRemove','data-discovery-open':'discoveryOpen','data-discovery-expand':'discoveryExpand','data-person-view':'personView','data-person-pending':'personPending','data-tab':'tab','data-model':'model','data-open':'open','data-person-focus':'personFocus','data-person-hide':'personHide','data-person-action':'personAction'};
const click=dataset=>dispatch('click',{target:{closest:selector=>{const key=mapping[selector.slice(1,-1)];return key&&dataset[key]?{dataset}:null}}});
const change=(dataset,value,checked)=>dispatch('change',{target:{dataset,value,checked}});
const run=code=>vm.runInContext(code,ctx);
assert.match(element('content').innerHTML,/成功读取/);
assert.ok(!html.includes('data-tab="pairs"'));
for(const tab of ['faces','animals','samples']){click({tab});assert.ok(element('content').innerHTML.length>50)}
// Existing pair labels remain compatible, but there is no pair-by-pair page.
run("labels.pet_pairs['A1|A2']='same';save()");
click({open:'A1'});assert.equal(element('viewer').open,true);assert.match(element('photo').innerHTML,/<rect/);
element('close').onclick();assert.equal(element('viewer').open,false);
click({tab:'faces'});
assert.match(element('content').innerHTML,/还没有关注的人/);
assert.equal(run('personRecommendations(people()[0],people()).length'),0);
click({personView:'other'});assert.match(element('content').innerHTML,/无需逐一命名/);
change({group:'P001'},'Alice');
click({personView:'followed'});assert.match(element('content').innerHTML,/尚未确认参考脸/);
assert.equal(run('personRecommendations(people()[0],people()).length'),0);
element('personName').value='Alice';click({personAction:'confirm'});
assert.match(element('content').innerHTML,/可能属于这个人的遗漏照片/);
assert.equal(run("personRecommendations(people()[0],people())[0].person.id"),'P002');
// Discovery is optional, capped initially, persisted separately from confirmed faces.
click({tab:'discoveries'});assert.match(element('content').innerHTML,/不要求你逐一核对/);assert.match(element('content').innerHTML,/已汇总 2 批/);
assert.equal((element('content').innerHTML.match(/data-discovery-open=/g)||[]).length,3);
click({discoveryExpand:'P001'});assert.equal((element('content').innerHTML.match(/data-discovery-open=/g)||[]).length,4);
change({discoveryCheck:'test-refs|N0|P001|100:200'},'same');
assert.equal(JSON.parse(saved.get(store)).discovery_checks['test-refs|N0|P001|100:200'],'same');
assert.equal(run('Object.keys(labels.face_checks).length'),1);
click({discoveryOpen:'N0'});assert.equal(element('viewer').open,true);element('close').onclick();
click({tab:'faces'});
// Multi-group manual confirmation, pair labels untouched, reversible identity links.
change({personSelect:'P002'},'',true);click({personAction:'merge'});
assert.equal(run('people().length'),2);
assert.equal(run("personRoot('P002')"),'P001');
assert.equal(run("labels.face_checks.F2"),'keep');
assert.equal(run("personRecommendations(people()[0],people())[0].person.id"),'P003');
assert.equal(run("labels.pet_pairs['A1|A2']"),'same');
click({personAction:'undo'});assert.equal(run('people().length'),3);assert.equal(run('labels.face_checks.F2'),undefined);
// Rejected/uncertain/ignored observations never become references during merge.
change({face:'F2'},'reject');change({personSelect:'P002'},'',true);click({personAction:'merge'});
assert.equal(run('labels.face_checks.F2'),'reject');assert.equal(run('representatives(people()[0]).length'),1);
click({personAction:'undo'});click({personAction:'undo'});
change({face:'F2'},'unsure');change({personSelect:'P002'},'',true);click({personAction:'merge'});
assert.equal(run('labels.face_checks.F2'),'unsure');assert.equal(run("representatives(people()[0]).includes('F2')"),false);
click({personAction:'undo'});click({personAction:'undo'});
click({personHide:'P002'});assert.equal(run('personRecommendations(people()[0],people()).length'),0);
click({personAction:'undo'});assert.equal(run('personRecommendations(people()[0],people()).length'),1);
// Unfollowing preserves names/references, removes recommendations and is reversible.
const priorChecks=run('JSON.stringify(labels.face_checks)');
click({personView:'other'});click({personFocus:'P002'});click({personAction:'unfollow'});
assert.equal(run("personStatus(people().find(p=>p.id==='P002'))"),'unfollowed');
assert.equal(run('JSON.stringify(labels.face_checks)'),priorChecks);
click({personView:'followed'});assert.equal(run('personRecommendations(people()[0],people()).length'),0);
click({personView:'unfollowed'});assert.match(element('content').innerHTML,/恢复关注/);
click({personAction:'undo'});click({personView:'followed'});
assert.equal(run('personRecommendations(people()[0],people()).length'),1);
// Confirm a saved name without silently promoting uncertain faces.
click({personView:'other'});click({personFocus:'P003'});element('personName').value='Bob';
change({face:'F3'},'unsure');click({personAction:'confirm'});
assert.equal(run('labels.face_checks.F3'),'unsure');
assert.equal(run('labels.group_names.P003'),'Bob');
assert.equal(run("personStatus(people().find(p=>p.id==='P003'))"),'followed');
click({personAction:'undo'});click({personAction:'undo'});
click({personView:'followed'});click({personFocus:'P001'});
// Old labels without new fields can be restored, and aliases are escaped.
const malicious='<img src=x onerror=bad()>';
const labels=JSON.parse(saved.get(store));labels.group_names.P001=malicious;delete labels.person_links;delete labels.person_history;
saved.set(store,JSON.stringify(labels));
const restored=vm.createContext({...ctx});listeners.click=[];listeners.change=[];listeners.input=[];
vm.runInContext(script,restored);click({tab:'faces'});
assert.ok(!element('content').innerHTML.includes(malicious));assert.match(element('content').innerHTML,/&lt;img/);
// Import invalid cycles must roll back rather than corrupting existing state.
const bad={dataset_id:builtData.dataset_id,person_links:{P001:'P002',P002:'P001'}};
await element('file').onchange({target:{files:[{text:async()=>JSON.stringify(bad)}]}});
assert.equal(vm.runInContext("personRoot('P001')",restored),'P001');
assert.ok(ctx.lastAlert);
const validImport={dataset_id:builtData.dataset_id,person_links:{P002:'P001'},face_checks:{F1:'keep',F2:'keep'},group_names:{P001:'Alice'},person_history:[]};
await element('file').onchange({target:{files:[{text:async()=>JSON.stringify(validImport)}]}});
assert.equal(vm.runInContext('people().length',restored),2);
assert.equal(vm.runInContext('representatives(people()[0]).length',restored),2);
assert.equal(JSON.parse(saved.get(store)).group_names.P001,'Alice');
assert.equal(vm.runInContext("personStatus(people()[0])",restored),'followed');
click({personAction:'unfollow'});
const unfollowed=JSON.parse(saved.get(store));
assert.equal(unfollowed.person_interest.P001,'unfollowed');
assert.equal(unfollowed.face_checks.F1,'keep');
click({personView:'unfollowed'});click({personAction:'follow'});
assert.equal(vm.runInContext('personView',restored),'followed');
assert.equal(vm.runInContext('personStatus(people()[0])',restored),'followed');
await element('file').onchange({target:{files:[{text:async()=>JSON.stringify(unfollowed)}]}});
assert.equal(vm.runInContext('personStatus(people()[0])',restored),'unfollowed');
assert.equal(vm.runInContext('labels.group_names.P001',restored),'Alice');
// Historical discovery identities follow subsequent manual merges and renamed roots.
vm.runInContext("labels.person_links={P001:'P002'};labels.group_names={P002:'Renamed'};labels.person_interest={P002:'followed'};current='discoveries';render()",restored);
assert.match(element('content').innerHTML,/Renamed/);
assert.ok((element('content').innerHTML.match(/data-discovery-open=/g)||[]).length>=3);
// Initial machine groups render as independent cards; birds and missing features are singletons.
click({tab:'animals'});click({animalView:'other'});
assert.equal(vm.runInContext('animalCandidateGroups().length',restored),5);
assert.equal((element('content').innerHTML.match(/data-animal-candidate=/g)||[]).length,5);
assert.equal(vm.runInContext("animalCandidateGroups().find(g=>g.id==='AG-T1').members.length",restored),2);
assert.equal(vm.runInContext("animalCandidateGroups().find(g=>g.kind==='bird').members.length",restored),1);
// Inspect and split a wrong item; undo restores the original candidate group.
click({animalSplit:'A2'});assert.equal(vm.runInContext('animalCandidateGroups().length',restored),6);
click({animalAction:'undo'});assert.equal(vm.runInContext('animalCandidateGroups().length',restored),5);
element('name-AG-T1').value='Milo';click({animalAction:'create-group',animalGroup:'AG-T1'});
const firstPet=vm.runInContext('animalActive',restored);
assert.equal(vm.runInContext('labels.animal_entities[animalActive].members.length',restored),2);
assert.equal(vm.runInContext('animalRecommendations(labels.animal_entities[animalActive]).length',restored),2);
// One operation merges multiple candidate blocks into the named animal.
change({animalGroupSelect:'AG-T2'},'',true);change({animalGroupSelect:'AG-T3'},'',true);
click({animalAction:'merge-selected'});assert.equal(vm.runInContext('labels.animal_entities[animalActive].members.length',restored),4);
click({animalAction:'undo'});assert.equal(vm.runInContext('labels.animal_entities[animalActive].members.length',restored),2);
// An already named archive can join the same batch selection.
click({animalView:'other'});element('name-AG-T2').value='Second';click({animalAction:'create-group',animalGroup:'AG-T2'});
const secondPet=vm.runInContext('animalActive',restored);
element('manualAnimalGroup').value=firstPet;click({animalAction:'add-group'});change({animalGroupSelect:'AG-T3'},'',true);
click({animalAction:'merge-selected'});assert.equal(vm.runInContext('Object.keys(labels.animal_entities).length',restored),1);
assert.equal(vm.runInContext('labels.animal_entities[animalActive].members.length',restored),4);
click({animalAction:'undo'});assert.equal(vm.runInContext('Object.keys(labels.animal_entities).length',restored),2);
// Cross-species bulk confirmation is rejected without changing assignments.
click({animalView:'other'});change({animalGroupSelect:'AG-T3'},'',true);change({animalGroupSelect:'AG-one-B1'},'',true);
const before=vm.runInContext('JSON.stringify(labels.animal_entities)',restored);click({animalAction:'create-selected'});
assert.equal(vm.runInContext('JSON.stringify(labels.animal_entities)',restored),before);assert.match(ctx.lastAlert,/不同物种/);
click({animalAction:'clear'});
// Bird groups support naming/manual joining, never cat/dog identity scoring.
element('name-AG-one-B1').value='Bird';click({animalAction:'create-group',animalGroup:'AG-one-B1'});
assert.equal(vm.runInContext('labels.animal_entities[animalActive].kind',restored),'bird');
assert.equal(vm.runInContext('animalRecommendations(labels.animal_entities[animalActive]).length',restored),0);
click({animalAction:'unfollow'});click({animalView:'unfollowed'});click({animalAction:'follow'});
assert.equal(vm.runInContext('labels.animal_entities[animalActive].status',restored),'followed');
// Ignoring a draft does not turn an unverified machine group into a confirmed individual.
click({animalView:'other'});click({animalAction:'ignore-group',animalGroup:'AG-T3'});
const ignored=vm.runInContext("Object.values(labels.animal_entities).find(e=>e.confirmed===false).id",restored);
click({animalView:'unfollowed'});click({animalFocus:ignored});click({animalAction:'follow'});
assert.equal(vm.runInContext('animalView',restored),'other');assert.equal(vm.runInContext('labels.animal_entities['+JSON.stringify(ignored)+']',restored),undefined);
// Class correction splits a record from the old archive and invalid items are recoverable.
change({animalClass:'A1'},'not_animal');click({animalView:'excluded'});assert.match(element('content').innerHTML,/A1/);
change({animalClass:'A1'},'bird');click({animalView:'other'});assert.equal(vm.runInContext("animalCandidateGroups().find(g=>g.members.includes('A1')).members.length",restored),1);
// Explicitly confirmed discovery records appear under the original person and
// become references without changing the original dataset namespace.
const enriched=structuredClone(builtData);
enriched.confirmed_discoveries=[{id:'CF1',person:'P001',sample:'new-sample',crop:'face',preview:'preview',captured:'2024:01:01',box:[.1,.1,.4,.4],face_pixels:90}];
enriched.initial_labels={dataset_id:builtData.dataset_id,group_names:{P001:'Owner'},face_checks:{F1:'keep'}};
enriched.face_similarity=[[1,.8,0,.9],[.8,1,.6,.85],[0,.6,1,.3],[.9,.85,.3,1]];
element('dataset').textContent=JSON.stringify(enriched);saved.clear();listeners.click=[];listeners.change=[];listeners.input=[];
const integrated=vm.createContext({...ctx});vm.runInContext(script,integrated);
assert.equal(vm.runInContext("people()[0].members.includes('CF1')",integrated),true);
assert.equal(vm.runInContext('confirmedFaces(people()[0]).length',integrated),2);
click({tab:'faces'});assert.match(element('content').innerHTML,/CF1/);
click({open:'CF1'});assert.equal(element('viewer').open,true);element('close').onclick();
change({face:'CF1'},'reject');assert.equal(vm.runInContext('confirmedFaces(people()[0]).length',integrated),1);
click({personAction:'undo'});assert.equal(vm.runInContext('confirmedFaces(people()[0]).length',integrated),2);
// SQLite bootstrap is authoritative; writes serialize with optimistic revisions.
listeners.click=[];listeners.change=[];listeners.input=[];element('dataset').textContent=payload;
const requests=[],resolvers=[];
const dbState={dataset_id:builtData.dataset_id,group_names:{P001:'From Database'},face_checks:{F1:'keep'},animal_entities:{}};
const bootstrap={state:dbState,revision:10,token:'test-token'};
const dbDocument={...ctx.document,getElementById:id=>id==='database-bootstrap'?{textContent:JSON.stringify(bootstrap)}:element(id)};
const live=vm.createContext({...ctx,document:dbDocument,fetch:(url,options)=>{requests.push({url,body:JSON.parse(options.body),headers:options.headers});return new Promise(resolve=>resolvers.push(resolve))}});
vm.runInContext(script,live);assert.equal(vm.runInContext('labels.group_names.P001',live),'From Database');
vm.runInContext("labels.group_names.P001='Edit 1';save();labels.group_names.P001='Edit 2';save()",live);
assert.equal(requests.length,1);assert.equal(requests[0].body.revision,10);
resolvers.shift()({ok:true,status:200,json:async()=>({revision:11})});await new Promise(setImmediate);
assert.equal(requests.length,2);assert.equal(requests[1].body.revision,11);assert.equal(requests[1].body.state.group_names.P001,'Edit 2');
resolvers.shift()({ok:true,status:200,json:async()=>({revision:12})});await new Promise(setImmediate);
assert.equal(vm.runInContext('databaseRevision',live),12);
vm.runInContext("labels.group_names.P001='Unsaved conflict';save()",live);
resolvers.shift()({ok:false,status:409});await new Promise(setImmediate);
assert.equal(vm.runInContext('databaseConflict',live),true);assert.match(element('saveState').textContent,/导出/);
assert.equal(JSON.parse(saved.get(store)).group_names.P001,'Unsaved conflict');
// Formal archives can contain observations outside the original 521-photo snapshot.
vm.runInContext("liveDatabase.formal=true;labels.animal_entities={PET:{id:'PET',kind:'cat',name:'Cat',status:'followed',members:['FORMAL-NEW']}};animalView='followed';animalNormalize();renderAnimals($('#content'))",live);
assert.match(element('content').innerHTML,/api\/vision\/image\?id=FORMAL-NEW/);
console.log('PASS: generated offline HTML, follow/other/unfollow views, pending references, named confirmation, relevance filtering, follow restore/import, merge/undo, multi-reference recommendations and existing review flows (synthetic only)');
