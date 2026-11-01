import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const src=fs.readFileSync('web/app.js','utf8');
const fn=name=>src.match(new RegExp(`function ${name}\\([^]*?\\n\\}`))[0];
const image=(name,top)=>({name,isConnected:true,dataset:{thumbUrl:name},top,
 getBoundingClientRect(){return {top:this.top,bottom:this.top+80}},removeAttribute(){this.cancelled=true}});
const old=image('old',5000),near=image('near',900),visible=image('visible',100);
const queue=[old,near,visible],live=new Set(),waiting=new Set(queue),started=[];
const ctx=vm.createContext({window:{innerHeight:800},THUMB_AHEAD:200,THUMB_MAX:1,thumbHeld:false,
 thumbQueue:queue,thumbLive:live,thumbWaiting:waiting,thumbAdvance:()=>{},thumbStart:img=>{live.add(img);started.push(img.name)}});
vm.runInContext(['thumbDistance','thumbDefer','thumbPump','thumbReprioritize'].map(fn).join('\n'),ctx);
ctx.thumbReprioritize();assert.deepEqual(started,['visible']);assert(!queue.includes(old));
visible.top=5000;ctx.thumbReprioritize();assert(visible.cancelled);assert.deepEqual(started,['visible','near']);
assert(live.size<=1);assert(!waiting.has(old));
// Exhaustion must skip obsolete offscreen work even without a scroll callback.
live.clear();queue.push(old);waiting.add(old);ctx.thumbPump();assert(!started.includes('old'));
console.log('PASS: viewport priority, offscreen cancellation, stale queue rejection, concurrency cap');
const a=image('visible',100),b=image('offscreen',4000),requests=[];
const context=vm.createContext({window:{innerHeight:800},THUMB_AHEAD:200,
 thumbQueue:[],thumbLive:new Set([a,b]),thumbWaiting:new Set(),
 api:(_path,params)=>{requests.push(params);return Promise.resolve({ok:true})}});
vm.runInContext("let thumbEpoch=0; const thumbViewId='test-browser-tab-123';\n"+
 ['thumbDistance','thumbDefer','thumbAdvance','thumbRequestURL'].map(fn).join('\n'),context);
context.thumbAdvance();
assert(a.cancelled&&b.cancelled);
assert.equal(context.thumbLive.size,0);
assert.deepEqual(context.thumbQueue.map(img=>img.name),['visible']);
assert.equal(requests[0]._slepoch,1);
assert(context.thumbRequestURL('/thumb?p=a').endsWith('_slepoch=1'));
console.log('PASS: viewport epoch announces cancellation and restamps visible retries');
let pumps=0;
context.thumbPump=()=>pumps++;
vm.runInContext('let thumbHeld=false;\n'+fn('thumbHold'),context);
const pendingImage=image('pending',100);
context.thumbLive.add(pendingImage);
const beforeNotices=requests.length;
context.thumbHold(true);
assert(pendingImage.cancelled);
assert.equal(requests.length,beforeNotices+1);
assert.equal(context.thumbLive.size,0);
assert.equal(pumps,0);
context.thumbHold(false);
assert.equal(pumps,1);
console.log('PASS: opening viewer also supersedes server waiters; closing resumes thumbnails');
{
  const img=image('still-visible',100),observed=new Set([img]);let freshEntries=0;
  const ctx=vm.createContext({state:{search:{active:false},days:[{ym:'2026-09'}],renderedDayIdx:1,done:false},
    thumbQueue:[img],thumbLive:new Set(),thumbAdvance(){},thumbDefer(){},
    thumbObserver:{unobserve(i){observed.delete(i)},observe(i){if(!observed.has(i))freshEntries++;observed.add(i)}},
    requestAnimationFrame(){},thumbReprioritize(){},focusRail(){},pinMonth(){}});
  vm.runInContext(fn('jumpToMonth'),ctx);ctx.jumpToMonth('2026-09');
  assert.equal(freshEntries,1);
  console.log('PASS: month jumps rearm visible cancelled images even without an intersection change');
}
