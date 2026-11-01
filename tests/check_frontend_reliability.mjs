import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import './check_browsing.mjs';
const source = fs.readFileSync('web/app.js', 'utf8');
const extract = name => source.match(new RegExp(`async function ${name}\\([^]*?\\n\\}`))[0];
{
  const pending = [], calls = [];
  const state = {lib:'t', viewEpoch:0, search:{q:'IMG',done:false}};
  const grid = {}, heading = {};
  const ctx = vm.createContext({state, $: s => s === '#searchGrid' ? grid : heading,
    fetchBrowsePage: async (_context, off) => {calls.push(off); return await new Promise(r => pending.push(r));},
    loadGridWindow:()=>{},toast:()=>{},groupObserver:{observe(){}},nearbyGrids:new Set()});
  vm.runInContext(extract('loadSearchPage'), ctx);
  const a = ctx.loadSearchPage(), b = ctx.loadSearchPage();
  assert.equal(pending.length,1);
  pending[0]({total:240,items:[{p:'a'}]});
  await Promise.all([a,b]);
  assert.deepEqual(calls,[0]);assert.equal(state.search.total,240);assert(state.search.done);
  state.search.done=false;
  const old=ctx.loadSearchPage();state.search.q='NEW';
  pending[1]({total:999,items:[]});await old;
  assert.equal(state.search.total,240);
  console.log('PASS: search request mutex and stale query rejection');
}
{
  const pending = {};
  const state = {lib:null,viewEpoch:0,libs:[{id:'a',name:'A'},{id:'b',name:'B'}],search:{},days:[],viewer:{}};
  const element = () => ({hidden:false,innerHTML:'',textContent:'',classList:{add(){},toggle(){}}});
  const elements = new Map();
  const dollar = s => {if(!elements.has(s)) elements.set(s,element()); return elements.get(s);};
  const ctx = vm.createContext({state,$:dollar,$$:()=>[],rail:element(),viewer:{hidden:true},sel:{active:false},
    searchTimer:null,clearTimeout(){},resetBrowseRequests(){},resetCollection(){},browseController:new AbortController(),
    document:{title:''},window:{scrollTo(){}},
    api:async (_p,{lib})=>await new Promise(r=>pending[lib]=r),thumbReset(){},syncRailHeight(){},hideToTop(){},syncTopbarHeight(){},
    buildMonthSheet(){},resetRail(){},syncDen(){},renderNextBatch(){},requestAnimationFrame(){},syncRail(){},toast(){},IS_SHARE:()=>false});
  vm.runInContext(extract('selectLib'),ctx);
  const a=ctx.selectLib('a'),b=ctx.selectLib('b');
  pending.b({lib:{id:'b'},dayOrder:['2024-02|01'],months:[]});await b;
  pending.a({lib:{id:'a'},dayOrder:['2024-01|01'],months:[]});await a;
  assert.equal(state.lib,'b');assert.equal(state.tree.lib.id,'b');
  assert.equal(state.days[0].key,'2024-02|01');
  console.log('PASS: late library response cannot overwrite the selected library');
}
