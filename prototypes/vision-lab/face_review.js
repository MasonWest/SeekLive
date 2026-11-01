// Local manual identities sit above immutable machine groups.
const machineGroups=new Map(D.face_groups.map(g=>[g.id,g]));
const faceIndex=new Map([...D.faces,...(D.confirmed_discoveries||[])].map((f,i)=>[f.id,i]));
for(const face of D.confirmed_discoveries||[]){byId.set(face.id,face);if(!samples.has(face.sample))samples.set(face.sample,{id:face.sample,preview:face.preview,captured:face.captured})}
let focusedPerson=D.face_groups[0]?.id||'',personSearch='',personView='followed',selectedPeople=new Set();
function personNormalize(){
 labels.person_links=labels.person_links&&typeof labels.person_links==='object'?labels.person_links:{};
 labels.person_hidden=Array.isArray(labels.person_hidden)?labels.person_hidden:[];
 labels.person_history=Array.isArray(labels.person_history)?labels.person_history.slice(-30):[];
 labels.person_interest=Object.fromEntries(Object.entries(labels.person_interest||{}).filter(([id,value])=>machineGroups.has(id)&&['followed','unfollowed'].includes(value)));
 for(const key of ['face_checks','group_names','animal_checks','pet_pairs'])if(!labels[key]||typeof labels[key]!=='object')labels[key]={};
 for(const face of D.confirmed_discoveries||[])if(labels.face_checks[face.id]===undefined)labels.face_checks[face.id]='keep';
 // Reject malformed/cyclic imported links instead of silently merging people.
 const valid={};
 for(const [a,b] of Object.entries(labels.person_links))if(machineGroups.has(a)&&machineGroups.has(b))valid[a]=b;
 for(const a of machineGroups.keys()){
  const visited=new Set();let b=a;
  while(valid[b]&&valid[b]!==b){if(visited.has(b))throw Error('人物合并记录存在循环，请重新导入有效文件');visited.add(b);b=valid[b]}
 }
 labels.person_links=valid;
}
try{personNormalize()}catch{labels.person_links={};labels.person_history=[];personNormalize();$('#saveState').textContent='人物合并记录无效，已恢复原始组；其他核对保留'}
function personRoot(id){const seen=new Set();while(labels.person_links[id]&&labels.person_links[id]!==id&&!seen.has(id)){seen.add(id);id=labels.person_links[id]}return id}
function personSnapshot(){return JSON.parse(JSON.stringify({person_links:labels.person_links,group_names:labels.group_names,face_checks:labels.face_checks,person_hidden:labels.person_hidden,person_interest:labels.person_interest}))}
function personCheckpoint(){labels.person_history.push(personSnapshot());if(labels.person_history.length>30)labels.person_history.shift()}
function personUndo(){const previous=labels.person_history.pop();if(!previous)return;Object.assign(labels,previous);labels.person_interest=previous.person_interest||{};personNormalize();selectedPeople.clear();focusedPerson=personRoot(focusedPerson);save();render()}
function people(){
 const all=new Map();
 for(const g of D.face_groups){const id=personRoot(g.id);if(!all.has(id))all.set(id,{id,groups:[],members:[]});const p=all.get(id);p.groups.push(g.id);p.members.push(...g.members)}
 for(const face of D.confirmed_discoveries||[]){const p=all.get(personRoot(face.person));if(p&&!p.members.includes(face.id))p.members.push(face.id)}
 return [...all.values()];
}
function personName(p){return String(labels.group_names[p.id]||p.id)}
function personStatus(p){return labels.person_interest[p.id]||(String(labels.group_names[p.id]||'').trim()?'followed':'other')}
function confirmedFaces(p){return p.members.filter(id=>labels.face_checks[id]==='keep')}
function usableFaces(p){return p.members.filter(id=>!['reject','ignore','unsure'].includes(labels.face_checks[id]))}
function faceScore(a,b){return D.face_similarity?.[faceIndex.get(a)]?.[faceIndex.get(b)]??-1}
function representatives(p){
 const available=usableFaces(p),confirmed=available.filter(id=>labels.face_checks[id]==='keep');
 const pool=confirmed.length?confirmed:available;
 if(!pool.length)return [];
 const selected=[pool.reduce((a,b)=>(byId.get(b).face_pixels||0)>(byId.get(a).face_pixels||0)?b:a)];
 // Diversity across confirmed references preserves side views instead of
 // collapsing all evidence into a single mean face.
 while(selected.length<Math.min(confirmed.length?6:3,pool.length)){
  const rest=pool.filter(id=>!selected.includes(id));
  rest.sort((a,b)=>Math.max(...selected.map(s=>faceScore(a,s)))-Math.max(...selected.map(s=>faceScore(b,s))));selected.push(rest[0]);
 }
 return selected;
}
function personRecommendations(anchor,all){
 const refs=representatives(anchor).filter(id=>labels.face_checks[id]==='keep');if(personStatus(anchor)!=='followed'||!refs.length||!D.face_similarity)return [];
 return all.filter(p=>p.id!==anchor.id&&personStatus(p)==='other'&&!anchor.groups.some(a=>p.groups.some(b=>labels.person_hidden.includes(pairKey(a,b))))).map(p=>{
  const candidates=usableFaces(p);const scores=candidates.map(b=>Math.max(...refs.map(a=>faceScore(a,b)))).sort((a,b)=>b-a);
  return {person:p,score:scores.length?(scores[0]*.8+(scores[1]??scores[0])*.2):-1,referenceCount:refs.filter(id=>labels.face_checks[id]==='keep').length};
 }).filter(r=>r.score>=.45).sort((a,b)=>b.score-a.score).slice(0,8);
}
function mergePeople(){
 const all=people(),target=all.find(p=>p.id===personRoot(focusedPerson));if(!target)return;
 const merging=all.filter(p=>p.id===target.id||(selectedPeople.has(p.id)&&personStatus(p)!=='unfollowed'));if(merging.length<2)return;
 personCheckpoint();
 for(const p of merging){for(const g of p.groups)labels.person_links[g]=target.id;
  for(const id of usableFaces(p))if(labels.face_checks[id]!=='unsure')labels.face_checks[id]='keep';
 }
 selectedPeople.clear();save();render();
}
function renderFaces(root){
 const all=people(),counts={followed:0,other:0,unfollowed:0};all.forEach(p=>counts[personStatus(p)]++);
 const inView=all.filter(p=>personStatus(p)===personView);
 const visible=inView.filter(p=>(personName(p)+' '+p.groups.join(' ')).toLowerCase().includes(personSearch.toLowerCase()));
 const active=visible.find(p=>p.id===personRoot(focusedPerson))||visible[0];
 const pending=all.filter(p=>personStatus(p)==='followed'&&!confirmedFaces(p).length);
 const toolbar=`<p>只整理你关心的人。其他人物不需要逐一核对；不关注只隐藏人物，不删除照片。</p><div class="actions">${[['followed','关注的人'],['other','其他人物'],['unfollowed','不关注']].map(([id,title])=>`<button data-person-view="${id}" class="${personView===id?'primary':''}" aria-pressed="${personView===id}">${title} · ${counts[id]}</button>`).join('')}</div><div class="actions"><input id="personSearch" aria-label="搜索人物或原组号" placeholder="搜索当前列表" value="${esc(personSearch)}"><button data-person-action="undo" ${labels.person_history.length?'':'disabled'}>撤销上一步人物操作</button><button data-person-action="restore">恢复隐藏推荐</button></div>${pending.length?`<div class="note">${pending.length} 个关注人物尚未确认参考脸。<div class="actions">${pending.map(p=>`<button data-person-pending="${p.id}">${esc(personName(p))} · 去确认</button>`).join('')}</div><small>名字和参考确认分别保存；不会把已命名照片自动当成正确参考。</small></div>`:''}`;
 if(!active){selectedPeople.clear();root.innerHTML=toolbar+`<div class="empty">${personSearch?'没有匹配的人物':personView==='followed'?'还没有关注的人。可以从“其他人物”选择并命名。':personView==='unfollowed'?'没有不关注的人物。':'其他人物已经整理完毕。'}</div>`;return}
 focusedPerson=active.id;
 const recommendations=personRecommendations(active,all),refs=confirmedFaces(active),status=personStatus(active);
 root.innerHTML=toolbar+`<div class="person-layout"><aside class="person-list" aria-label="人物列表">${visible.map(p=>`<button data-person-focus="${p.id}" class="${p.id===active.id?'active':''}">${representatives(p)[0]?`<img alt="" src="${D.images[byId.get(representatives(p)[0]).crop]}">`:''}${esc(personName(p))}<small> · ${p.members.length} 张脸 / ${p.groups.length} 组${personStatus(p)==='followed'&&!confirmedFaces(p).length?' · 待确认参考':''}</small></button>`).join('')}</aside><div>
 <section class="group"><div class="group-hd"><h2>${esc(personName(active))}</h2><input id="personName" data-group="${active.id}" aria-label="${active.id} 本机别名" placeholder="命名后加入关注的人" value="${esc(labels.group_names[active.id]||'')}"></div><p>包含原组 ${esc(active.groups.join('、'))}。已确认 ${refs.length} 张参考脸，最多选取 6 张不同角度用于推荐。</p>
 <div class="actions">${status==='unfollowed'?'<button data-person-action="follow">恢复关注</button>':'<button class="primary" data-person-action="confirm">保存名字并确认未排除的人脸为参考</button><button data-person-action="unfollow">不关注此人</button>'}${status==='other'?'<button data-person-action="follow">暂不命名，加入关注</button>':''}</div><small>确认前请排除错误人脸。“不确定”“排除”“忽略”的脸不会作为参考。单独填写名字不会自动确认人脸。</small>
 <div class="faces">${active.members.map(id=>imageCard(id,`<select aria-label="核对 ${id}" data-face="${id}">${choices([['','待核对'],['keep','确认本人 · 作为参考'],['reject','不是这个人 · 排除'],['ignore','非真实人脸／忽略'],['unsure','不确定 · 不作参考']],labels.face_checks[id]||'')}</select>`)).join('')}</div></section>
 ${status==='followed'?`<h2>可能属于这个人的遗漏照片</h2><p>${refs.length?'只用你确认的参考脸寻找其他人物中的候选，低相似候选不再反复展示。':'请先确认至少一张参考脸，再寻找遗漏照片。'}推荐分数不是准确率；已有命名的人物可以在下方手动合并。</p>
 <div class="person-candidates">${recommendations.map(({person:p,score})=>`<section class="group"><label><input type="checkbox" data-person-select="${p.id}" ${selectedPeople.has(p.id)?'checked':''}> ${esc(personName(p))} · ${p.members.length} 张脸</label><small> 推荐分数 ${score.toFixed(3)}</small><div class="faces">${representatives(p).slice(0,3).map(id=>imageCard(id)).join('')}</div><button data-person-hide="${p.id}">隐藏这条推荐</button></section>`).join('')||'<p>当前没有需要核对的相似候选，无需清空“其他人物”。</p>'}</div>
 <div class="group"><label for="manualPerson">手动选择其他人物组</label><select id="manualPerson"><option value="">选择要合并的组</option>${all.filter(p=>p.id!==active.id&&personStatus(p)!=='unfollowed').map(p=>`<option value="${p.id}">${esc(personName(p))} · ${esc(p.groups.join('、'))}</option>`).join('')}</select><button data-person-action="add">加入勾选</button></div>
 <div class="merge-bar"><span>已选 ${selectedPeople.size} 个候选：${[...selectedPeople].map(id=>esc(personName(all.find(p=>p.id===id)||{id}))).join('、')||'尚未选择'}</span><div class="actions"><button class="primary" data-person-action="merge" ${selectedPeople.size?'':'disabled'}>合并到当前人物并确认人脸</button><button data-person-action="clear">清空勾选</button></div><small>合并可撤销；请导出结果备份。</small></div>`:status==='unfollowed'?'<p>此人不参与日常人物列表和匹配推荐。已有姓名、参考与照片保留，恢复关注后可继续整理。</p>':'<p>这里的人物无需逐一命名或核对。遇到关心的人再关注即可。</p>'}
 </div></div>`;
}
document.addEventListener('click',e=>{
 const view=e.target.closest('[data-person-view]');if(view){personView=view.dataset.personView;personSearch='';selectedPeople.clear();render();return}
 const pending=e.target.closest('[data-person-pending]');if(pending){personView='followed';personSearch='';focusedPerson=pending.dataset.personPending;selectedPeople.clear();render();return}
 const focus=e.target.closest('[data-person-focus]');if(focus){focusedPerson=focus.dataset.personFocus;selectedPeople.clear();render();return}
 const hide=e.target.closest('[data-person-hide]');if(hide){const all=people(),a=all.find(p=>p.id===focusedPerson),b=all.find(p=>p.id===hide.dataset.personHide);if(a&&b){personCheckpoint();for(const x of a.groups)for(const y of b.groups)labels.person_hidden.push(pairKey(x,y));selectedPeople.delete(b.id);save();render()}return}
 const button=e.target.closest('[data-person-action]');if(!button)return;
 const action=button.dataset.personAction;
 if(action==='merge')mergePeople();
 if(action==='undo')personUndo();
 if(action==='clear'){selectedPeople.clear();render()}
 if(action==='restore'){personCheckpoint();labels.person_hidden=[];save();render()}
 if(action==='add'){const id=$('#manualPerson').value;if(machineGroups.has(id)&&id!==focusedPerson)selectedPeople.add(id);render()}
 if(action==='follow'||action==='unfollow'){personCheckpoint();labels.person_interest[focusedPerson]=action==='follow'?'followed':'unfollowed';selectedPeople.clear();if(action==='follow')personView='followed';save();render()}
 if(action==='confirm'){const p=people().find(p=>p.id===focusedPerson);if(!p)return;personCheckpoint();const name=$('#personName').value;if(typeof name==='string')labels.group_names[p.id]=name.trim();labels.person_interest[p.id]='followed';for(const id of usableFaces(p))labels.face_checks[id]='keep';personView='followed';selectedPeople.clear();save();render()}
});
document.addEventListener('change',e=>{const id=e.target.dataset.personSelect;if(id){if(e.target.checked)selectedPeople.add(id);else selectedPeople.delete(id);render()}if(e.target.dataset.face)render()});
document.addEventListener('input',e=>{if(e.target.id==='personSearch'){personSearch=e.target.value;render();const input=$('#personSearch');input.focus();input.setSelectionRange(personSearch.length,personSearch.length)}});
