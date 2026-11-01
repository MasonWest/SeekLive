// Optional discovery browsing: no new mandatory review queue or automatic labels.
let expandedDiscoveryPeople=new Set();
function discoveryKey(c){const parts=[D.person_discovery.reference_signature,c.id,c.person];if(c.source_version)parts.push(c.source_version.join(':'));return parts.join('|')}
function renderDiscoveries(root){
 const run=D.person_discovery;
 if(!run){root.innerHTML='<div class="empty">还没有扩展扫描结果。原有的人物核对与命名已保留。</div>';return}
 const active=new Map(people().filter(p=>personStatus(p)==='followed').map(p=>[p.id,p]));
 const canonical=new Map();
 const integrated=new Set((D.confirmed_discoveries||[]).map(f=>f.id));
 for(const p of run.people){const id=personRoot(p.id);if(active.has(id)&&!canonical.has(id))canonical.set(id,{id,matches:[]})}
 for(const c of run.candidates){if(integrated.has(c.id))continue;const group=canonical.get(personRoot(c.person));if(group)group.matches.push(c)}
 const groups=[...canonical.values()].filter(p=>p.matches.length);
 const s=run.summary;
 root.innerHTML=`<div class="group"><h2>为关注的人找到的照片候选</h2><p>${s.batch_count?'已汇总 '+s.batch_count+' 批结果，共分析':'本轮分析'} ${s.decoded} 张照片，使用 ${s.references} 张人工确认参考。${s.new_batch_images?'本次新增分析 '+s.new_batch_images+' 张，其余复用已有分析。':''}这里只列出较有把握的候选，不要求你逐一核对。</p><p>没有自动把照片归入任何人物。推荐不等于确认身份，分数不是准确率；没出现在这里也不表示照片中没有这个人。</p><small>每人先显示 3 张；连拍近重复已粗略折叠。原有“其他人物”无需继续整理。当前结果来自扫描时的参考样本，后续修改参考需重新扫描匹配。</small></div>`+
 groups.map(p=>{
  const visible=expandedDiscoveryPeople.has(p.id)?p.matches:p.matches.slice(0,3);
  return `<section class="group"><div class="group-hd"><h2>${esc(personName(active.get(p.id)))} <small>${p.matches.length} 张精选候选</small></h2>${p.matches.length>3?`<button data-discovery-expand="${p.id}">${expandedDiscoveryPeople.has(p.id)?'收起':'展开其余候选'}</button>`:''}</div><div class="faces">${visible.map(c=>`<div class="item"><button data-discovery-open="${c.id}" aria-label="查看新照片预览"><img loading="lazy" src="${D.images[c.crop]}" alt="待确认人脸"></button><small>${esc(c.captured||'拍摄日期未知')} · 推荐分数 ${c.score}</small><select data-discovery-check="${discoveryKey(c)}" aria-label="可选核对新照片">${choices([['','可选：暂不核对'],['same','确认是这个人'],['different','不是这个人'],['unsure','不确定']],labels.discovery_checks?.[discoveryKey(c)]||'')}</select></div>`).join('')}</div></section>`;
 }).join('')+(!groups.length?'<div class="empty">这一轮没有为当前关注人物筛出足够明确的候选，已有命名与参考均保留。</div>':'');
}
document.addEventListener('click',e=>{
 const expand=e.target.closest('[data-discovery-expand]');if(expand){const id=expand.dataset.discoveryExpand;if(expandedDiscoveryPeople.has(id))expandedDiscoveryPeople.delete(id);else expandedDiscoveryPeople.add(id);render();return}
 const open=e.target.closest('[data-discovery-open]');if(!open)return;
 const c=D.person_discovery?.candidates.find(c=>c.id===open.dataset.discoveryOpen);if(!c)return;
 const b=c.box;$('#photo').innerHTML=`<div class="picture"><img src="${D.images[c.preview]}" alt="新照片预览"><svg viewBox="0 0 1 1" preserveAspectRatio="none"><rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="none" stroke="#ff5133" stroke-width="0.006"/></svg></div>`;$('#photoInfo').textContent='扩展扫描候选 · '+(c.captured||'拍摄日期未知')+' · 尚未自动归入人物';$('#viewer').showModal();
});
document.addEventListener('change',e=>{const key=e.target.dataset.discoveryCheck;if(key){labels.discovery_checks??={};labels.discovery_checks[key]=e.target.value;save()}});
