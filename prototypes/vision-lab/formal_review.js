// Live SQL results replace the prototype snapshot for online browsing only.
let formalSummary=null,formalEntity='',formalMode='pending',formalOffset=0,formalSelected=new Set(),formalGeneration=0,formalPhotos=[];
function formalImage(id,full=false){return '/api/vision/image?'+new URLSearchParams({id,view:full?'photo':'crop'})}
async function formalApi(path,body){
 const response=await fetch(path,{method:body?'PUT':'GET',headers:{'Content-Type':'application/json','X-Vision-Token':liveDatabase.token},...(body?{body:JSON.stringify(body)}:{})});
 const data=await response.json();if(!response.ok){if(response.status===409)databaseConflict=true;throw Error(data.error||'正式结果读取失败')}return data;
}
function formalOverview(root){
 if(!formalSummary){root.innerHTML='<p>正在读取正式数据库统计…</p>';return}
 const s=formalSummary,p=s.progress;
 root.innerHTML=`<div class="stats">${[['正式已分析照片',p.analysed],['当前图库照片',p.media],['人脸检测实例',s.detections.person||0],['猫检测实例',s.detections.cat||0],['狗检测实例',s.detections.dog||0],['鸟检测实例',s.detections.bird||0],['人工确认归属',s.confirmed],['新增匹配待确认',s.pending]].map(([label,value])=>`<div class="stat"><b>${value}</b><span>${label}</span></div>`).join('')}</div><section class="group"><h2>正式结果已接通</h2><p>以上统计直接来自 SQLite。点击人物或动物档案，可查看全库新增候选和已归档照片，按页批量确认。</p><p>新增候选不会自动增加“已确认”数量；确认后立即归入对应档案。检测实例数不是人数或动物数量。</p><button data-formal-all="1">查看全库匹配结果</button></section><details class="group"><summary>最初原型样本</summary><p>最初 ${D.summary.decoded} 张是试验样本，旧分组与命名操作仍保留在每个档案的“初始样本”中。</p></details>`;
}
function formalAll(root){
 if(!formalSummary){root.innerHTML='<p>正在读取全库匹配结果…</p>';return}
 const entities=formalSummary.entities.filter(e=>e.status==='followed');
 root.innerHTML=`<section class="group"><h2>全库人物与动物匹配</h2><p>已分析 ${formalSummary.progress.analysed} 张，当前新增待确认 ${formalSummary.pending} 条。进入档案查看照片并批量归档。</p></section><div class="animal-group-grid">${entities.map(e=>`<section class="group"><h2>${esc(e.name||e.id)}</h2><p>${esc(({person:'人物',cat:'猫',dog:'狗',bird:'鸟'})[e.kind])} · 已归档 ${e.confirmed} · 新增待确认 ${e.pending}</p><button data-formal-focus="${e.id}">查看照片</button></section>`).join('')}</div>`;
}
async function refreshFormalSummary(){
 try{formalSummary=await formalApi('/api/vision/summary');formalBadges();if(current==='summary')formalOverview($('#content'));if(current==='discoveries')formalAll($('#content'))}
 catch(error){if(current==='summary'||current==='discoveries')$('#content').textContent=error.message}
}
function formalBadges(){
 if(!formalSummary)return;
 document.querySelectorAll('[data-person-focus],[data-animal-focus]').forEach(button=>{
  const id=button.dataset.personFocus||button.dataset.animalFocus,e=formalSummary.entities.find(e=>e.id===id);if(!e)return;
  let badge=button.querySelector('.formal-count');if(!badge){badge=document.createElement('small');badge.className='formal-count';button.appendChild(badge)}
  badge.textContent=' · 正式已归档 '+e.confirmed+' / 新增 '+e.pending;
 });
}
function formalMount(){
 if(!liveDatabase?.formal)return;
 if(current==='summary'){formalOverview($('#content'));return}
 if(current==='discoveries'){formalAll($('#content'));return}
 let entity=current==='faces'&&personView==='followed'?focusedPerson:current==='animals'&&animalView==='followed'?animalActive:'';
 if(!entity)return;
 const target=$('#content').querySelector?.('.person-layout > div');if(!target)return;
 const old=document.createElement('details');old.className='group';const title=document.createElement('summary');title.textContent='初始样本、命名与分组合并（可选）';old.appendChild(title);
 while(target.firstChild)old.appendChild(target.firstChild);target.appendChild(old);
 const section=document.createElement('section');section.className='group';section.id='formalPhotos';target.insertBefore(section,old);
 if(formalEntity!==entity){formalEntity=entity;formalMode='pending';formalOffset=0;formalSelected.clear()}
 formalBadges();loadFormalPhotos();
}
async function loadFormalPhotos(){
 const slot=$('#formalPhotos');if(!slot)return;const generation=++formalGeneration,entity=formalEntity,mode=formalMode,offset=formalOffset;
 slot.innerHTML='<p>正在读取这个档案的正式照片…</p>';
 try{
  const page=await formalApi('/api/vision/photos?'+new URLSearchParams({entity,mode,offset,limit:24}));
  if(generation!==formalGeneration||entity!==formalEntity||current!=='faces'&&current!=='animals')return;
  formalPhotos=page.items;const e=formalSummary?.entities.find(e=>e.id===entity);
  slot.innerHTML=`<h2>${esc(page.name||entity)} · 正式照片</h2><div class="actions"><button data-formal-mode="pending" class="${mode==='pending'?'primary':''}">新增待确认 ${e?.pending??(mode==='pending'?page.total:'')}</button><button data-formal-mode="confirmed" class="${mode==='confirmed'?'primary':''}">已归档 ${e?.confirmed??(mode==='confirmed'?page.total:'')}</button><button data-formal-action="refresh">刷新结果</button></div><p>${mode==='pending'?'这些是全库新匹配，可勾选多张一次确认归入。':'这些照片已获得人工确认。确认新照片后，这里的数量会增加。'} 共 ${page.total} 条。</p><div class="faces">${page.items.map(item=>`<div class="item"><button data-formal-open="${item.id}" aria-label="查看正式照片"><img loading="lazy" src="${formalImage(item.id)}" alt="${mode==='pending'?'待确认':'已归档'}照片"></button><small>${esc(item.captured||'日期未知')}${item.score===null?'':' · 相似度 '+item.score}</small>${(mode==='pending'||page.kind!=='person')?`<label><input type="checkbox" data-formal-select="${item.id}" ${formalSelected.has(item.id)?'checked':''}> 选择这张</label>`:''}</div>`).join('')||'<p>当前没有照片。</p>'}</div><div class="actions">${offset>0?'<button data-formal-page="previous">上一页</button>':''}${offset+page.items.length<page.total?'<button data-formal-page="next">下一页</button>':''}</div>${(mode==='pending'||page.kind!=='person')?'<div class="merge-bar"><span id="formalSelectedCount">已选 '+formalSelected.size+' 张</span><div class="actions"><button data-formal-action="select-page">全选本页</button><button data-formal-action="clear">清空选择</button><button class="primary" data-formal-action="confirm">确认所选并归档</button><button data-formal-action="reject">所选不属于这个档案</button></div></div>':''}<p id="formalActionStatus" role="status" aria-live="polite"></p><button data-formal-action="undo">撤销这个档案上次正式操作</button>`;
  if(mode==='confirmed'&&page.kind!=='person'){
   slot.querySelector('[data-formal-action="confirm"]')?.remove();
   const remove=slot.querySelector('[data-formal-action="reject"]');if(remove){remove.dataset.formalAction='remove';remove.textContent='从此档案移除所选（保留原照片）'}
  }
 }catch(error){if(generation===formalGeneration)slot.textContent=error.message}
}
function formalFeedback(message){const status=$('#formalActionStatus');if(status)status.textContent=message;$('#saveState').textContent=message}
async function submitFormal(action){
 if(databaseSaving||databasePending||databaseConflict){formalFeedback(databaseSaving?'正在保存，请稍候…':databaseConflict?'档案已在其他页面更新，请刷新页面后重新勾选；当前选择尚未提交。':'还有修改未保存，请稍候再归档。');return}
 if(action!=='undo'&&!formalSelected.size){alert('请先选择照片');return}
 databaseSaving=true;formalFeedback('正在保存所选照片，请稍候…');
 try{
  const response=await formalApi('/api/vision/decision',{entity:formalEntity,ids:action==='undo'?[]:[...formalSelected],action,revision:databaseRevision});
  databaseRevision=response.revision;
  if(databasePending){databaseConflict=true;$('#saveState').textContent='批量操作已提交，但同时有其他编辑。请导出当前修改后刷新，避免覆盖。';return}
  labels=response.state;personNormalize();animalNormalize();offlineSave();$('#saveState').textContent='已保存到 SQLite · '+(action==='confirm'?'已归档 ':action==='reject'?'已排除 ':action==='remove'?'已移除归属 ':'已撤销 ')+response.changed+' 条';
  formalSelected.clear();formalOffset=0;if(action==='confirm')formalMode='confirmed';await refreshFormalSummary();await loadFormalPhotos();
 }catch(error){formalFeedback(error.message+'；请刷新页面读取最新记录。所选照片尚未完成归档。')}finally{databaseSaving=false}
}
function syncFormalSelection(){
 document.querySelectorAll('#formalPhotos [data-formal-select]').forEach(input=>{input.checked=formalSelected.has(input.dataset.formalSelect)});
 const count=$('#formalSelectedCount');if(count)count.textContent='已选 '+formalSelected.size+' 张';
}
if(liveDatabase?.formal){
 const prototypeRender=render;
 render=function(){prototypeRender();formalMount()};
 document.addEventListener('change',event=>{const id=event.target.dataset.formalSelect;if(!id)return;if(event.target.checked){if(formalSelected.size>=120){event.target.checked=false;alert('一次最多确认 120 张');return}formalSelected.add(id)}else formalSelected.delete(id);$('#formalSelectedCount').textContent='已选 '+formalSelected.size+' 张'});
 document.addEventListener('click',event=>{
  const all=event.target.closest('[data-formal-all]');if(all){current='discoveries';render();return}
  const focus=event.target.closest('[data-formal-focus]');if(focus){const e=formalSummary?.entities.find(e=>e.id===focus.dataset.formalFocus);if(!e)return;if(e.kind==='person'){current='faces';personView='followed';personSearch='';focusedPerson=e.id}else{current='animals';animalView='followed';animalSpecies='all';animalActive=e.id}render();return}
  const mode=event.target.closest('[data-formal-mode]');if(mode){if(formalMode===mode.dataset.formalMode)return;formalMode=mode.dataset.formalMode;formalOffset=0;formalSelected.clear();loadFormalPhotos();return}
  const page=event.target.closest('[data-formal-page]');if(page){formalOffset=Math.max(0,formalOffset+(page.dataset.formalPage==='next'?24:-24));loadFormalPhotos();return}
  const open=event.target.closest('[data-formal-open]');if(open){const item=formalPhotos.find(i=>i.id===open.dataset.formalOpen);if(!item)return;const b=item.box;$('#photo').innerHTML=`<div class="picture"><img src="${formalImage(item.id,true)}" alt="正式照片预览">${b?`<svg viewBox="0 0 1 1" preserveAspectRatio="none"><rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="none" stroke="#ff5133" stroke-width="0.006"/></svg>`:''}</div>`;$('#photoInfo').textContent=item.captured||'日期未知';$('#viewer').showModal();return}
  const button=event.target.closest('[data-formal-action]');if(!button)return;const action=button.dataset.formalAction;
  if(action==='refresh'){refreshFormalSummary();loadFormalPhotos()}
  else if(action==='clear'){formalSelected.clear();syncFormalSelection()}
  else if(action==='select-page'){for(const i of formalPhotos){if(formalSelected.size<120)formalSelected.add(i.id)}syncFormalSelection()}
  else submitFormal(action);
 });
 refreshFormalSummary();
}
