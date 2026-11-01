import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync('web/app.js', 'utf8');
const fn = name => {
  const match = source.match(new RegExp(`(?:async )?function ${name}\\([^\\n]*\\) \\{[^]*?\\n\\}`));
  assert(match, name); return match[0];
};
const install = (ctx, names) => vm.runInContext(names.map(fn).join('\n'), ctx);
const tick = () => new Promise(resolve => setImmediate(resolve));
let checks = 0;
function check(name, run) { run(); checks++; console.log('PASS:', name); }
{
  const ctx = vm.createContext({}); install(ctx, ['rowWindow']);
  const seen = new Set(); let peak = 0;
  for (let row = 0; row < 601; row++) {
    const w = ctx.rowWindow(2401, 4, 90, 4, -row * 94, 844);
    peak = Math.max(peak, w.end - w.start);
    for (let i = w.start; i < w.end; i++) seen.add(i);
    assert.equal(w.height, 601 * 94 - 4);
  }
  check('2401 photos remain reachable while mounted rows stay bounded', () => {
    assert.equal(seen.size, 2401); assert(peak < 90);
    assert(ctx.rowWindow(100000, 4, 90, 4, -10000, 844).end - ctx.rowWindow(100000, 4, 90, 4, -10000, 844).start < 90);
    assert.equal(ctx.rowWindow(2401, 4, 90, 4, 1000, 844).end, 12);
  });
}
{
  const context = {lib:'t', epoch:1, key:'day'};
  const ctx = vm.createContext({state:{lib:'t',viewEpoch:1},pageCache:new Map(),PAGE_CACHE_LIMIT:32});
  install(ctx,['pageKey','rememberPage','cachedPage','tileItemOf']);
  const selected = {p:'selected.jpg'};
  for (let i=0;i<40;i++) ctx.rememberPage(context,i*120,{items:[{p:String(i)}]});
  check('LRU evicts old pages; tile selection does not depend on evicted data', () => {
    assert.equal(ctx.pageCache.size,32); assert.equal(ctx.cachedPage(context,0),undefined);
    assert.equal(ctx.tileItemOf({item:selected}),selected);
    ctx.cachedPage(context,8*120);ctx.rememberPage(context,999,{items:[]});
    assert(ctx.cachedPage(context,8*120));assert.equal(ctx.cachedPage(context,9*120),undefined);
    ctx.rememberPage({...context,epoch:0},1,{items:[]});assert.equal(ctx.pageCache.size,32);
  });
}
{
  const pending = [];
  const ctx = vm.createContext({DOMException,AbortController,PAGE_SIZE:120,PAGE_CACHE_LIMIT:32,BROWSE_CONCURRENCY:2,
    state:{lib:'t',viewEpoch:1},pageCache:new Map(),browseController:new AbortController(),browseWaiters:new Set(),
    api:(_path,_params,signal)=>new Promise((resolve,reject)=>{
      pending.push({resolve,signal}); signal.addEventListener('abort',()=>reject(new DOMException('cancelled','AbortError')),{once:true});
    })});
  vm.runInContext('let browseActive=0;',ctx);
  install(ctx,['abortError','pageKey','rememberPage','cachedPage','browseSlot','releaseBrowseSlot','fetchBrowsePage']);
  const context={lib:'t',epoch:1,key:'day'};
  const controllers=Array.from({length:6},()=>new AbortController());
  const jobs=controllers.map((c,i)=>ctx.fetchBrowsePage(context,i*120,c.signal).catch(e=>e.name));
  await tick();assert.equal(pending.length,2);
  controllers[2].abort();controllers[3].abort();
  pending[0].resolve({total:1000,items:[{p:'a'}]}); await tick();
  assert.equal(pending.length,3); assert.equal(pending[2].signal,controllers[4].signal);
  controllers[1].abort();await tick();assert.equal(pending.length,4);
  // Simulate a late response after a library/query epoch changed.
  ctx.state.viewEpoch=2;pending[2].resolve({items:[{p:'stale'}]});pending[3].resolve({items:[{p:'stale'}]});
  await Promise.all(jobs);
  check('concurrency cap, queued cancellation, slot transfer and stale response rejection',()=>{
    assert.equal(vm.runInContext('browseActive',ctx),0);assert.equal(ctx.browseWaiters.size,0);
    assert.equal(ctx.pageCache.size,1);
  });
}
{
  const context={lib:'t',epoch:1,key:'day',ym:'2024-01',d:'01'};
  let resolvePage;const paints=[];let call=0;
  const grid={isConnected:true,getBoundingClientRect:()=>({top:0}),style:{},classList:{add(){}},querySelectorAll:()=>[],_range:'',children:[]};
  const ctx=vm.createContext({AbortController,browseController:new AbortController(),PAGE_SIZE:120,
    state:{lib:'t',viewEpoch:1,cols:4},gridGeometry:()=>({start:0,end:241,cell:90,gap:4,height:10000}),
    paintWindow:()=>paints.push(call),fetchBrowsePage:async()=>{call++;if(call===2)await new Promise(r=>resolvePage=r);return {items:[]}},
    document:{createElement(){throw Error('unexpected retry')}},releaseGrid(){}});
  install(ctx,['loadGridWindow']);
  const job=ctx.loadGridWindow(grid,context,2401);await tick();
  check('first page paints before subsequent pages finish',()=>{assert.equal(call,2);assert.equal(paints.length,2)});
  grid._job.abort();resolvePage();await job;
  check('cancelled window cannot paint later pages',()=>{assert.equal(call,2);assert.equal(paints.length,2)});
}
{
  let resolve;let fallback=0,players=0;
  const item={p:'live.jpg'},other={p:'other.jpg'};
  const ctx=vm.createContext({IS_IOS:true,liveMuted:true,viewerRevision:1,viewer:{hidden:false},
    currentItem:()=>item,ensureLPK:()=>new Promise(r=>resolve=r),fallbackMotion:()=>fallback++,
    document:{createElement:()=>({style:{}})},vMedia:{appendChild:()=>players++},window:{LivePhotosKit:{Player(){return {play(){}}}}},
    state:{viewer:{}},mediaURL:()=>'',canPlayHevc:()=>true});
  install(ctx,['viewerCurrent','attachLive']);
  ctx.attachLive(item);ctx.viewerRevision=2;resolve(false);await tick();
  check('obsolete Live Photo initialization cannot fall back into the new slide',()=>{assert.equal(fallback,0);assert.equal(players,0)});
  ctx.attachLive(item);ctx.viewer.hidden=true;resolve(true);await tick();
  check('closing viewer invalidates pending Live Photo initialization',()=>{assert.equal(players,0)});
}
{
  const pending=[],rendered=[],controller=new AbortController();
  const ctx=vm.createContext({AbortController,PAGE_SIZE:120,browseController:controller,
    state:{viewer:{context:{total:2401},offset:0,i:119}},viewer:{hidden:false},viewerRevision:0,viewerRequest:null,
    vMedia:{innerHTML:'',querySelector:()=>({})},$:()=>({}),
    stopMedia(){ctx.viewerRevision++;ctx.viewerRequest?.abort()},
    fetchBrowsePage:(_c,off,signal)=>new Promise(resolve=>pending.push({off,signal,resolve})),
    renderSlide(index){rendered.push(index);ctx.state.viewer.i=index;},currentItem:()=>null});
  install(ctx,['viewerCurrent','showSlide']);
  const first=ctx.showSlide(120,1);const newer=ctx.showSlide(121,1);
  assert(pending[0].signal.aborted);assert.equal(pending[1].off,120);
  pending[1].resolve({items:[{p:'120'},{p:'121'}]});await newer;
  pending[0].resolve({items:[{p:'old'}]});await first;
  check('cross-page viewer navigation commits only the latest response',()=>{
    assert.deepEqual(rendered,[1]);assert.equal(ctx.state.viewer.offset,120);
  });
}
{
  const grid={getBoundingClientRect:()=>({top:100-ctx.window.scrollY})};
  const day={dataset:{key:'2024-10|01',total:'2401'},querySelector:()=>grid,
    getBoundingClientRect:()=>({top:50-ctx.window.scrollY,bottom:100000-ctx.window.scrollY})};
  const ctx=vm.createContext({state:{lib:'t',viewEpoch:1,cols:4,renderedDayIdx:1,done:false,days:[{key:'2024-10|01'}]},
    window:{scrollY:1000,scrollTo({top}){this.scrollY=top}},pinToken:0,
    $$:()=>[day],$:()=>day,topbarOffset:()=>100,
    gridGeometry:()=>({cell:ctx.state.cols===4?90:45,gap:4}),
    requestAnimationFrame(){},scheduleWindows(){},syncRail(){},renderNextBatch(){},wallMode:()=> 'day'});
  install(ctx,['captureViewAnchor','restoreViewAnchor']);
  const anchor=ctx.captureViewAnchor();assert.equal(anchor.index,40);
  ctx.state.cols=8;ctx.restoreViewAnchor(anchor);
  check('density changes retain the same photo row and pixel offset',()=>{
    assert.equal(ctx.window.scrollY,305);
    const newRowTop=100-ctx.window.scrollY+Math.floor(anchor.index/8)*49;
    assert.equal(newRowTop-100,anchor.tileRel);
  });
}
{
  let attempts=0,retry;
  const grid={isConnected:true,getBoundingClientRect:()=>({top:0}),style:{},classList:{add(){}},querySelectorAll:()=>[],appendChild(el){retry=el}};
  const context={lib:'t',epoch:1,key:'day'};
  const ctx=vm.createContext({AbortController,browseController:new AbortController(),PAGE_SIZE:120,
    state:{lib:'t',viewEpoch:1,cols:4},gridGeometry:()=>({start:0,end:4,cell:90,gap:4,height:1000}),
    paintWindow(){},releaseGrid(){},document:{createElement:()=>({style:{}})},
    fetchBrowsePage:async()=>{if(++attempts===1)throw Error('synthetic failure');return {items:[]}}});
  install(ctx,['loadGridWindow']);
  await ctx.loadGridWindow(grid,context,2401);
  assert(retry);await retry.onclick();
  check('a failed page exposes an explicit retry and can recover',()=>{assert.equal(attempts,2);assert.equal(grid._job,null)});
}
{
  let resolve;
  const state={lib:'t',viewEpoch:1},sel={active:true,revision:1,map:new Map()};
  const el={dataset:{ym:'2024-10',d:'01',total:'2401'},querySelector:()=>({})};
  const ctx=vm.createContext({state,sel,browseController:new AbortController(),
    api:()=>new Promise(r=>resolve=r),syncDayPicks(){},toast(){throw Error('stale operation emitted a toast')},
    $$:()=>[],syncSelBar(){},fmtDayHead:()=>''});
  install(ctx,['pickDay']);
  const job=ctx.pickDay(el,true);state.lib='u';state.viewEpoch++;
  resolve({items:[{p:'old.jpg'}],total:1});await job;
  check('old whole-day selection cannot populate a newly selected library',()=>{assert.equal(sel.map.size,0)});
}
{
  const requests=[];
  const ctx=vm.createContext({DOMException,AbortController,PAGE_SIZE:120,PAGE_CACHE_LIMIT:32,BROWSE_CONCURRENCY:2,
    state:{lib:'t',viewEpoch:1},pageCache:new Map(),browseController:new AbortController(),browseWaiters:new Set(),
    api:async(path,params)=>{requests.push({path,params});return {total:500,items:[]}}});
  vm.runInContext('let browseActive=0;',ctx);
  install(ctx,['abortError','pageKey','rememberPage','cachedPage','browseSlot','releaseBrowseSlot','fetchBrowsePage']);
  const context={lib:'t',epoch:1,key:'album:P,Q',album:{ids:'P,Q',ym:'2024-01',memory:''}};
  await ctx.fetchBrowsePage(context,120);
  await ctx.fetchBrowsePage({...context,key:'album:P',album:{ids:'P',ym:'',memory:''}},120);
  check('album pagination preserves joint/month filters and separate page caches',()=>{
    assert.equal(requests.length,2);assert.equal(requests[0].path,'/api/vision/album');
    assert.equal(requests[0].params.ids,'P,Q');assert.equal(requests[0].params.ym,'2024-01');
    assert.equal(requests[0].params.off,120);assert.equal(requests[1].params.ids,'P');
  });
}
console.log(`Browsing behavior checks: ${checks} passed`);
