const animalWords={cat:'猫',dog:'狗',bird:'鸟'};
let animalView='auto',animalSpecies='all',animalActive='',animalSelected=new Set(),animalExpanded=new Set(),animalDraftNames=new Map(),animalLimit=24;
function animalNormalize(){
 labels.animal_entities??={};labels.animal_splits=Array.isArray(labels.animal_splits)?labels.animal_splits:[];
 labels.animal_history=Array.isArray(labels.animal_history)?labels.animal_history.slice(-30):[];
 const assigned=new Set();
 for(const [id,e] of Object.entries(labels.animal_entities)){
  if(!/^[A-Za-z0-9_-]{1,100}$/.test(id)||e.id!==id||!animalWords[e.kind]||!['followed','unfollowed','other'].includes(e.status)||!Array.isArray(e.members))throw Error('动物档案格式无效');
  for(const oid of e.members){if((!D.pets.some(p=>p.id===oid)&&!liveDatabase?.formal)||assigned.has(oid))throw Error('动物归属记录无效或重复');assigned.add(oid)}
 }
}
animalNormalize();
function animalKind(id){return labels.animal_checks[id]||byId.get(id)?.kind}
function animalOwner(id){return Object.values(labels.animal_entities).find(e=>e.members.includes(id))}
function animalCheckpoint(){labels.animal_history.push(JSON.parse(JSON.stringify({animal_entities:labels.animal_entities,animal_checks:labels.animal_checks,animal_splits:labels.animal_splits})));if(labels.animal_history.length>30)labels.animal_history.shift()}
function animalUndo(){if(liveDatabase?.formal){alert('已归档照片请在已归档列表中勾选移除，不需要撤销整份档案。');return}const prev=labels.animal_history.pop();if(prev){Object.assign(labels,prev);labels.animal_splits=prev.animal_splits||[]}animalSelected.clear();save();render()}
function animalScore(entity,id){
 if(entity?.kind==='bird'||!D.animal_similarity?.small)return null;
 const ids=D.animal_similarity.ids,j=ids.indexOf(id);if(j<0)return null;
 const scores=entity.members.map(oid=>ids.indexOf(oid)).filter(i=>i>=0).map(i=>D.animal_similarity.small[i][j]);
 return scores.length?Math.max(...scores):null;
}
function animalCandidateGroups(){
 const available=D.pets.filter(p=>!animalOwner(p.id)&&animalWords[animalKind(p.id)]),eligible=new Set(available.map(p=>p.id)),used=new Set(),groups=[];
 const split=new Set(labels.animal_splits);
 for(const raw of D.pet_groups?.base||D.pet_groups?.small||[]){
  const members=[],samplesSeen=new Set();
  for(const id of raw.members){const p=byId.get(id);if(!eligible.has(id)||used.has(id)||split.has(id)||animalKind(id)==='bird'||animalKind(id)!==p.kind||animalKind(id)!==raw.kind||samplesSeen.has(p.sample))continue;members.push(id);used.add(id);samplesSeen.add(p.sample)}
  if(members.length)groups.push({id:'AG-'+raw.id,kind:raw.kind,members,draft:true});
 }
 // Birds, corrected classes, split photos and detections without features stay independent.
 for(const p of available)if(!used.has(p.id))groups.push({id:'AG-one-'+p.id,kind:animalKind(p.id),members:[p.id],draft:true});
 return groups.sort((a,b)=>({cat:0,dog:1,bird:2}[a.kind]-{cat:0,dog:1,bird:2}[b.kind])||b.members.length-a.members.length||a.id.localeCompare(b.id)).map((g,i)=>({...g,title:'候选组 '+String(i+1).padStart(3,'0')}));
}
function animalGroupScore(entity,group){const scores=group.members.map(id=>animalScore(entity,id)).filter(s=>s!==null).sort((a,b)=>b-a);return scores.length?scores[0]*.8+(scores[1]??scores[0])*.2:null}
function animalTargets(active){return [...animalCandidateGroups().filter(g=>g.kind===active.kind),...Object.values(labels.animal_entities).filter(e=>e.id!==active.id&&e.kind===active.kind&&e.status==='followed').map(e=>({...e,draft:false}))]}
function animalRecommendations(active){if(active.kind==='bird')return [];return animalCandidateGroups().filter(g=>g.kind===active.kind).map(g=>({...g,score:animalGroupScore(active,g)})).filter(g=>g.score!==null&&g.score>=.55).sort((a,b)=>b.score-a.score).slice(0,8)}
function animalObservation(id,draft=false){const p=byId.get(id);if(!p)return '';return imageCard(id,`<span class="label">检测：${esc(animalWords[p.kind]||p.kind)}</span><select data-animal-class="${id}" aria-label="纠正动物类别">${choices([['','沿用检测类别'],['cat','猫'],['dog','狗'],['bird','鸟'],['not_animal','不是动物／框错'],['unsure','不确定']],labels.animal_checks[id]||'')}</select>${draft?`<button data-animal-split="${id}">拆为独立候选</button>`:animalOwner(id)?`<button data-animal-remove="${id}">从档案移出</button>`:''}`)}
function animalGroupCard(group,mode){
 const expanded=animalExpanded.has(group.id),shown=expanded?group.members:group.members.slice(0,3);
 return `<section class="group animal-candidate" data-animal-candidate="${group.id}"><div class="group-hd"><label><input type="checkbox" data-animal-group-select="${group.id}" ${animalSelected.has(group.id)?'checked':''}> ${esc(group.name||group.title||group.id)} · ${animalWords[group.kind]} · ${group.members.length} 张</label></div><small>${group.draft?'候选分组，尚未确认同一只':'已命名档案'}${group.score===null||group.score===undefined?'':' · 推荐分数 '+group.score.toFixed(3)}</small><div class="faces">${shown.map(id=>animalObservation(id,group.draft)).join('')}</div>${group.members.length>3?`<button data-animal-expand="${group.id}">${expanded?'收起':'展开本组全部 '+group.members.length+' 张'}</button>`:''}${mode==='other'?`<div class="actions"><input id="name-${group.id}" data-animal-draft-name="${group.id}" value="${esc(animalDraftNames.get(group.id)||'')}" aria-label="${group.id} 命名" placeholder="给这一组起个名字"><button data-animal-action="create-group" data-animal-group="${group.id}">命名并确认本组</button><button data-animal-action="ignore-group" data-animal-group="${group.id}">不关注本组</button></div>`:''}</section>`;
}
function animalToolbar(groups){
 const entities=Object.values(labels.animal_entities),invalid=D.pets.filter(p=>!animalOwner(p.id)&&!animalWords[animalKind(p.id)]);
 return `<p>先看候选小组，给一组命名，再勾选其他组批量合并。猫狗按已有特征预分组，鸟和无特征的照片先独立成组。候选分组不代表已确认同一只。</p><div class="actions">${[['followed','关注的动物'],['other','候选分组'],['unfollowed','不关注'],['excluded','待定／框错']].map(([v,t])=>`<button data-animal-view="${v}" class="${animalView===v?'primary':''}">${t} · ${v==='other'?groups.length:v==='excluded'?invalid.length:entities.filter(e=>e.status===v).length}</button>`).join('')}<select id="animalSpecies" aria-label="动物类别">${choices([['all','全部物种'],['cat','猫'],['dog','狗'],['bird','鸟']],animalSpecies)}</select><button data-animal-action="undo" ${!liveDatabase?.formal&&labels.animal_history.length?'':'disabled'} title="已归档照片请在已归档列表中勾选移除">撤销动物操作</button></div>`;
}
function animalBulkBar(mode,targets){
 const selected=targets.filter(g=>animalSelected.has(g.id)),count=selected.reduce((n,g)=>n+g.members.length,0);
 const controls=mode==='other'?`<input id="newAnimalName" data-animal-draft-name="bulk" value="${esc(animalDraftNames.get('bulk')||'')}" placeholder="合并后的名字（可选）" aria-label="批量归档名字"><button class="primary" data-animal-action="create-selected">确认所选组是同一只并命名</button><button data-animal-action="ignore-selected">不关注所选组</button>`:'<button class="primary" data-animal-action="merge-selected">确认所选组属于当前动物并合并</button>';
 return `<div class="merge-bar"><span>已勾选 <b id="animalSelectedCount">${selected.length}</b> 组，共 <b id="animalSelectedPhotos">${count}</b> 张；仅合并你确认属于同一只的组。</span><div class="actions">${controls}<button data-animal-action="clear">清空勾选</button></div><small>可展开检查组内照片、拆出错误项；归档和合并可撤销。使用本机管理入口时会自动保存到 SQLite。</small></div>`;
}
function renderAnimals(root){
 animalNormalize();const entities=Object.values(labels.animal_entities),groups=animalCandidateGroups();
 if(animalView==='auto')animalView=entities.some(e=>e.status==='followed')?'followed':'other';
 const toolbar=animalToolbar(groups);
 if(animalView==='excluded'){
  const invalid=D.pets.filter(p=>!animalOwner(p.id)&&!animalWords[animalKind(p.id)]&&(animalSpecies==='all'||p.kind===animalSpecies));
  root.innerHTML=toolbar+`<section class="group"><h2>待定或框错的检测</h2><p>纠正类别后会恢复为候选小组。</p><div class="faces">${invalid.map(p=>animalObservation(p.id)).join('')||'当前没有待定检测。'}</div></section>`;return;
 }
 if(animalView==='other'){
  const filtered=groups.filter(g=>animalSpecies==='all'||g.kind===animalSpecies);
  root.innerHTML=toolbar+`<p>${filtered.length} 个候选组，共 ${filtered.reduce((n,g)=>n+g.members.length,0)} 张。每组可单独命名，也可以勾选多个组一次归档。</p><div class="animal-group-grid">${filtered.slice(0,animalLimit).map(g=>animalGroupCard(g,'other')).join('')||'<p>当前类别没有候选组。</p>'}</div>${filtered.length>animalLimit?'<button data-animal-action="more">显示更多候选组</button>':''}`+animalBulkBar('other',groups);return;
 }
 const filtered=entities.filter(e=>e.status===animalView&&(animalSpecies==='all'||e.kind===animalSpecies)),active=filtered.find(e=>e.id===animalActive)||filtered[0];animalActive=active?.id||'';
 const recommendations=active?animalRecommendations(active):[],targets=active?animalTargets(active):[];
 root.innerHTML=toolbar+`<div class="person-layout"><aside class="person-list">${filtered.map(e=>`<button data-animal-focus="${e.id}" class="${e.id===active?.id?'active':''}">${e.members[0]?`<img alt="" src="${byId.has(e.members[0])?D.images[byId.get(e.members[0]).crop]:'/api/vision/image?id='+encodeURIComponent(e.members[0])}">`:''}${esc(e.name||e.id)} · ${animalWords[e.kind]} · ${e.members.length} 张</button>`).join('')||'<p>当前列表没有动物档案。可先到“候选分组”给一组命名。</p>'}</aside><div>${active?`<section class="group"><h2>${esc(active.name||active.id)} · ${animalWords[active.kind]}</h2><input data-animal-name="${active.id}" value="${esc(active.name)}" aria-label="修改动物名字"><button data-animal-action="${animalView==='unfollowed'?'follow':'unfollow'}">${animalView==='unfollowed'?'恢复':'不关注此动物'}</button><div class="faces">${active.members.map(id=>animalObservation(id)).join('')}</div></section>${animalView==='followed'?`<h2>可能是同一只的其他组</h2><p>${active.kind==='bird'?'鸟类请从下方手动选择候选组。':'按已确认参考推荐少量同类候选组，分数仅供排序。'}勾选后一次合并，无需逐对打分。</p><div class="animal-group-grid">${recommendations.map(g=>animalGroupCard(g,'followed')).join('')||'<p>暂时没有相似度足够的推荐，可以手动选择其他组。</p>'}</div><section class="group"><label>手动选择同类候选组或已有档案 <select id="manualAnimalGroup"><option value="">选择其他组</option>${targets.map(g=>`<option value="${g.id}">${esc(g.name||g.title||g.id)} · ${g.members.length} 张</option>`).join('')}</select></label><button data-animal-action="add-group">加入勾选</button><p id="animalSelectedNames">${targets.filter(g=>animalSelected.has(g.id)).map(g=>esc(g.name||g.title||g.id)).join('、')}</p></section>${animalBulkBar('followed',targets)}`:''}`:''}</div></div>`;
}
function newAnimalId(){return 'PET-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8)}
function animalCommit(action,groups,active,name=''){
 if(!groups.length){alert('请先勾选候选组');return}
 const kinds=new Set(groups.map(g=>g.kind));
 if(action!=='ignore'&&(kinds.size!==1||(active&&active.kind!==[...kinds][0]))){alert('不能把不同物种合并为同一只，请先纠正类别');return}
 animalCheckpoint();
 if(action==='ignore')for(const group of groups){const id=newAnimalId();labels.animal_entities[id]={id,kind:group.kind,name:'',status:'unfollowed',members:[...group.members],confirmed:false}}
 else{
  const id=active?.id||newAnimalId(),members=[...new Set([...(active?.members||[]),...groups.flatMap(g=>g.members)])];
  const aliases=[...new Set([...(active?.aliases||[]),...groups.filter(g=>!g.draft&&g.name).map(g=>g.name)])];
  for(const group of groups)if(!group.draft)delete labels.animal_entities[group.id];
  labels.animal_entities[id]={...(active||{}),id,kind:groups[0].kind,name:active?.name??name,status:'followed',members,confirmed:true,aliases};animalActive=id;animalView='followed';
 }
 animalSelected.clear();save();render();
}
document.addEventListener('click',e=>{
 const view=e.target.closest('[data-animal-view]');if(view){animalView=view.dataset.animalView;animalSelected.clear();animalLimit=24;render();return}
 const focus=e.target.closest('[data-animal-focus]');if(focus){animalActive=focus.dataset.animalFocus;animalSelected.clear();render();return}
 const expand=e.target.closest('[data-animal-expand]');if(expand){const id=expand.dataset.animalExpand;if(animalExpanded.has(id))animalExpanded.delete(id);else animalExpanded.add(id);render();return}
 const split=e.target.closest('[data-animal-split]');if(split){animalCheckpoint();labels.animal_splits=[...new Set([...labels.animal_splits,split.dataset.animalSplit])];animalSelected.clear();save();render();return}
 const remove=e.target.closest('[data-animal-remove]');if(remove){animalCheckpoint();const owner=animalOwner(remove.dataset.animalRemove);if(owner)owner.members=owner.members.filter(id=>id!==remove.dataset.animalRemove);labels.animal_splits=[...new Set([...labels.animal_splits,remove.dataset.animalRemove])];animalSelected.clear();save();render();return}
 const button=e.target.closest('[data-animal-action]');if(!button)return;
 const action=button.dataset.animalAction,active=labels.animal_entities[animalActive],groups=animalCandidateGroups();
 if(action==='undo'){animalUndo();return}
 if(action==='clear'){animalSelected.clear();render();return}
 if(action==='more'){animalLimit+=24;render();return}
 if(action==='add-group'){const id=$('#manualAnimalGroup').value;if(active&&animalTargets(active).some(g=>g.id===id))animalSelected.add(id);render();return}
 if(action==='create-group'||action==='ignore-group'){const group=groups.find(g=>g.id===button.dataset.animalGroup);if(group)animalCommit(action==='create-group'?'create':'ignore',[group],null,$('#name-'+group.id).value?.trim()||'');return}
 if(action==='create-selected'||action==='ignore-selected'){animalCommit(action==='create-selected'?'create':'ignore',groups.filter(g=>animalSelected.has(g.id)),null,$('#newAnimalName').value?.trim()||'');return}
 if(action==='merge-selected'&&active){animalCommit('merge',animalTargets(active).filter(g=>animalSelected.has(g.id)),active);return}
 if((action==='follow'||action==='unfollow')&&active){animalCheckpoint();if(action==='follow'&&active.confirmed===false){delete labels.animal_entities[active.id];animalView='other'}else{active.status=action==='follow'?'followed':'unfollowed';if(action==='follow')animalView='followed'}animalSelected.clear();save();render()}
});
document.addEventListener('change',e=>{
 const el=e.target;
 if(el.dataset.animalGroupSelect){if(el.checked)animalSelected.add(el.dataset.animalGroupSelect);else animalSelected.delete(el.dataset.animalGroupSelect);const active=labels.animal_entities[animalActive],targets=animalView==='other'?animalCandidateGroups():active?animalTargets(active):[],selected=targets.filter(g=>animalSelected.has(g.id));$('#animalSelectedCount').textContent=selected.length;$('#animalSelectedPhotos').textContent=selected.reduce((n,g)=>n+g.members.length,0)}
 if(el.id==='animalSpecies'){animalSpecies=el.value;animalSelected.clear();animalLimit=24;render()}
 if(el.dataset.animalName){animalCheckpoint();labels.animal_entities[el.dataset.animalName].name=el.value.trim();save()}
 if(el.dataset.animalClass){animalCheckpoint();const id=el.dataset.animalClass;labels.animal_checks[id]=el.value;const owner=animalOwner(id);if(owner&&owner.kind!==animalKind(id))owner.members=owner.members.filter(oid=>oid!==id);animalSelected.clear();save();render()}
});

document.addEventListener('input',e=>{if(e.target.dataset.animalDraftName)animalDraftNames.set(e.target.dataset.animalDraftName,e.target.value)});
