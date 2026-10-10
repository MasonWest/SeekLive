const { chromium } = require('playwright-core');
const fs = require('fs'); const path = require('path');
const OUT = path.join(__dirname, 'shots');
const EXE = process.env.EXE;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';

// 全部格子的「最终状态」（骨架 / 出图 / 占位 / 破图）
const PROBE = () => {
  const tiles = [...document.querySelectorAll('.tile')];
  const out = { total: tiles.length, skeleton: 0, loaded: 0, zero: 0, placeholder: 0, vid: 0 };
  tiles.forEach(t => {
    if (t.classList.contains('sk')) { out.skeleton++; return; }
    const ph = t.querySelector('.tile-ph');
    if (ph) { out.placeholder++; out.vid++; return; }
    const i = t.querySelector('img');
    if (!i) { out.skeleton++; return; }
    if (i.naturalWidth > 0) out.loaded++; else out.zero++;
  });
  return out;
};

(async () => {
  const b = await chromium.launch({ executablePath: fs.existsSync(EXE) ? EXE : undefined });
  const c = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
    isMobile: true, hasTouch: true, userAgent: IPHONE_UA });
  const p = await c.newPage();
  await p.goto('http://127.0.0.1:9000/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.tile', { timeout: 30000 }); await sleep(1500);
  await p.locator('.chip[data-lib="videos"]').click(); await sleep(2500);

  // 充分滚动，让所有日期分组加载并触发 lazy 图片
  for (let i = 0; i < 40; i++) {
    await p.evaluate(() => window.scrollBy(0, 400));
    await sleep(450);
    const h = await p.evaluate(() => ({ y: window.scrollY, dh: document.body.scrollHeight }));
    if (h.y + 900 >= h.dh) break;
  }
  await sleep(3500);
  const final = await p.evaluate(PROBE);
  console.log('最终状态：', JSON.stringify(final));

  // 逐屏截图（每屏一张），便于人工核对每一格
  await p.evaluate(() => window.scrollTo(0, 0)); await sleep(1500);
  const total = await p.evaluate(() => document.body.scrollHeight);
  let shot = 0;
  for (let y = 0; y < total && shot < 12; y += 700) {
    await p.evaluate(v => window.scrollTo(0, v), y);
    await sleep(1400);
    await p.screenshot({ path: path.join(OUT, `qa-ios-screen-${String(shot).padStart(2, '0')}.png`) });
    shot++;
  }
  console.log('截图张数：', shot, ' pageHeight=', total);
  await b.close();
})();
