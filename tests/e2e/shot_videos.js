const { chromium } = require('playwright-core');
const fs = require('fs'); const path = require('path');
const OUT = path.join(__dirname, 'shots');
const EXE = process.env.EXE;
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch({ executablePath: fs.existsSync(EXE) ? EXE : undefined });
  // 桌面：视频库网格 + 索引信息面板
  const c = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const p = await c.newPage();
  await p.goto('http://127.0.0.1:9000/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.tile', { timeout: 30000 }); await sleep(2200);
  await p.locator('.chip[data-lib="videos"]').click(); await sleep(3000);
  await p.screenshot({ path: path.join(OUT, '09-desktop-video-grid.png') });
  await p.locator('#btnInfo').click(); await sleep(900);
  await p.screenshot({ path: path.join(OUT, '10-index-info.png') });
  console.log('desktop ok');
  // 移动端：视频库
  const c2 = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
    isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' });
  const p2 = await c2.newPage();
  await p2.goto('http://127.0.0.1:9000/', { waitUntil: 'domcontentloaded' });
  await p2.waitForSelector('.tile', { timeout: 30000 }); await sleep(2200);
  await p2.locator('.chip[data-lib="videos"]').click(); await sleep(3000);
  await p2.screenshot({ path: path.join(OUT, '11-mobile-video-grid.png') });
  console.log('mobile ok');
  await b.close();
})();
