// Served locally: SQLite is authoritative. File pages retain JSON export fallback.
let databaseRevision=liveDatabase?.revision,databasePending=null,databaseSaving=false,databaseConflict=false;
const offlineSave=save;
save=function(){offlineSave();if(liveDatabase){databasePending=JSON.parse(JSON.stringify(labels));flushDatabase()}};
async function flushDatabase(){
 if(databaseSaving||!databasePending||databaseConflict)return;
 databaseSaving=true;const state=databasePending;databasePending=null;
 try{
  const response=await fetch('/api/state',{method:'PUT',headers:{'Content-Type':'application/json','X-Vision-Token':liveDatabase.token},body:JSON.stringify({revision:databaseRevision,state})});
  if(response.status===409){databaseConflict=true;databasePending=state;throw Error('其他页面已更新档案。请先导出当前修改，再刷新后合并，避免覆盖。')}
  if(!response.ok)throw Error('数据库未保存，请导出备份。'+(await response.json()).error);
  const result=await response.json();databaseRevision=result.revision;$('#saveState').textContent='已保存到本机 SQLite · 版本 '+databaseRevision;if(liveDatabase?.formal)refreshFormalSummary();
 }catch(error){databasePending??=state;$('#saveState').textContent=error.message;databaseSaving=false;return}
 databaseSaving=false;if(databasePending)flushDatabase();
}
if(liveDatabase)$('#saveState').textContent='本机 SQLite 已连接 · 版本 '+databaseRevision;
globalThis.addEventListener?.('beforeunload',event=>{if(liveDatabase&&(databaseSaving||databasePending)){event.preventDefault();event.returnValue='修改仍在保存，请稍候'}});
if(liveDatabase?.vision&&typeof document.querySelector('header')?.appendChild==='function'){
 const panel=document.createElement('div');panel.className='note';panel.style.marginTop='14px';
 panel.innerHTML='<span id="visionStatus">正在读取后台识别进度…</span><div class="actions"><button data-vision-action="pause">暂停识别</button><button data-vision-action="resume">继续识别</button><button data-vision-action="scan">核对新增照片</button><button data-vision-action="retry">重试失败项</button></div>';
 document.querySelector('header').appendChild(panel);
 let polling=false;
 async function updateVision(action){
  if(polling)return;polling=true;
  try{
   const response=await fetch(action?'/api/vision/control':'/api/vision/status',{method:action?'PUT':'GET',headers:{'Content-Type':'application/json','X-Vision-Token':liveDatabase.token},...(action?{body:JSON.stringify({action})}:{})});
   if(!response.ok)throw Error('暂时无法读取后台状态');const s=await response.json();
   const remaining=(s.queue.pending||0)+(s.queue.running||0);
   const state=s.paused?'已暂停':s.media>0&&s.analysed>=s.media?(remaining?'检测已完成，正在用最新确认更新匹配 · 待处理 '+remaining+' 项':'当前全库分析已完成'):s.yielding?'看图优先，识别等待中':s.workerAlive?'正在后台处理':'等待后台启动';
   const errors={ModelsUnavailable:'识别模型未就绪',WorkerUnavailable:'后台进程暂不可用',IndexSyncFailed:'暂时无法读取图库索引',SupervisorFailed:'后台需要重启'};
   $('#visionStatus').textContent='后台识别：'+state+' · 已完成 '+s.analysed+' / '+s.media+' 张 · 失败 '+(s.queue.failed||0)+' 项'+(s.error?' · '+(errors[s.error]||'后台暂时遇到问题'):'');
  }catch(error){$('#visionStatus').textContent=error.message}finally{polling=false}
 }
 panel.addEventListener('click',event=>{const button=event.target.closest('[data-vision-action]');if(button)updateVision(button.dataset.visionAction)});
 updateVision();globalThis.setInterval?.(()=>{if(!document.hidden)updateVision()},3000);
}
