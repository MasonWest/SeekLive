// Standalone density study. Extra entries repeat the same public sample photos.
const levels=[{name:'大图',size:230},{name:'标准',size:122},{name:'密集',size:72},{name:'月份',size:28},{name:'年份',size:14}];
let density=1;
// Populate a realistic wall while clearly marking the repeated demo material.
const originals=photos.slice();
for(let i=18;i<900;i++)photos.push({...originals[i%18],id:i,name:`DEMO_${i}.JPG`,date:i<450?'2026年10月8日':'2026年10月3日'});
const baseRender=render;
render=function(){mode='day';baseRender();$('#memories').hidden=true;applyDensity();$('.period>span').textContent='900 个演示格 · 重复示例素材';};
function applyDensity(){
 const lv=levels[density];
 const width=$('#gallery').clientWidth;
 const cols=Math.max(2,Math.round(width/lv.size));
 document.documentElement.style.setProperty('--columns',cols);
 document.body.classList.toggle('overview',density>=3);
 document.body.classList.toggle('year-overview',density===4);
 $('#periodTitle').innerHTML=density===4?'<strong>2026</strong><em>年</em>':'<small>2026</small><strong>十月</strong>';
 if(!document.querySelector('.mobile-date-jump')){const n=document.createElement('div');n.className='mobile-date-jump';n.innerHTML='<button id=jumpRecent aria-label=跳到最新日期>↑</button><button id=jumpOlder aria-label=跳到较早日期>↓</button>';document.querySelector('.library-tools').append(n);n.querySelector('#jumpRecent').onclick=()=>document.querySelector('#gallery').scrollIntoView({behavior:"smooth",block:"start"});n.querySelector('#jumpOlder').onclick=()=>{const blocks=density>=3?document.querySelectorAll('.photo-grid .photo'):document.querySelectorAll('.day-heading');const target=density>=3?blocks[444]:blocks[blocks.length-1];target?.scrollIntoView({behavior:"smooth",block:"start"})};}
 $('#zoomLevels').innerHTML=levels.map((l,i)=>`<button data-density="${i}" class="${i===density?'active':''}" aria-pressed="${i===density}">${l.name}</button>`).join('');
}
function zoom(next){
 const tiles=$$('[data-photo]');
 const anchor=tiles.find(t=>t.getBoundingClientRect().bottom>0&&t.getBoundingClientRect().top<innerHeight);
 const id=anchor?.dataset.photo, offset=anchor?.getBoundingClientRect().top;
 density=Math.max(0,Math.min(4,next));applyDensity();
 if(id){requestAnimationFrame(()=>{const tile=document.querySelector(`[data-photo="${id}"]`);if(tile)window.scrollBy(0,tile.getBoundingClientRect().top-offset)});}
}
$('#zoomOut').onclick=()=>zoom(density+1);$('#zoomIn').onclick=()=>zoom(density-1);
$('#zoomLevels').onclick=e=>{const b=e.target.closest('[data-density]');if(b)zoom(+b.dataset.density)};
window.addEventListener('resize',applyDensity);
render();
