// Live gallery state transitions, using synthetic records and no real photos.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{innerHTML:'',textContent:''});return nodes.get(id)};
const calls=[];const listeners={};const checkboxes=[{dataset:{formalSelect:"N"},checked:false}];
let archived=false;
const summary=()=>({progress:{analysed:5499,media:5499},detections:{person:3592,cat:10},confirmed:archived?2:1,pending:archived?0:1,entities:[{id:'P',name:'Example',kind:'person',status:'followed',confirmed:archived?2:1,pending:archived?0:1}]});
const context=vm.createContext({URLSearchParams,Set,console,
 document:{querySelectorAll:selector=>selector.includes('data-formal-select')?checkboxes:[],addEventListener(type,handler){listeners[type]=handler}},
 $:id=>node(id),esc:s=>String(s),D:{summary:{decoded:521}},
 liveDatabase:{formal:true,token:'synthetic'},current:'summary',focusedPerson:'P',animalView:'followed',animalActive:'PET',
 databaseRevision:1,databaseSaving:false,databasePending:null,databaseConflict:false,
 labels:{},personNormalize(){},animalNormalize(){},offlineSave(){},render(){},alert:message=>{throw Error(message)},
 fetch:async(path,options)=>{
  calls.push({path,options});let data;
  if(path==='/api/vision/summary')data=summary();
  else if(path.startsWith('/api/vision/photos'))data={name:'Example',total:1,items:[{id:'N',captured:'2025',score:archived?null:.9}]};
  else {assert.equal(path,'/api/vision/decision');assert.equal(JSON.parse(options.body).revision,1);archived=true;data={revision:2,state:{from:'SQLite'},changed:1}}
  return {ok:true,json:async()=>data};
 }
});
vm.runInContext(fs.readFileSync('prototypes/vision-lab/formal_review.js','utf8'),context);
const run=code=>vm.runInContext(code,context);
await new Promise(setImmediate);
assert.match(node('#content').innerHTML,/5499/);
assert.match(node('#content').innerHTML,/最初原型样本/);
await run("current='faces';formalEntity='P';loadFormalPhotos()");
assert.match(node('#formalPhotos').innerHTML,/新增待确认 1/);
assert.match(node('#formalPhotos').innerHTML,/确认所选并归档/);
const beforeCalls=calls.length, beforeHtml=node('#formalPhotos').innerHTML;
const clickAction=action=>listeners.click({target:{closest:selector=>selector==='[data-formal-action]'?{dataset:{formalAction:action}}:null}});
clickAction('select-page');
assert.equal(run('formalSelected.size'),1);assert.equal(checkboxes[0].checked,true);
assert.equal(node('#formalSelectedCount').textContent,'已选 1 张');
clickAction('clear');
assert.equal(run('formalSelected.size'),0);assert.equal(checkboxes[0].checked,false);
assert.equal(calls.length,beforeCalls);assert.equal(node('#formalPhotos').innerHTML,beforeHtml);
await run("formalSelected.add('N');submitFormal('confirm')");
assert.equal(run('databaseRevision'),2);
assert.equal(run('labels.from'),'SQLite');
assert.equal(run('formalMode'),'confirmed');
assert.equal(run('formalSelected.size'),0);
assert.match(node('#formalPhotos').innerHTML,/已归档 2/);
assert.equal(calls.filter(c=>c.options.method==='PUT').length,1);
assert.ok(calls.every(c=>c.options.headers['X-Vision-Token']==='synthetic'));
console.log('PASS: live counts, paginated identity photos, batch archive and immediate archived count (synthetic only)');
