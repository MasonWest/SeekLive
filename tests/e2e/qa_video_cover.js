const { chromium } = require('playwright-core');
const fs = require('fs'); const path = require('path');
const OUT = path.join(__dirname, 'shots');
const EXE = process.env.EXE;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';

(async () => {
  const b = await chromium.launch({ executablePath: fs.existsSync(EXE) ? EXE : undefined });

  /* ---- 1. iPhone 视口：视频库封面（预热后应有图） ---- */
  const c = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
    isMobile: true, hasTouch: true, userAgent: IPHONE_UA });
  const p = await c.newPage();
  await p.goto('http://127.0.0.1:9000/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.tile', { timeout: 30000 }); await sleep(1500);
  await p.locator('.chip[data-lib="videos"]').click(); await sleep(3500);
  await p.locator('.chip[data-lib="videos"]').click().catch(() => {});
  await p.evaluate(() => window.scrollTo(0, 0));
  await sleep(2500);
  // 统计：有图 vs 占位
  const stat1 = await p.evaluate(() => {
    const tiles = [...document.querySelectorAll('.tile')];
    return {
      tiles: tiles.length,
      withImg: tiles.filter(t => t.querySelector('img') && t.querySelector('img').naturalWidth > 0).length,
      placeholders: tiles.filter(t => t.querySelector('.tile-ph')).length,
      zeroWidth: tiles.filter(t => { const i = t.querySelector('img'); return i && i.naturalWidth === 0; }).length,
    };
  });
  await p.screenshot({ path: path.join(OUT, 'qa-ios-video-warm.png') });
  console.log('iOS 视频库封面：', JSON.stringify(stat1));

  /* ---- 2. iPhone 视口：拦截 /thumb 全部返回 500，验证占位兜底 ---- */
  const c2 = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
    isMobile: true, hasTouch: true, userAgent: IPHONE_UA });
  const p2 = await c2.newPage();
  let blocked = 0;
  await p2.route('**/thumb**', route => { blocked++; route.fulfill({ status: 500, body: 'boom' }); });
  await p2.goto('http://127.0.0.1:9000/', { waitUntil: 'domcontentloaded' });
  await p2.waitForSelector('.tile', { timeout: 30000 }); await sleep(1500);
  await p2.locator('.chip[data-lib="videos"]').click(); await sleep(4000);
  const stat2 = await p2.evaluate(() => {
    const tiles = [...document.querySelectorAll('.tile')];
    return {
      tiles: tiles.length,
      placeholders: tiles.filter(t => t.querySelector('.tile-ph')).length,
      vidPlaceholders: tiles.filter(t => t.querySelector('.tile-ph.vid')).length,
      plainBlank: tiles.filter(t => !t.querySelector('img') && !t.querySelector('.tile-ph')).length,
    };
  });
  await p2.screenshot({ path: path.join(OUT, 'qa-ios-video-fail.png') });
  console.log('拦截 /thumb → 500：', JSON.stringify(stat2), '拦截次数=', blocked);

  /* ---- 3. iPhone 视口：Live Photo 库（回归） ---- */
  const c3 = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
    isMobile: true, hasTouch: true, userAgent: IPHONE_UA });
  const p3 = await c3.newPage();
  await p3.goto('http://127.0.0.1:9000/', { waitUntil: 'domcontentloaded' });
  await p3.waitForSelector('.tile', { timeout: 30000 }); await sleep(1500);
  await p3.locator('.chip[data-lib="live"]').click(); await sleep(3500);
  const stat3 = await p3.evaluate(() => {
    const tiles = [...document.querySelectorAll('.tile')];
    return {
      tiles: tiles.length,
      withImg: tiles.filter(t => { const i = t.querySelector('img'); return i && i.naturalWidth > 0; }).length,
      liveBadges: document.querySelectorAll('.badge.live').length,
      placeholders: tiles.filter(t => t.querySelector('.tile-ph')).length,
    };
  });
  await p3.screenshot({ path: path.join(OUT, 'qa-ios-live-regression.png') });
  console.log('iOS Live Photo 库：', JSON.stringify(stat3));

  await b.close();
  console.log('QA done');
})();
