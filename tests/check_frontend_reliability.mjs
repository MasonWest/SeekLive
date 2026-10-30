import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync('web/app.js', 'utf8');
const extract = name => source.match(new RegExp(`async function ${name}\\([^]*?\\n\\}`))[0];
const results = {};
{
  let calls = 0, painted = 0;
  const state = { lib: 't', viewEpoch:0, items: new Map() };
  const ctx = vm.createContext({state, api: async (_p,args) => {calls++; return {total: 601, items: Array.from({length:Math.min(600,601-args.off)}, (_, i) => ({p: `${args.off+i}.jpg`}))};}, paintGrid: (_g, items) => {painted = items.length;}});
  vm.runInContext(extract('loadDay'), ctx);
  await ctx.loadDay({dataset:{ym:'2024-01', d:'01',key:'2024-01|01'},querySelector:()=>({})});
  results.daily_paging = {total:601, painted, calls};
}
{
  const pending = [], calls = [];
  const state = {lib:'t', viewEpoch:0, search:{q:'IMG',off:0,done:false}, searchList:[]};
  const grid = {};
  const ctx = vm.createContext({state, $: s => s === '#searchGrid' ? grid : {}, api: async (_p, args) => {calls.push({...args}); return await new Promise(r => pending.push(r));}, paintSearch:()=>{},toast:()=>{}});
  vm.runInContext(extract('loadSearchPage'), ctx);
  const a = ctx.loadSearchPage(); const b = ctx.loadSearchPage();
  pending[0]({total:240,items:Array.from({length:120},(_,i)=>({p:`${i}.jpg`}))});
  assert.equal(pending.length,1);
  await Promise.all([a,b]);
  results.search_overlap = {offsets: calls.map(c=>c.off), offset_after: state.search.off, loaded: state.searchList.length, unique: new Set(state.searchList.map(i=>i.p)).size};
}
{
  const pending = {};
  const state = {lib:null,viewEpoch:0,libs:[{id:'a',name:'A'},{id:'b',name:'B'}],items:new Map(),search:{},days:[]};
  const element = () => ({hidden:false, innerHTML:'',textContent:'',classList:{add(){},toggle(){}}});
  const elements = new Map();
  const dollar = s => {if(!elements.has(s)) elements.set(s,element()); return elements.get(s);};
  const ctx = vm.createContext({state, $:dollar,$$:()=>[],rail:element(),document:{title:''},window:{scrollTo(){}},
    api:async (_p,{lib})=>await new Promise(r=>pending[lib]=r),thumbReset(){},syncRailHeight(){},hideToTop(){},syncTopbarHeight(){},
    buildMonthSheet(){},resetRail(){},syncDen(){},renderNextBatch(){},requestAnimationFrame(){},syncRail(){},toast(){},IS_SHARE:()=>false});
  vm.runInContext(extract('selectLib'),ctx);
  const a=ctx.selectLib('a'); const b=ctx.selectLib('b');
  pending.b({lib:{id:'b'},dayOrder:['2024-02|01'],months:[]}); await b;
  pending.a({lib:{id:'a'},dayOrder:['2024-01|01'],months:[]}); await a;
  results.library_switch_race = {selected_library:state.lib,displayed_tree_library:state.tree.lib.id,displayed_day:state.days[0].key};
}
assert.equal(results.daily_paging.painted,601);
assert.equal(results.daily_paging.calls,2);
assert.deepEqual(results.search_overlap.offsets,[0]);
assert.equal(results.search_overlap.offset_after,120);
assert.equal(results.library_switch_race.displayed_tree_library,'b');
console.log(JSON.stringify(results,null,2));
