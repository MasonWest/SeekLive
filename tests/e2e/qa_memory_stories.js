/* Actual browser checks, synthetic images and synthetic names only. */
const {chromium}=require('playwright-core');
const {spawn}=require('child_process');
const fs=require('fs');
const assert=require('assert/strict');
const PY=process.env.PY || 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe';
async function fixture(){return new Promise((resolve,reject)=>{
 const child=spawn(PY,['-u','tests/fixture_albums_server.py'],{windowsHide:true,env:{...process.env,SEEKLIVE_STATE_PREFIX:'test_memories_'}});
 let text='';const timer=setTimeout(()=>{child.kill();reject(Error('Fixture timeout'))},20000);
 child.stdout.on('data',chunk=>{text+=chunk;const match=text.match(/READY (\S+)/);if(match){clearTimeout(timer);resolve({child,base:match[1]})}});
 child.stderr.on('data',chunk=>text+=chunk);child.on('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+': '+text))});
})}
(async()=>{
 let fx,browser;
 try{
  fx=await fixture();browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const ctx=await browser.newContext({viewport:{width:390,height:844}});
  assert.equal((await ctx.request.post(fx.base+'/api/admin/login',{headers:{'X-SL-Admin':'1'},data:{token:'test-pw-123'}})).status(),200);
  const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const openOrganization=async()=>{const section=page.locator('.memory-organize');if(!await section.evaluate(el=>el.open))await section.locator('summary').click();};
  await page.goto(fx.base+'/?view=memories');await page.locator('#splash.hide').waitFor();
  await page.locator('[data-story="fixture-story-ink"]').waitFor();assert.equal(await page.locator('.story-card').count(),3);
  await page.waitForFunction(()=>[...document.querySelectorAll('.story-card img')].some(img=>img.complete&&img.naturalWidth>0));
  fs.mkdirSync('tests/e2e/shots',{recursive:true});await page.screenshot({path:'tests/e2e/shots/memory-samples-mobile.png'});
  for(const theme of ['ink','amber','sage']){
   await page.locator('[data-story="fixture-story-'+theme+'"]').click();await page.locator('.memory-frame-ready').waitFor();
   assert.match(await page.locator('.memory-title-layout').innerText(),/2024 · 10 · 02/);
   assert.match(await page.locator('.memory-title-layout').innerText(),/Alice/);
   assert.equal(await page.locator('#memoryPlayer').isVisible(),true);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.waitForFunction(()=>+getComputedStyle(document.querySelector('.memory-frame-ready')).opacity>.99);
   await page.screenshot({path:'tests/e2e/shots/memory-opening-'+theme+'.png'});
   await page.locator('#memoryPlay').click();
   await page.waitForFunction(()=>memorySession?.music?.context.state==='running');
   assert.equal(await page.evaluate(()=>memorySession.playing),true);
   await page.locator('#memorySound').click();assert.equal(await page.evaluate(()=>memorySession.muted),true);
   await page.locator('#memoryPlay').click();assert.equal(await page.evaluate(()=>memorySession.playing),false);
   await page.locator('#memoryNext').click();await page.waitForFunction(()=>memorySession.index===1&&memorySession.ready);
   assert.equal(await page.locator('.memory-photo').last().isVisible(),true);
   await page.locator('#memoryPlay').click();await page.waitForFunction(()=>memorySession.playing);
   await page.keyboard.press('Escape');assert.equal(await page.locator('#memoryPlayer').isVisible(),false);
   assert.equal(await page.evaluate(()=>memorySession),null);
  }
  // Rapid close invalidates a pending manifest read.
  await page.evaluate(()=>{openMemory('fixture-story-ink');closeMemory()});await page.waitForTimeout(250);
  assert.equal(await page.locator('#memoryPlayer').isVisible(),false);
  await page.setViewportSize({width:1365,height:900});await page.locator('[data-story="fixture-story-ink"]').click();await page.locator('.memory-frame-ready').waitFor();
  await page.waitForFunction(()=>+getComputedStyle(document.querySelector('.memory-frame-ready')).opacity>.99);
  await page.screenshot({path:'tests/e2e/shots/memory-opening-desktop.png'});await page.locator('#memoryClose').click();
  // Device selection and complete playback cross the 120-photo boundary.
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-story="fixture-story-ink"]').click();await page.waitForFunction(()=>memorySession?.ready);
  assert.equal(await page.evaluate(()=>memorySession.layout),'portrait');
  await page.locator('#memoryNext').click();await page.waitForFunction(()=>memorySession.index===1&&memorySession.ready);
  assert(await page.evaluate(()=>{const img=memorySession.frame.querySelector('img');return img.naturalHeight>img.naturalWidth}));
  await page.locator('#memoryOptions').click();await page.locator('#memoryEditTitle').fill('测试之旅');await page.locator('#memoryEditPlace').fill('示例海边');
  await page.locator('#memoryEditTheme').selectOption('sage');assert.match(await page.locator('#memoryPlayer').getAttribute('class'),/story-sage/);
  await page.locator('#memoryEditLayout').selectOption('landscape');await page.locator('#memoryEditMode').selectOption('full');
  await page.locator('#memoryChooseCover').click();await page.locator('[data-memory-cover]').first().waitFor();await page.locator('[data-memory-cover]').nth(2).click();
  await page.locator('#memorySave').click();await page.waitForFunction(()=>memorySession?.story?.title==='测试之旅'&&memorySession.ready);
  assert.equal(await page.evaluate(()=>memorySession.layout),'landscape');assert.equal(await page.evaluate(()=>memorySession.mode),'full');
  assert(await page.evaluate(()=>memoryCount(memorySession)>120));assert.equal(await page.evaluate(()=>memorySession.story.photos.length),120);
  await page.evaluate(()=>moveMemory(121));await page.waitForFunction(()=>memorySession.index===121&&memorySession.ready);
  assert(await page.evaluate(()=>memorySession.pages.has(120)));
  await page.locator('#memoryAll').click();await page.locator('#storyAlbumGrid .tile:not(.sk)').first().waitFor();
  assert.match(await page.locator('#storyAlbumHeading').innerText(),/完整相册/);assert(await page.locator('#storyAlbumGrid .tile').count()<100);
  await page.locator('[data-story-full="fixture-story-ink"]').click();await page.waitForFunction(()=>memorySession?.ready);
  await page.locator('#memoryOptions').click();await openOrganization();await page.locator('#memoryMergeTarget').selectOption('fixture-story-amber');await page.locator('#memoryMerge').click();
  await page.waitForFunction(()=>memorySession?.id.startsWith('manual-')&&memorySession.ready);
  await page.locator('#memoryOptions').click();await openOrganization();await page.locator('#memoryUndoOrganize').click();await page.waitForFunction(()=>!memorySession);
  await page.locator('[data-story="fixture-story-ink"]').waitFor();assert.equal(await page.locator('.story-card').count(),3);
  await page.locator('[data-story="fixture-story-ink"]').click();await page.waitForFunction(()=>memorySession?.ready);
  await page.locator('#memoryOptions').click();await openOrganization();await page.locator('#memorySplit').click();await page.waitForFunction(()=>memorySession?.id.startsWith('manual-')&&memorySession.ready);
  await page.locator('#memoryOptions').click();await openOrganization();await page.locator('#memoryUndoOrganize').click();await page.waitForFunction(()=>!memorySession);
  await page.locator('[data-story="fixture-story-ink"]').waitFor();
  const saved=await (await ctx.request.get(fx.base+'/api/vision/story?lib=t&id=fixture-story-ink')).json();assert.equal(saved.title,'测试之旅');assert.equal(saved.theme,'sage');assert.equal(saved.place,'示例海边');
  assert.equal((await ctx.request.post(fx.base+'/api/vision/story/edit',{headers:{'X-SL-Admin':'1'},data:{lib:'t',id:'fixture-story-ink',revision:0,changes:{title:'Stale'}}})).status(),409);
  assert.deepEqual(errors,[]);
  console.log('PASS: title styles, device orientation, editable opening/cover, complete album, 120-photo paging, reversible merge/split, music, navigation and cancellation (synthetic fixture only)');
 }finally{if(browser)await browser.close();if(fx)fx.child.kill()}
})().catch(error=>{console.error(error);process.exitCode=1});
