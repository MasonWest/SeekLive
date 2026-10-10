const { chromium } = require('playwright-core');
const fs = require('fs');
const EXE = process.env.EXE;
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch({ executablePath: fs.existsSync(EXE) ? EXE : undefined });
  const c = await b.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const p = await c.newPage();
  const logs = [];
  p.on('console', m => logs.push(m.type() + ': ' + m.text()));
  p.on('pageerror', e => logs.push('pageerror: ' + e.message));
  await p.goto('http://127.0.0.1:9000/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.tile', { timeout: 30000 });
  await sleep(2000);
  const t0 = Date.now();
  await p.locator('#btnInfo').click();
  for (let i = 0; i < 40; i++) {
    const st = await p.evaluate(() => ({
      hidden: document.querySelector('#vDetail').hasAttribute('hidden'),
      head: (document.querySelector('#vDetail .vd-head') || {}).textContent,
      rows: document.querySelectorAll('#vDetail #vdList dt').length,
    }));
    if (!st.hidden) { console.log(`opened after ${Date.now() - t0}ms`, JSON.stringify(st)); break; }
    await sleep(250);
  }
  const fin = await p.evaluate(() => ({
    hidden: document.querySelector('#vDetail').hasAttribute('hidden'),
    head: (document.querySelector('#vDetail .vd-head') || {}).textContent,
    rows: document.querySelectorAll('#vDetail #vdList dt').length,
    countSel: document.querySelectorAll('#vDetail:not([hidden])').length,
  }));
  console.log('final', JSON.stringify(fin));
  console.log('page logs:', logs.slice(0, 10).join(' || ') || '(none)');
  await b.close();
})();
