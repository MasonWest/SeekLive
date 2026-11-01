import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const src=fs.readFileSync('web/app.js','utf8');
const fn=name=>src.match(new RegExp(`(?:async )?function ${name}\\([^\\n]*\\) \\{[^]*?\\n\\}`))[0];
const install=(ctx,names)=>vm.runInContext(names.map(fn).join('\n'),ctx);
const tree={months:[{ym:'2026-09',n:601,days:[{d:'30',n:1},{d:'29',n:350},{d:'28',n:250}]}]};
{
  let saved;
  const ctx=vm.createContext({state:{switching:true,density:1},DENSITY_LEVELS:Array(5),computeCols(){},saveDensity(i){saved=i},syncDenUI(){}});
  install(ctx,['setDensity']);ctx.setDensity(4);
  assert.equal(saved,4);assert.equal(ctx.state.density,4);
  console.log('PASS: choosing a view during library loading defers rebuilding instead of cancelling the tree request');
}
{
  let renders=0;
  const ctx=vm.createContext({state:{lib:'t',tree:null,search:{active:false}},wallMode:()=> 'year',renderYearOverview(){renders++}});
  install(ctx,['renderNextBatch']);ctx.renderNextBatch();assert.equal(renders,0);
  ctx.state.tree=tree;ctx.renderNextBatch();assert.equal(renders,1);
  console.log('PASS: saved year mode cannot render before bootstrap provides the tree');
}
{
  const ctx=vm.createContext({});install(ctx,['monthDayOffset','convertWallAnchor']);
  for(let index=0;index<601;index++){
    const day=ctx.convertWallAnchor({key:'2026-09',index,tileRel:-12},'day',tree);
    const back=ctx.convertWallAnchor(day,'month',tree);
    assert.equal(back.index,index);assert.equal(back.key,'2026-09');assert.equal(back.tileRel,-12);
  }
  const year=ctx.convertWallAnchor({key:'2026-09|29',index:130,rel:-5000},'year',tree);
  assert.equal(year.rel,0);assert.equal(year.index,undefined);
  assert.equal(ctx.convertWallAnchor({key:'2026-09'},'day',tree).tileRel,0);
  console.log('PASS: day/month anchor round trip for all 601 photos; year/header anchors reset safely');
}
{
  const calls=[];
  const ctx=vm.createContext({state:{lib:'t',viewEpoch:1},PAGE_SIZE:120,browseController:new AbortController(),
    abortError:()=>Error('aborted'),cachedPage:()=>null,browseSlot:async()=>{},releaseBrowseSlot(){},rememberPage(){},
    api:async(path,params)=>{calls.push({path,params});return {total:601,items:[]}}});
  install(ctx,['fetchBrowsePage']);
  await ctx.fetchBrowsePage({lib:'t',epoch:1,key:'2026-09',ym:'2026-09'},120);
  assert.equal(calls[0].path,'/api/items');assert.equal(calls[0].params.off,120);
  assert(!Object.hasOwn(calls[0].params,'d'));
  console.log('PASS: continuous month uses monthly pagination without an undefined date filter');
}
{
  const elements=[],groups=[];
  const element=()=>({dataset:{},children:[],appendChild(el){this.children.push(el)},innerHTML:'',className:''});
  const timeline=element();
  const days=Array.from({length:90},(_,i)=>({ym:i<30?'2026-10':i<60?'2026-09':'2026-08'}));
  const months=['2026-10','2026-09','2026-08'].map(ym=>({ym,n:60,days:Array.from({length:30},()=>({n:2}))}));
  const state={renderedDayIdx:0,days,tree:{months}};
  const ctx=vm.createContext({state,document:{createDocumentFragment:element,createElement(){const e=element();elements.push(e);return e}},
    $:()=>timeline,groupObserver:{observe(e){groups.push(e)}},sizeSkeletons(){},fmtMonthLabel:ym=>ym,countOfMonth:()=>60,
    requestAnimationFrame(){},nearBottom:()=>false});
  install(ctx,['renderMonthBatch']);ctx.renderMonthBatch();
  assert.equal(groups.length,3);assert.equal(state.renderedDayIdx,90);assert(state.done);
  assert.deepEqual(groups.map(g=>g.dataset.total),[60,60,60]);
  assert.equal(elements.filter(e=>e.className==='day-blk month-wall').length,3);
  console.log('PASS: 90 sparse dates produce three continuous month grids, without duplicating photos');
}
{
  let resolve,painted=0;
  const card={isConnected:true,dataset:{ym:'2026-09'},querySelector(){return {replaceChildren(){painted++},dataset:{}}}};
  const ctx=vm.createContext({state:{lib:'t',viewEpoch:1},AbortController,browseController:new AbortController(),
    fetchBrowsePage:()=>new Promise(r=>resolve=r)});
  install(ctx,['loadYearCard']);const job=ctx.loadYearCard(card);card._job.abort();
  resolve({items:[{p:'old.jpg'}]});await job;assert.equal(painted,0);assert.equal(card._job,null);
  console.log('PASS: cancelled year covers cannot repaint obsolete cards');
}
