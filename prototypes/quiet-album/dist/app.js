const assets=[4,0,1,3,2,5,6,7,4,1,3,0,5,6,7,2,4,5];
const captions=['湖畔的清晨','山风与晴空','沿着湖边走走','远山与水色','停一停，看看鸟','光落在森林里','山野里的好天气','在湖边发呆','再看一眼山间','旅行中的小片刻','慢慢走的一天','把风景带回家','林间散步','秋日出游','山的另一边','小小的相遇','山间的好时光','日常也有微光'];
const photos=assets.map((asset,i)=>({id:i,asset,caption:captions[i],date:i<12?'2026年10月8日':'2026年10月3日',name:`IMG_${String(i+1).padStart(4,'0')}.JPG`}));
const favorites=new Set([0,5,14]), selected=new Set();let nav='all',mode='day',selecting=false,current=0,query='';
const $=s=>document.querySelector(s);const $$=s=>[...document.querySelectorAll(s)];
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>$('#toast').hidden=true,2800)}
function tile(p,i){return `<button class="photo ${i%7===0?'wide':''} ${selected.has(p.id)?'selected':''}" data-photo="${p.id}" aria-label="${p.date}，${p.caption}"><img src="assets/${p.asset}.jpg" alt="${p.caption}" loading="lazy">${p.id===1||p.id===8?'<span>◉ LIVE</span>':''}${favorites.has(p.id)?'<b class="star">♡</b>':''}<b class="pick">${selected.has(p.id)?'✓':''}</b></button>`}
function render(){
 let list=photos.filter(p=>(nav!=='favorites'||favorites.has(p.id))&&(!query||`${p.caption}${p.date}${p.name}`.toLowerCase().includes(query.toLowerCase())));
 $('#pageTitle').innerHTML=(nav==='favorites'?'我的收藏':nav==='albums'?'相册':'所有照片')+'<span class="title-dot">.</span>';
 $('#subtitle').textContent=nav==='favorites'?'偏爱的瞬间，再看多少次都喜欢。':nav==='albums'?'把一起走过的日子，认真收藏。':'那些平凡又闪光的日子，都在这里。';
 $('#memories').hidden=nav!=='all'||!!query;$('#periodTitle').textContent=query?'搜索结果':mode==='year'?'2026 年':mode==='month'?'十月的回忆':'2026 年 10 月';$('.period>span').textContent=`${list.length} 个瞬间`;
 if(nav==='albums'||mode!=='day'){$('#gallery').innerHTML='<div class="album-grid">'+[{a:4,t:mode==='year'?'2026 · 我们的这一年':'十月，去山里',n:12},{a:5,t:'日常里的微光',n:6}].map(x=>`<button class="album-card" data-open-album><img src="assets/${x.a}.jpg" alt="${x.t}"><strong>${x.t}</strong><small>${x.n} 个瞬间 · 2026</small></button>`).join('')+'</div>'}
 else $('#gallery').innerHTML=list.length?[...new Set(list.map(p=>p.date))].map(date=>`<div class="day-heading"><b>${date.includes('8日')?'10 月 8 日':'10 月 3 日'}</b><span>${date.includes('8日')?'星期四 · 秋日出游':'星期六 · 日常小记'}</span><i class="line"></i><span class="date-end">${list.filter(p=>p.date===date).length} PHOTOS</span></div><div class="photo-grid">${list.filter(p=>p.date===date).map(tile).join('')}</div>`).join(''):'<div class="empty">没有找到这个瞬间，换个关键词试试。</div>';
 document.body.classList.toggle('selecting',selecting);$('#selection').hidden=!selecting;$('#selectedCount').textContent=selected.size;$$('[data-action="select"]').forEach(b=>b.classList.toggle('on',selecting));$$('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav===nav));$$('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===mode));$('.favorite-count').textContent=favorites.size;
}
function openPhoto(id){current=(id+photos.length)%photos.length;const p=photos[current];$('#viewerImage').src=`assets/${p.asset}.jpg`;$('#viewerImage').alt=p.caption;$('#viewerTitle').textContent=p.date;$('#viewerCaption').textContent=p.caption+' · '+p.name;$('#viewerCount').textContent=`${current+1} / ${photos.length}`;$('[data-action="favorite"]').textContent=favorites.has(current)?'♥':'♡';if(!$('#lightbox').open)$('#lightbox').showModal()}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
 if(b.dataset.nav){nav=b.dataset.nav;mode='day';query='';$('#search').value='';render()}
 if(b.dataset.mode){mode=b.dataset.mode;render()}
 if(b.hasAttribute('data-photo')){const id=+b.dataset.photo;if(selecting){selected.has(id)?selected.delete(id):selected.add(id);render()}else openPhoto(id)}
 if(b.hasAttribute('data-index'))openPhoto(+b.dataset.index);
 if(b.hasAttribute('data-open-album')){nav='all';mode='day';render();$('.library').scrollIntoView({behavior:'smooth'})}
 if(b.dataset.year){if(b.dataset.year==='2025')toast('示例图库仅包含 2026 年照片');else{mode='year';render()}}
 if(b.dataset.demo)toast(b.dataset.demo+' · 正式版接入现有功能');
 switch(b.dataset.action){case 'search':$('.search-row').hidden=false;$('#search').focus();break;case 'cancel-search':$('.search-row').hidden=true;query='';$('#search').value='';render();break;case 'select':selecting=!selecting;if(!selecting)selected.clear();render();break;case 'settings':$('#settings').showModal();break;case 'close-settings':$('#settings').close();break;case 'close-viewer':$('#lightbox').close();break;case 'next':openPhoto(current+1);break;case 'prev':openPhoto(current-1);break;case 'favorite':favorites.has(current)?favorites.delete(current):favorites.add(current);openPhoto(current);render();break;case 'share':toast(selected.size?'分享预览 · 原型不会创建真实分享链接':'先选择想分享的照片');break}
});
$('#search').addEventListener('input',e=>{query=e.target.value.trim();mode='day';nav='all';render()});document.addEventListener('keydown',e=>{if($('#lightbox').open){if(e.key==='ArrowRight')openPhoto(current+1);if(e.key==='ArrowLeft')openPhoto(current-1)}});$$('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close()}}));render();
