/* Real-browser albums validation, entirely within a synthetic fixture. */
const {chromium}=require('playwright-core');
const {spawn}=require('child_process');
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const PY=process.env.PY || 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe';
const EXE=process.env.EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
async function fixture(){
 return new Promise((resolve,reject)=>{
  const child=spawn(PY,['-u','tests/fixture_albums_server.py'],{windowsHide:true,env:{...process.env,SEEKLIVE_STATE_PREFIX:'test_albums_'}});
  let output='';const timer=setTimeout(()=>{child.kill();reject(Error('Fixture timeout: '+output))},20000);
  child.stdout.on('data',chunk=>{output+=chunk;const ready=output.match(/READY (\S+)/);if(ready){clearTimeout(timer);resolve({child,base:ready[1]})}});
  child.stderr.on('data',chunk=>output+=chunk);
  child.on('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+': '+output))});
 });
}
(async()=>{
 let fx,browser;
 try{
  fx=await fixture();browser=await chromium.launch({executablePath:EXE,headless:true});
  const context=await browser.newContext({viewport:{width:1365,height:900}});
  const login=await context.request.post(fx.base+'/api/admin/login',{headers:{'X-SL-Admin':'1'},data:{token:'test-pw-123'}});assert.equal(login.status(),200);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(fx.base);await page.locator('#splash.hide').waitFor();
  await page.locator('#collectionNav [data-collection="people"]').click();
  await page.locator('[data-album="P"]').waitFor();assert.equal(await page.locator('.collection-card').count(),2);
  await page.waitForFunction(()=>[...document.querySelectorAll('.collection-card img')].every(i=>i.complete&&i.naturalWidth>0));
  fs.mkdirSync('tests/e2e/shots',{recursive:true});await page.screenshot({path:'tests/e2e/shots/albums-desktop.png'});
  await page.locator('#collectionFind').fill('Alice');assert.equal(await page.locator('.collection-card:visible').count(),1);
  await page.locator('[data-album="P"]').click();await page.locator('#albumGrid .tile:not(.sk)').first().waitFor();
  assert.match(await page.locator('#albumHeading').innerText(),/2 张照片/);
  async function checkAlbumDensity() {
    while(!(await page.locator('#denIn').isDisabled()))await page.locator('#denIn').click();
    const before=await page.locator('#albumGrid .tile').first().evaluate(el=>el.getBoundingClientRect().width);
    await page.locator('#denOut').click();
    await page.waitForFunction(width=>document.querySelector('#albumGrid .tile')?.getBoundingClientRect().width<width,before);
    assert(await page.evaluate(()=>nearbyGrids.has(document.querySelector('#albumGrid'))));
    assert(await page.locator('#albumGrid .tile').count()>0);
  }
  await checkAlbumDensity();
  await page.locator('[data-growth="P"]').click();await page.locator('.growth-stage').first().waitFor();
  assert((await page.locator('.growth-stage').count())>=1);
  await page.waitForFunction(()=>[...document.querySelectorAll('.growth-hero img')].every(i=>i.complete&&i.naturalWidth>0));
  await page.screenshot({path:'tests/e2e/shots/growth-desktop.png'});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'tests/e2e/shots/growth-mobile.png'});
  await page.locator('[data-growth-picker]').first().click();await page.locator('[data-growth-save]').first().waitFor();
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/vision/growth/cover')&&r.request().method()==='POST');
  await page.locator('[data-growth-save]').first().click();assert.equal((await saved).status(),200);
  await page.locator('.growth-stage').first().waitFor();
  await page.locator('[data-growth-open]').first().click();await page.locator('#albumGrid .tile:not(.sk)').first().waitFor();
  assert.match(await page.locator('#albumHeading').innerText(),/张照片/);
  await page.setViewportSize({width:1365,height:900});
  await page.locator('[data-growth="P"]').click();await page.locator('[data-album="P"]').first().click();await page.locator('#albumGrid .tile:not(.sk)').first().waitFor();
  await page.locator('#albumGrid .tile').first().click();await page.locator('#viewer').waitFor({state:'visible'});
  await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>state.viewer.i===1&&state.viewer.item);
  await page.locator('#vClose').click();
  await page.locator('[data-together="Q"]').click();await page.locator('#albumHeading h1').filter({hasText:'Alice与Bob'}).waitFor();
  assert.match(await page.locator('#albumHeading').innerText(),/1 张照片/);
  await page.locator('#collectionNav [data-collection="animals"]').click();await page.locator('[data-album="PET"]').waitFor();
  await page.locator('[data-album="PET"]').click();await page.locator('[data-together="P"]').waitFor();
  await checkAlbumDensity();
  await page.locator('[data-together="P"]').click();await page.locator('#albumHeading h1').filter({hasText:'Milo与Alice'}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.locator('#collectionNav [data-collection="memories"]').click();await page.locator('.memory-cards').waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'tests/e2e/shots/albums-mobile-memories.png'});
  await page.locator('[data-memory="today"]').click();await page.locator('#albumHeading h1').filter({hasText:'往年今日'}).waitFor();
  await page.locator('[data-memory="random"]').click();await page.locator('#albumGrid .tile:not(.sk)').first().waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('#collectionNav [data-collection="people"]').click();await page.locator('[data-album="P"]').waitFor();
  await page.waitForFunction(()=>[...document.querySelectorAll('.collection-card img')].every(i=>i.complete&&i.naturalWidth>0));
  await page.screenshot({path:'tests/e2e/shots/albums-mobile-people.png'});
  await page.locator('#btnSearch').click();await page.locator('#searchInput').fill('Alice');await page.locator('#searchGrid .tile:not(.sk)').first().waitFor();
  assert.match(await page.locator('.month-sep').innerText(),/2 张/);
  await page.locator('#searchCancel').click();await page.locator('.day-blk,.month-wall,.year-collection').first().waitFor();
  assert.equal(await page.locator('#collectionNav [data-collection="photos"]').getAttribute('aria-pressed'),'true');
  // A separate unauthorised context cannot see collection navigation or names.
  const outsider=await browser.newContext();const anon=await outsider.newPage();await anon.goto(fx.base);
  assert.equal((await outsider.request.get(fx.base+'/api/vision/albums?lib=t')).status(),401);
  assert.equal(await anon.locator('#collectionNav').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log('PASS: desktop/mobile albums, real cover images, name filter/search, photo viewer navigation, people/pet joint photos, memories, return to timeline, anonymous isolation; no browser exceptions');
 }finally{if(browser)await browser.close();if(fx)fx.child.kill()}
})().catch(error=>{console.error(error);process.exitCode=1});
