// Viewport-first demo loading; this prototype never generates thumbnails.
(()=>{
 const pending=new Set(), active=new Set();let frame=0;
 const distance=img=>{const r=img.getBoundingClientRect();return Math.max(0,-r.bottom,r.top-innerHeight)};
 function cancel(img){pending.delete(img);if(active.delete(img)){img.onload=img.onerror=null;img.removeAttribute('src')}}
 function pump(){
   [...pending,...active].forEach(img=>{if(!img.isConnected||distance(img)>160)cancel(img)});
   const queue=[...pending].sort((a,b)=>distance(a)-distance(b));
   while(active.size<4&&queue.length){
     const img=queue.shift();pending.delete(img);active.add(img);
     img.onload=()=>{active.delete(img);img.onload=img.onerror=null;observer.unobserve(img);pump()};
     img.onerror=()=>{active.delete(img);img.onload=img.onerror=null;observer.unobserve(img);img.alt='加载失败，点击照片重试';pump()};
     img.src=img.dataset.source;
   }
 }
 const observer=new IntersectionObserver(entries=>{
   entries.forEach(e=>{if(e.isIntersecting){if(!active.has(e.target))pending.add(e.target)}else cancel(e.target)});pump();
 },{rootMargin:'160px 0px'});
 const scan=()=>{observer.disconnect();[...pending,...active].forEach(cancel);document.querySelectorAll('.photo img[data-source]').forEach(img=>observer.observe(img))};
 document.addEventListener('DOMContentLoaded',()=>{
   const gallery=document.querySelector('#gallery');
   new MutationObserver(scan).observe(gallery,{childList:true});scan();
 });
 function refresh(){if(!frame)frame=requestAnimationFrame(()=>{frame=0;pump()})}
 addEventListener('scroll',refresh,{passive:true});addEventListener('resize',refresh);
 window.prototypeImageQueue=()=>({waiting:pending.size,active:active.size});
})();
