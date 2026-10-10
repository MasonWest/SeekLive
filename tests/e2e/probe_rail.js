const { chromium } = require('playwright-core');
const fs = require('fs');
const EXE = process.env.EXE;
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch({ executablePath: fs.existsSync(EXE) ? EXE : undefined });
  const c = await b.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  });
  const p = await c.newPage();
  await p.goto('http://127.0.0.1:9000/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.tile', { timeout: 30000 });
  await sleep(2500);
  await p.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(2500);
  await p.evaluate(() => window.scrollTo(0, 0));
  await sleep(1100);
  const yrTop = (await p.locator('#yrLabel').innerText()).trim();
  await p.locator('#yrPrev').click();
  await sleep(1300);
  const yrOld = (await p.locator('#yrLabel').innerText()).trim();
  const ymOld = await p.evaluate(() => state.railYm);
  const pillYm = await p.evaluate(() => {
    const bs = [...document.querySelectorAll('#railMonths .rm')];
    const last = bs[bs.length - 1]; last.click(); return last.dataset.ym;
  });
  await sleep(1300);
  console.log(`yrTop=${yrTop} yrOld=${yrOld} ymOld=${ymOld} pillYm=${pillYm}`);

  // 关键：scrollTo(0,0) 之后逐帧观察 scrollY 是否被“拽回”到目标月
  await p.evaluate(() => window.scrollTo(0, 0));
  for (let i = 0; i < 32; i++) {
    const s = await p.evaluate(() => ({
      y: Math.round(window.scrollY),
      yr: (document.querySelector('#yrLabel').textContent || '').trim(),
      railYm: state.railYm,
      firstSep: (document.querySelector('#timeline .month-sep[data-ym]') || {}).dataset?.ym,
    }));
    console.log(`t=${(i * 100).toString().padStart(4)}ms  scrollY=${String(s.y).padStart(5)}  yrLabel=${s.yr.padEnd(8)} railYm=${s.railYm} firstSep=${s.firstSep}`);
    await sleep(100);
  }
  await b.close();
})();
