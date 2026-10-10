/* 定向验收：索引进度面板（首次/全量索引的两阶段进度 + 完成汇总 + 增量重扫）
 *
 * 运行前提：服务端需带管理口令启动（因为要触发 full=1 全量索引）：
 *   python server.py --lan --port 9000 --no-recheck --admin-token "<测试口令>"
 *   NODE_PATH=... TOKEN="<测试口令>" node tests/e2e/verify_idx_progress.js
 */
const { chromium } = require('playwright-core');
const fs = require('fs');
const os = require('os');
const path = require('path');

const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });
const BASE = process.env.BASE || 'http://127.0.0.1:9000/';
const TOK = process.env.TOKEN || '';   // 由环境变量传入测试口令
/* 本机 chromium 目录名随版本变化（chrome-win / chrome-win64），两者都试一下。
   优先用环境变量 EXE 指定；否则按 playwright 默认缓存目录探测。 */
const PW_DIR = path.join(os.homedir(), 'AppData/Local/ms-playwright');
const _dirs = fs.existsSync(PW_DIR)
  ? fs.readdirSync(PW_DIR).filter(d => d.startsWith('chromium')).sort().reverse() : [];
const EXE = process.env.EXE || _dirs
  .flatMap(d => [path.join(PW_DIR, d, 'chrome-win64', 'chrome.exe'),
                 path.join(PW_DIR, d, 'chrome-win', 'chrome.exe')])
  .find(p => fs.existsSync(p));

const pass = [], fail = [];
const chk = (n, ok, i = '') => {
  (ok ? pass : fail).push(n + (i ? '  →  ' + i : ''));
  console.log(`${ok ? '  ✅' : '  ❌'} ${n}${i ? '  →  ' + i : ''}`);
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ executablePath: EXE, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });

  console.log('\n=== 1. 页面加载 ===');
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await sleep(2500);
  chk('页面正常渲染（时间轴有磁贴）',
    await page.locator('.tile').count() > 0,
    (await page.locator('.tile').count()) + ' 块');
  chk('索引进度入口存在', await page.locator('#btnIdx').count() === 1);

  console.log('\n=== 2. 触发全量索引（模拟「首次索引」的长过程）===');
  const trig = await page.evaluate(async (tok) => {
    const a = await fetch('/api/admin/login', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-SL-Admin': '1' },
      body: JSON.stringify({ token: tok })
    });
    if (!a.ok) return 'login ' + a.status;
    const b = await fetch('/api/index/scan', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-SL-Admin': '1' },
      body: JSON.stringify({ full: 1 })
    });
    return b.status + ' ' + JSON.stringify(await b.json().catch(() => ({})));
  }, TOK);
  chk('授权并触发全量索引', /^200/.test(trig), trig);

  console.log('\n=== 3. 重载页面 → 进度面板应自动出现并轮询 ===');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await sleep(700);
  const opened = await page.locator('#idxSheet:not([hidden])').count() === 1;
  chk('索引进行中 → 进度面板自动打开', opened,
    'sheet hidden=' + await page.locator('#idxSheet').getAttribute('hidden'));
  await page.screenshot({ path: path.join(OUT, '15-idx-panel.png') });

  console.log('\n=== 4. 采样两阶段（关键是「总数未知时不出现百分比」）===');
  let sawIndet = '', sawIndetTxt = '', sawDet = '', sawDetPct = '';
  let sawIndetKv = '', sawIndetIncHidden = null;
  let finalHint = '', finalKv = '', stageSeen = new Set();
  for (let i = 0; i < 90; i++) {
    const s = await page.evaluate(() => {
      const q = s => document.querySelector(s);
      return {
        hidden: q('#idxSheet').hidden,
        stage: (q('#idxStage') || {}).textContent || '',
        nums: (q('#idxNums') || {}).textContent || '',
        indet: !!(q('#idxBar') && q('#idxBar').classList.contains('indet')),
        hint: (q('#idxHint') || {}).textContent || '',
        kv: (q('#idxKv') || {}).innerText || '',
        incHidden: !!(q('#idxInc') || {}).hidden,
        tiles: document.querySelectorAll('.tile').length,
      };
    });
    if (s.stage) stageSeen.add(s.stage.slice(0, 24));
    if (s.indet && !sawIndet) { sawIndet = 'yes'; sawIndetTxt = s.nums; }
    else if (s.indet && s.nums.indexOf('正在扫描') >= 0) sawIndetTxt = s.nums;
    if (s.indet) { sawIndetKv = s.kv; sawIndetIncHidden = s.incHidden; }
    if (s.nums.indexOf('%') >= 0 && !sawDet) { sawDet = 'yes'; sawDetPct = s.nums; }
    finalHint = s.hint; finalKv = s.kv;
    if (s.hint === '已完成' && s.tiles > 0) break;
    await sleep(300);
  }
  chk('观察到「总数未知」的不确定态（无百分比）', sawIndet === 'yes',
    nz(sawIndetTxt));
  chk('不确定态文案为「正在扫描图库，统计文件数量……」',
    /正在扫描图库，统计文件数量/.test(sawIndetTxt), nz(sawIndetTxt));
  chk('不确定态**不含**任何百分比', sawIndetTxt.indexOf('%') < 0, nz(sawIndetTxt));
  // ★ 拿没算出来的数当结论也是一种"伪造"：统计阶段不许显示 0
  chk('统计阶段汇总不显示假的 0（一律「—」）',
    /总照片数[\s\S]{0,6}—/.test(sawIndetKv) && !/总照片数\s*\n?\s*0\b/.test(sawIndetKv),
    nz(sawIndetKv));
  chk('统计阶段不显示「本次检查 0 个文件」（还没开始比对）',
    sawIndetIncHidden === true, 'idxInc hidden=' + sawIndetIncHidden);
  chk('拿到真实总数后才出现百分比', sawDet === 'yes', nz(sawDetPct));
  chk('共观察到多个阶段（计数 → 建立索引 → 完成）', stageSeen.size >= 3,
    Array.from(stageSeen).join(' / '));
  chk('索引结束后提示「已完成」', finalHint === '已完成', finalHint);
  chk('完成汇总含 总照片数 / 视频数 / Live Photo（且为真实数字，不是 —）',
    /总照片数/.test(finalKv) && /视频数/.test(finalKv) && /Live Photo/.test(finalKv) &&
    !/—/.test(finalKv), nz(finalKv));
  const tilesAfter = await page.locator('.tile').count();
  chk('索引完成后自动进入时间轴（有磁贴）', tilesAfter > 0, tilesAfter + ' 块');
  await page.screenshot({ path: path.join(OUT, '16-idx-done.png') });

  console.log('\n=== 5. 增量重扫（不重建界面，显示本次检查/新增/变更）===');
  await page.locator('#btnIdx').click();
  await sleep(300);
  chk('再次打开面板', await page.locator('#idxSheet:not([hidden])').count() === 1);
  await page.locator('#btnIdxRescan').click();
  // 轮询等待：统计阶段（counting）不显示增量行，进入 scanning 后才出现
  const incTxt = await (async () => {
    for (let i = 0; i < 40; i++) {
      const t = await page.evaluate(() =>
        (document.querySelector('#idxInc') || {}).hidden
          ? '' : ((document.querySelector('#idxInc') || {}).innerText || ''));
      if (/本次检查/.test(t) && /新增/.test(t) && /变更/.test(t)) return t;
      await sleep(300);
    }
    return '';
  })();
  chk('增量信息含「本次检查 / 新增 / 变更」',
    /本次检查/.test(incTxt) && /新增/.test(incTxt) && /变更/.test(incTxt), nz(incTxt));
  const stillTiles = await page.locator('.tile').count();
  chk('增量重扫期间时间轴仍然可见（浏览不中断）', stillTiles > 0, stillTiles + ' 块');
  await page.screenshot({ path: path.join(OUT, '17-idx-incremental.png') });

  chk('全程无 JS 报错', errs.length === 0, nz(errs.slice(0, 2).join(' | ')));

  await browser.close();
  console.log(`\n结果：${pass.length} 通过 / ${fail.length} 失败`);
  if (fail.length) fail.forEach(f => console.log('  ❌ ' + f));
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('异常：', e); process.exit(2); });

function nz(s) { return String(s || '').replace(/\s+/g, ' ').slice(0, 90); }
