/* 正式版 UI 端到端验收（真实 Chromium/Edge） */
/* ============================================================================
 * ⚠️ DEPRECATED（已废弃）—— 默认拒绝运行，避免误跑破坏真实数据
 * ----------------------------------------------------------------------------
 * 本脚本口径已过时（断言「匿名 /api/libs → 200」、图库 id 写死等），对当前版本会误报；
 * 且带 TOKEN 时会对**真实服务**执行真删 / 真恢复，可能破坏你的真实图库。
 *
 *   ✅ 请改用：node tests/e2e/qa_share.js        （自带隔离夹具，不碰真实图库）
 *            node tests/e2e/qa_keep_scroll.js   （只跑重建路径，不真删图）
 *
 * 如确需在「可随时丢弃的测试实例」上运行，显式设置环境变量再执行：
 *   SEEKLIVE_VERIFY_UI_ALLOW=1 node tests/e2e/verify_ui.js
 * ========================================================================== */
if (process.env.SEEKLIVE_VERIFY_UI_ALLOW !== '1') {
  console.error('\n[已阻止] tests/e2e/verify_ui.js 已废弃，且会对真实服务执行破坏性操作。');
  console.error('  请改用 tests/e2e/qa_share.js（隔离夹具）或 qa_keep_scroll.js。');
  console.error('  确需运行：SEEKLIVE_VERIFY_UI_ALLOW=1 node tests/e2e/verify_ui.js');
  console.error('  并确保目标是可以随时丢弃的测试实例。\n');
  process.exit(2);
}

const { chromium, request } = require('playwright-core');
const fs = require('fs');
const os = require('os');
const path = require('path');

const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });
const BASE = process.env.BASE || 'http://127.0.0.1:9000/';
/* 管理授权模式：TOKEN=xxxx 表示「服务端配置了管理口令」。
   ★ 浏览永远匿名 —— 口令**不进 URL、不进 storage**，只在授权弹窗里输入一次。 */
const TOK = process.env.TOKEN || '';
const URL0 = BASE;
const DENIED = c => c === 401 || c === 403;
/* 未授权时四个破坏性接口的期望状态：
   未配置口令 → 403；已配置口令但无会话 → 401 */
/* 本机 chromium 目录名随版本变化（chrome-win / chrome-win64），两者都试一下。
   优先用环境变量 EXE 指定；否则按 playwright 默认缓存目录探测。 */
const PW_DIR = path.join(os.homedir(), 'AppData/Local/ms-playwright');
const EXE = process.env.EXE || fs.readdirSync(PW_DIR)
  .filter(d => d.startsWith('chromium'))
  .sort().reverse()
  .map(d => path.join(PW_DIR, d, 'chrome-win64', 'chrome.exe'))
  .concat(fs.readdirSync(PW_DIR)
    .filter(d => d.startsWith('chromium'))
    .sort().reverse()
    .map(d => path.join(PW_DIR, d, 'chrome-win', 'chrome.exe')))
  .find(p => fs.existsSync(p));

const pass = [], fail = [];
const chk = (name, ok, info = '') => {
  (ok ? pass : fail).push(name + (info ? '  →  ' + info : ''));
  console.log(`${ok ? '  ✅' : '  ❌'} ${name}${info ? '  →  ' + info : ''}`);
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 轮询等待条件成立（避免因缩略图并发导致的偶发超时） */
async function waitFor(fn, timeout = 15000, step = 400) {
  const until = Date.now() + timeout;
  let last;
  while (Date.now() < until) {
    try { last = await fn(); } catch (e) { last = undefined; }
    if (last) return last;
    await sleep(step);
  }
  return last;
}

/* 用 CDP 派发真实触摸滑动（playwright 的 touchscreen 只有 tap） */
async function dispatchSwipe(page, x1, y1, x2, y2, steps = 8) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x1, y: y1 }] });
  for (let i = 1; i <= steps; i++) {
    const x = x1 + (x2 - x1) * i / steps, y = y1 + (y2 - y1) * i / steps;
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y }] });
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await cdp.detach();
}

(async () => {
  const browser = await chromium.launch({ executablePath: fs.existsSync(EXE) ? EXE : undefined });
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true,
    hasTouch: true, userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  const reqs = [];
  page.on('request', r => { const u = r.url(); if (u.includes('/thumb') || u.includes('/photo') || u.includes('/video')) reqs.push(u); });

  console.log('\n=== 1. 首屏（移动端 390×844） ===');
  await page.goto(URL0, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.tile', { timeout: 30000 });
  await sleep(2500);
  chk('时间轴已渲染', await page.locator('.day-blk').count() > 0,
    `${await page.locator('.day-blk').count()} 个日期分组`);
  chk('月标题已渲染', await page.locator('.month-sep').count() > 0,
    (await page.locator('.month-sep').first().innerText()).replace(/\n/g, ' '));
  const tiles = await page.locator('.tile img').count();
  chk('图片磁贴已生成', tiles > 0, tiles + ' 张');
  const loaded = await page.locator('.tile.loaded').count();
  chk('可视区图片已加载', loaded > 0, loaded + ' 张完成');

  // 懒加载：首屏只应请求"可见 + 预取"的图片，而不是全部
  const thumbReqs = reqs.filter(u => u.includes('/thumb')).length;
  chk('懒加载生效（未一次性拉全部）', thumbReqs < tiles, `首屏请求 ${thumbReqs} / 共 ${tiles} 张`);

  await page.screenshot({ path: path.join(OUT, '01-mobile-timeline.png') });

  console.log('\n=== 2. 滚动加载（分页） ===');
  const before = await page.locator('.day-blk').count();
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(2500);
  const after = await page.locator('.day-blk').count();
  chk('滚动触发更多分组加载', after > before, `${before} → ${after}`);

  console.log('\n=== 2b. 年份 / 月份导航栏（< 2025 >） ===');
  await page.evaluate(() => window.scrollTo(0, 0));
  await sleep(1100);
  chk('导航栏已显示', await page.locator('#rail:not([hidden])').count() === 1,
    (await page.locator('#yrLabel').innerText()).trim());
  const yrTop = (await page.locator('#yrLabel').innerText()).trim();
  const pills = await page.locator('#railMonths .rm').count();
  chk('月份条按年份渲染', pills > 0, `${yrTop} · ${pills} 个月份`);

  await page.locator('#yrPrev').click();
  await sleep(1300);
  const yrOld = (await page.locator('#yrLabel').innerText()).trim();
  const ymOld = await page.evaluate(() => state.railYm);
  chk('‹ 切到更早一年', yrOld !== yrTop, `${yrTop} → ${yrOld}`);
  chk('‹ 后月份条已重建（只含该年）',
    (await page.locator('#railMonths .rm').count()) >= 1,
    `${yrOld} · ${await page.locator('#railMonths .rm').count()} 个月份`);
  const pos = await page.evaluate(ym => {
    const s = document.querySelector(`.month-sep[data-ym="${ym}"]`);
    if (!s) return null;
    const off = document.querySelector('#topbar').offsetHeight +
      document.querySelector('#rail').offsetHeight;
    return { top: Math.round(s.getBoundingClientRect().top), off };
  }, ymOld);
  chk('跳转后该月标题停在导航栏正下方',
    !!pos && pos.top <= pos.off + 16 && pos.top >= pos.off - 40,
    `${ymOld} top=${pos && pos.top} / 栏底=${pos && pos.off}`);

  const pillYm = await page.evaluate(() => {
    const bs = [...document.querySelectorAll('#railMonths .rm')];
    const last = bs[bs.length - 1];
    last.click();
    return last.dataset.ym;
  });
  await sleep(1300);
  const pos2 = await page.evaluate(ym => {
    const s = document.querySelector(`.month-sep[data-ym="${ym}"]`);
    return s ? Math.round(s.getBoundingClientRect().top) : null;
  }, pillYm);
  chk('点击月份可直接定位', pos2 !== null && pos2 < 340, `${pillYm} top=${pos2}`);

  await page.evaluate(() => window.scrollTo(0, 0));
  const yrBack = await waitFor(async () => {
    const t = (await page.locator('#yrLabel').innerText()).trim();
    return t === yrTop ? t : null;
  }, 9000) || (await page.locator('#yrLabel').innerText()).trim();
  chk('滚动回顶部年份自动同步', yrBack === yrTop, `${yrOld} → ${yrBack}`);

  await page.locator('#yrPrev').click();
  const yrMid = await waitFor(async () => {
    const t = (await page.locator('#yrLabel').innerText()).trim();
    return t !== yrBack ? t : null;
  }, 9000) || (await page.locator('#yrLabel').innerText()).trim();
  await page.locator('#yrNext').click();
  const yrBackBtn = await waitFor(async () => {
    const t = (await page.locator('#yrLabel').innerText()).trim();
    return t === yrTop ? t : null;
  }, 9000) || (await page.locator('#yrLabel').innerText()).trim();
  chk('› 可切回更新一年', yrMid !== yrBackBtn && yrBackBtn === yrTop,
    `${yrMid} → ${yrBackBtn}`);

  await page.locator('#btnSearch').click();
  await page.locator('#searchInput').fill('zzzz');
  await sleep(1200);
  chk('搜索进行时导航栏自动隐藏', await page.locator('#rail[hidden]').count() === 1);
  await page.locator('#searchCancel').click();
  await sleep(1100);
  chk('退出搜索后导航栏恢复', await page.locator('#rail:not([hidden])').count() === 1);

  console.log('\n=== 3. 打开灯箱 ===');
  await page.evaluate(() => window.scrollTo(0, 0));
  await sleep(600);
  await page.locator('.tile').first().click();
  await page.waitForSelector('#viewer:not([hidden])', { timeout: 8000 });
  await sleep(2200);
  chk('灯箱已打开', await page.locator('#viewer:not([hidden])').count() === 1);
  const hi = await page.locator('#vMedia .v-hi').count();
  chk('高清图元素存在', hi === 1);
  const imgState = await page.evaluate(() => {
    const i = document.querySelector('#vMedia .v-hi');
    return i ? { w: i.naturalWidth, h: i.naturalHeight } : null;
  });
  chk('高清图已解码', !!imgState && imgState.w > 0, imgState ? `${imgState.w}×${imgState.h}` : '无');
  chk('标题显示序号', /\/ \d+/.test(await page.locator('#vTitle').innerText()),
    await page.locator('#vTitle').innerText());
  await page.screenshot({ path: path.join(OUT, '02-mobile-viewer.png') });

  console.log('\n=== 4. 灯箱切换下一张（手机端用键盘/滑动） ===');
  const t1 = await page.locator('#vTitle').innerText();
  await page.keyboard.press('ArrowRight');
  await sleep(1400);
  const t2 = await page.locator('#vTitle').innerText();
  chk('可切换到下一张', t1 !== t2, `${t1} → ${t2}`);
  // 真实触摸滑动
  const t3 = await page.locator('#vTitle').innerText();
  await dispatchSwipe(page, 330, 240, 60, 240);
  await sleep(1200);
  const t4 = await page.locator('#vTitle').innerText();
  chk('触摸滑动可切换（手机核心手势）', t4 !== t3, `${t3} → ${t4}`);

  console.log('\n=== 5. 桌面端 ===');
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const p2 = await ctx2.newPage();
  p2.on('pageerror', e => errs.push('[desktop] ' + String(e)));
  await p2.goto(URL0, { waitUntil: 'domcontentloaded' });
  await p2.waitForSelector('.tile', { timeout: 30000 });
  await sleep(2800);
  const cols = await p2.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--cols').trim());
  chk('桌面网格列数自适应', +cols >= 6, `--cols = ${cols}`);
  await p2.screenshot({ path: path.join(OUT, '03-desktop-timeline.png') });

  console.log('\n=== 6. Live Photo 图库 ===');
  await p2.locator('.chip[data-lib="live"]').click();
  await sleep(2500);
  const liveTiles = await p2.locator('.tile .badge.live').count();
  chk('LIVE 徽标渲染', liveTiles >= 8, liveTiles + ' 个');
  // 必须点「确实是实况」的那一块：同秒导入的照片可能并列，首块未必是 Live
  // （顺序本身已在 index_db 里做了稳定次级键，这里再加一层与顺序无关的保险）
  const liveTile = p2.locator('#timeline .tile:has(.badge.live)').first();
  await liveTile.scrollIntoViewIfNeeded().catch(() => {});
  await liveTile.click();
  const readMotion = () => p2.evaluate(() => {
    const v = document.querySelector('#vMedia video.v-motion');
    if (!v) return { found: false };
    return { found: true, rs: v.readyState, w: v.videoWidth, h: v.videoHeight,
             ct: v.currentTime, t: v.currentTime > 0 };
  });
  // 等「解出像素」且「播放进度前进」，避免解码与播放的时序抖动
  const motion = await waitFor(async () => {
    const m = await readMotion();
    return m && m.w > 0 && m.ct > 0 ? m : null;
  }, 20000) || await readMotion();
  chk('LivePhoto 动图视频已挂载', motion.found, JSON.stringify(motion));
  if (motion.found) {
    chk('动图视频已解出像素（非假播放）', motion.w > 0, `${motion.w}×${motion.h}`);
    chk('动图正在播放', motion.t, `currentTime=${(motion.ct || 0).toFixed(2)}s`);
  }
  await p2.screenshot({ path: path.join(OUT, '04-desktop-livephoto.png') });

  console.log('\n=== 6b. 独立视频（单 MOV/MP4/MKV）===');
  await p2.keyboard.press('Escape');            // 先关掉上一节的灯箱
  await sleep(700);
  const vreqs = [];
  p2.on('request', r => { const u = r.url(); if (u.includes('/video')) vreqs.push(u); });
  await p2.locator('.chip[data-lib="videos"]').click();
  await sleep(2600);
  const vtiles = await p2.locator('.tile').count();
  chk('视频库条目渲染', vtiles > 0, vtiles + ' 条');
  const vthumb = await waitFor(async () => {
    const t = await p2.evaluate(() => {
      const i = document.querySelector('.tile img');
      return i && i.naturalWidth ? { w: i.naturalWidth, h: i.naturalHeight } : null;
    });
    return t;
  }, 15000);
  chk('视频缩略图已生成（服务端抽帧）', !!vthumb && vthumb.w > 0,
    vthumb ? `${vthumb.w}×${vthumb.h}` : '无');
  await p2.locator('.tile').first().click();
  const vm = await waitFor(async () => {
    const m = await p2.evaluate(() => {
      const v = document.querySelector('#vMedia video.v-video');
      return v ? { found: true, rs: v.readyState, w: v.videoWidth, h: v.videoHeight,
                   ct: v.currentTime, src: v.currentSrc || '' } : null;
    });
    return m && m.w > 0 ? m : null;
  }, 25000) || await p2.evaluate(() => {
    const v = document.querySelector('#vMedia video.v-video');
    return v ? { found: true, rs: v.readyState, w: v.videoWidth, h: v.videoHeight,
                 ct: v.currentTime, src: v.currentSrc || '' } : { found: false };
  });
  chk('视频播放器已挂载', vm.found, JSON.stringify(vm).slice(0, 170));
  if (vm.found) {
    chk('视频已解出像素', vm.w > 0, `${vm.w}×${vm.h}`);
    chk('H.264 原样直出（未触发整文件转码）', !/t=1/.test(vm.src),
      (vm.src.split('/video?')[1] || '').slice(0, 90));
  }
  chk('/video 请求未携带 t=1（无整文件转码）',
    vreqs.length > 0 && !vreqs.some(u => u.includes('t=1')),
    vreqs.length + ' 次请求');
  await p2.screenshot({ path: path.join(OUT, '08-desktop-video.png') });

  console.log('\n=== 6c. 手动重新扫描索引 ===');
  await p2.keyboard.press('Escape');
  await sleep(600);
  await p2.locator('#btnMenu').click();
  await sleep(700);
  chk('月份面板含「重新扫描索引」', await p2.locator('#btnRefresh').count() === 1);
  await p2.locator('#btnRefresh').click();
  await waitFor(async () =>
    (await p2.locator('.day-blk').count()) > 0, 12000);
  chk('扫描后时间轴正常重建', await p2.locator('.day-blk').count() > 0,
    `${await p2.locator('.day-blk').count()} 个分组`);
  const idx = await p2.evaluate(() => fetch('/api/index').then(r => r.json()));
  chk('索引 API 返回行数与库数', (idx.db && idx.db.rows > 0 && idx.db.libs >= 3),
    `${idx.db.rows} 行 / ${idx.db.libs} 库`);
  chk('两次扫描均未重复解析 MOV（增量生效）',
    idx.libraries.every(x => !x.movParsed),
    idx.libraries.map(x => `${x.name}:${x.movParsed}`).join(' '));

  console.log('\n=== 7. iOS Safari 原生 LivePhotosKit 路径 ===');
  const ctxIos = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  });
  const pIos = await ctxIos.newPage();
  pIos.on('pageerror', e => errs.push('[ios] ' + String(e)));
  await pIos.goto(URL0, { waitUntil: 'domcontentloaded' });
  await pIos.waitForSelector('.tile', { timeout: 30000 });
  await sleep(1500);
  await pIos.locator('.chip[data-lib="live"]').click();
  await sleep(2200);
  const liveTileIos = pIos.locator('#timeline .tile:has(.badge.live)').first();
  await liveTileIos.scrollIntoViewIfNeeded().catch(() => {});
  await liveTileIos.click();
  await sleep(1200);
  // LivePhotosKit 走 Apple CDN，加载耗时不稳定 → 轮询等待而不是死等固定秒数
  const lpkLoaded = await waitFor(async () =>
    await pIos.evaluate(() =>
      !!(window.LivePhotosKit && window.LivePhotosKit.Player)), 30000);
  await sleep(1500);
  const lpk = await pIos.evaluate(() => {
    const has = !!(window.LivePhotosKit && window.LivePhotosKit.Player);
    const inst = !!(typeof state !== 'undefined' && state.viewer && state.viewer.lpk);
    const dom = document.querySelectorAll('#vMedia .lpk-player, #vMedia canvas, #vMedia video').length;
    return { has, inst, dom };
  });
  chk('LivePhotosKit 已加载', lpk.has, JSON.stringify(lpk) + ' cdn=' + !!lpkLoaded);
  chk('iOS 走原生 LPK 播放器（非降级）', lpk.inst, `实例=${lpk.inst} DOM=${lpk.dom}`);
  await pIos.screenshot({ path: path.join(OUT, '07-ios-livephoto.png') });

  console.log('\n=== 8. 搜索 / 月份面板 / 详情 ===');
  await p2.keyboard.press('Escape');
  await sleep(500);
  // 前面的新用例切到了「培训视频」库；搜索关键字 IMG_48 在 Live Photo 库里
  await p2.locator('.chip[data-lib="live"]').click();
  await sleep(2400);
  await p2.locator('#btnSearch').click();
  await p2.locator('#searchInput').fill('IMG_48');
  await sleep(1600);
  const sr = await p2.locator('#searchGrid .tile').count();
  chk('搜索返回结果', sr > 0, sr + ' 张');
  await p2.screenshot({ path: path.join(OUT, '05-desktop-search.png') });
  await p2.locator('#searchCancel').click();
  await sleep(900);
  await p2.locator('#btnMenu').click();
  await sleep(500);
  chk('月份面板已打开', await p2.locator('#sheet:not([hidden])').count() === 1,
    `${await p2.locator('.m-item').count()} 个月份`);
  await p2.screenshot({ path: path.join(OUT, '06-desktop-months.png') });

  console.log('\n=== 8a. 孤儿缓存清理（预览不删除任何文件） ===');
  chk('月份面板含「孤儿缓存清理」', await p2.locator('#btnGc').count() === 1);
  await p2.locator('#btnGc').click();
  const gcHint = await waitFor(async () => {
    const t = (await p2.locator('#gcHint').innerText()).trim();
    return (t && t !== '计算中…') ? t : null;
  }, 25000);
  chk('点击给出缓存预览（第一下不执行删除）', !!gcHint, gcHint || '无响应');
  await p2.screenshot({ path: path.join(OUT, '11-cache-gc.png') });

  console.log('\n=== 8a2. 最近删除（回收站）入口 + 权限提示 + 服务端闸门 ===');
  await p2.locator('#sheet .sheet-bg').click();
  await sleep(600);
  chk('顶栏含「选择」按钮', await p2.locator('#btnSelect').count() === 1);
  chk('顶栏含「最近删除」按钮', await p2.locator('#btnTrash').count() === 1);
  await p2.locator('#btnTrash').click();
  await p2.locator('#trashSheet:not([hidden])').waitFor({ timeout: 8000 });
  chk('回收站面板已打开', await p2.locator('#trashSheet:not([hidden])').count() === 1);
  const tHead = (await p2.locator('#trashSheet .vd-head').innerText()).trim();
  chk('面板标题为「最近删除」', tHead.includes('最近删除'), tHead);
  const noteShown = await waitFor(async () =>
    await p2.locator('#trashNote:not([hidden])').count() === 1, 12000);
  const noteTxt = (await p2.locator('#trashNote').innerText()).trim();
  chk('回收站面板给出明确的能力/授权原因', !!noteShown, noteTxt.slice(0, 78));
  if (!TOK) {
    chk('未配置管理口令 → 文案说明「恢复与永久删除已关闭」',
      /未配置管理口令/.test(noteTxt), noteTxt.slice(0, 60));
  } else {
    chk('已配置口令但未授权 → 文案说明「需要管理授权」',
      /需要管理授权/.test(noteTxt), noteTxt.slice(0, 60));
  }
  const listTxt = (await p2.locator('#trashList').innerText()).trim();
  chk('回收站列表或空态可渲染', listTxt.length > 0, listTxt.slice(0, 32));
  await p2.screenshot({ path: path.join(OUT, '14-trash-panel.png') });

  /* 服务端独立闸门：用**全新的无 Cookie 会话**验证（页面上的 fetch 会带 Cookie） */
  const anon = await request.newContext({ baseURL: BASE.replace(/\/+$/, '') });
  const anonLibs = await anon.get('/api/libs');
  chk('匿名浏览 /api/libs → 200（浏览完全免登录）',
    anonLibs.status() === 200, 'HTTP ' + anonLibs.status());
  const anonThumb = await anon.get('/api/items?lib=live&lim=1');
  chk('匿名浏览 /api/items → 200', anonThumb.status() === 200);
  const gateDel = (await anon.post('/api/delete',
    { data: { lib: 'live', items: ['nope.jpg'], dry: 1 } })).status();
  const gateRes = (await anon.post('/api/trash/restore',
    { data: { lib: 'live', items: ['x'] } })).status();
  const gatePurge = (await anon.post('/api/trash/purge',
    { data: { lib: 'live', items: ['x'], confirm: 1 } })).status();
  const gateEmpty = (await anon.post('/api/trash/empty',
    { data: { lib: 'live', confirm: 1 } })).status();
  await anon.dispose();
  const EXP_GATE = TOK ? 401 : 403;
  chk(`未授权 POST /api/delete → ${EXP_GATE}（服务端独立校验）`,
    gateDel === EXP_GATE, 'HTTP ' + gateDel);
  chk(`未授权 POST /api/trash/restore → ${EXP_GATE}`, gateRes === EXP_GATE,
    'HTTP ' + gateRes);
  chk(`未授权 POST /api/trash/purge → ${EXP_GATE}`, gatePurge === EXP_GATE,
    'HTTP ' + gatePurge);
  chk(`未授权 POST /api/trash/empty → ${EXP_GATE}`, gateEmpty === EXP_GATE,
    'HTTP ' + gateEmpty);
  await p2.locator('#trashSheet .sheet-bg').click();
  await sleep(500);

  /* ===== 管理授权闭环 + 真删真恢复（仅 TOKEN=xxx 时）===== */
  if (TOK) {
    console.log('\n=== 8a3. 管理授权闭环 + 真实「移入回收站 → 恢复」（live 测试库） ===');
    const metas = await p2.evaluate(async () => {
      const r = await fetch('/api/libs'); const d = await r.json();
      return d.libraries.map(l => ({ id: l.id, can: !!l.canManage, ro: !!l.readonly }));
    });
    const mLive = metas.find(l => l.id === 'live') || {};
    const mWe = metas.find(l => l.id === 'wework') || {};
    chk('已开启删除的图库 canManage=true（能力，与是否已授权无关）',
      mLive.can === true, JSON.stringify(metas));
    chk('只读图库仍被拒（口令不改变 readonly）', mWe.can === false,
      'wework readonly=' + mWe.ro);

    const countOf = id => p2.evaluate(async lid => {
      const r = await fetch('/api/libs'); const d = await r.json();
      const l = d.libraries.find(x => x.id === lid) || {};
      return l.count || 0;
    }, id);
    const trashStat = () => p2.evaluate(async () => {
      const r = await fetch('/api/trash?lib=live');
      const d = await r.json();
      return { items: d.stats.items, groups: (d.groups || []).length };
    });

    const n0 = await countOf('live');
    await p2.locator('.chip[data-lib="live"]').click();
    await sleep(2400);
    if (await p2.locator('#selBar:not([hidden])').count() === 0) {
      await p2.locator('#btnSelect').click(); await sleep(400);
    }
    chk('进入选择模式出现底部操作条',
      await p2.locator('#selBar:not([hidden])').count() === 1);
    await p2.locator('.tile').first().click();
    await sleep(400);
    const selTxt = (await p2.locator('#selCount').innerText()).trim();
    chk('勾选后计数正确且删除按钮就绪',
      /已选\s*1\s*项/.test(selTxt) && !(await p2.locator('#selDelete').isDisabled()), selTxt);

    /* --- A. 未授权 → 点删除应弹出「管理授权」输入框 --- */
    await p2.locator('#selDelete').click();
    const authShown = await waitFor(async () =>
      await p2.locator('#confirm:not([hidden])').count() === 1 &&
      await p2.locator('#aaPw').count() === 1, 15000);
    const authTitle = authShown ? (await p2.locator('#cfmTitle').innerText()).trim() : '';
    chk('未授权点删除 → 弹出管理授权输入框（不是直接失败）',
      !!authShown && authTitle === '需要管理授权', authTitle);
    await p2.screenshot({ path: path.join(OUT, '18-admin-auth.png') });

    /* --- B. 错误口令 → 提示失败，且不进入业务 --- */
    await p2.locator('#aaPw').fill('definitely-wrong-pw');
    await p2.locator('#cfmYes').click();
    const badToast = await waitFor(async () => {
      const t = (await p2.locator('#toast').innerText()).trim();
      return /口令错误|授权失败/.test(t) ? t : null;
    }, 12000);
    chk('错误口令 → 明确提示失败', !!badToast, (badToast || '').slice(0, 48));
    chk('错误口令不会误删文件',
      (await p2.evaluate(async () => (await (await fetch('/api/trash?lib=live')).json())
        .stats.items)) === 0);

    /* --- C. 正确口令 → 授权成功 → 自动重试 → 进入二次确认 --- */
    await p2.locator('#selDelete').click();
    const auth2 = await waitFor(async () =>
      await p2.locator('#aaPw').count() === 1, 15000);
    chk('再次点击删除 → 重新要求授权', !!auth2);
    await p2.locator('#aaPw').fill(TOK);
    await p2.locator('#cfmYes').click();
    const cfmShown = await waitFor(async () => {
      const t = (await p2.locator('#cfmTitle').innerText()).trim();
      return t.includes('移入回收站') ? t : null;
    }, 20000);
    const cfmBody = (await p2.locator('#cfmBody').innerText()).trim();
    chk('授权成功后**自动重试**并进入「移入回收站」二次确认',
      !!cfmShown && /保留\s*\d+\s*天/.test(cfmBody),
      (cfmShown || '') + ' ｜ ' + cfmBody.replace(/\s+/g, ' ').slice(0, 46));

    /* --- D. 凭据卫生：口令不进 URL / 不进 storage --- */
    const leak = await p2.evaluate(() => ({
      url: location.href,
      ss: JSON.stringify(Object.entries(sessionStorage)),
      ls: JSON.stringify(Object.entries(localStorage)),
      ck: document.cookie,
    }));
    chk('口令不出现在 URL', leak.url.indexOf('token=') < 0, leak.url);
    chk('口令不写入 sessionStorage', leak.ss.indexOf('__PW__') < 0 &&
      leak.ss.indexOf('sl_token') < 0, leak.ss.slice(0, 60));
    chk('口令不写入 localStorage', leak.ls.indexOf('sl_token') < 0, leak.ls.slice(0, 60));
    chk('会话 Cookie 为 HttpOnly（JS document.cookie 读不到）',
      leak.ck.indexOf('sl_admin') < 0, 'cookie=' + (leak.ck || '(空)').slice(0, 40));
    await p2.screenshot({ path: path.join(OUT, '15-trash-confirm.png') });

    /* --- E. 真删 → 回收站 → 恢复 --- */
    await p2.locator('#cfmYes').click();
    const afterDel = await waitFor(async () => {
      const t = await trashStat(); return t.items > 0 ? t : null;
    }, 25000);
    chk('确认后文件进入回收站', !!afterDel,
      afterDel ? `${afterDel.items} 个文件 / ${afterDel.groups} 组` : '未进入');

    await p2.locator('#btnTrash').click();
    await p2.locator('#trashSheet:not([hidden])').waitFor({ timeout: 8000 });
    await sleep(800);
    const listNow = (await p2.locator('#trashList').innerText()).trim();
    chk('回收站列出条目并给出剩余天数',
      /剩余/.test(listNow) || /已到期/.test(listNow),
      listNow.replace(/\s+/g, ' ').slice(0, 64));
    chk('有授权 + 有条目 → 显示操作条（恢复 / 永久删除）',
      await p2.locator('#trashBar:not([hidden])').count() === 1);
    await p2.screenshot({ path: path.join(OUT, '16-trash-has-item.png') });
    chk('非空时「清空回收站」可用',
      !(await p2.locator('#btnEmptyTrash').isDisabled()));

    await p2.locator('#trashAll').click();
    await sleep(300);
    await p2.locator('#trashRestore').click();
    const afterRes = await waitFor(async () => {
      const t = await trashStat(); return t.items === 0 ? t : null;
    }, 30000);
    chk('整组恢复后回收站清空', !!afterRes, afterRes ? '0 项' : '仍有残留');
    const n1 = await countOf('live');
    chk('恢复后原图库条目数回到删除前', n1 === n0, `${n0} → ${n1}`);

    /* --- F. 退出授权 → 再点删除应重新要求授权 --- */
    const lo = await p2.evaluate(async () => {
      const r = await fetch('/api/admin/logout', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-SL-Admin': '1' }
      });
      const s = await (await fetch('/api/admin/status')).json();
      return { code: r.status, authed: s.authed };
    });
    chk('退出管理授权 → 会话立即失效', lo.code === 200 && lo.authed === false,
      JSON.stringify(lo));

    await p2.locator('#trashSheet .sheet-bg').click();
    await sleep(400);
    if (await p2.locator('#selBar:not([hidden])').count() === 0) {
      await p2.locator('#btnSelect').click(); await sleep(400);
    }
    await p2.locator('.tile').first().click();
    await sleep(300);
    await p2.locator('#selDelete').click();
    const auth3 = await waitFor(async () =>
      await p2.locator('#aaPw').count() === 1, 15000);
    chk('退出后再次删除 → 重新要求授权', !!auth3);
    await p2.locator('#cfmNo').click();
    await sleep(300);
    if (await p2.locator('#selBar:not([hidden])').count() === 1) {
      await p2.locator('#selCancel').click(); await sleep(400);
    }
  }

  console.log('\n=== 8b. 详情面板（灯箱内外通用）===');
  // 8a2 已把月份面板关掉，这里仅在仍开着时才点，避免对 hidden 面板点击而超时
  if (await p2.locator('#sheet:not([hidden])').count()) {
    await p2.locator('#sheet .sheet-bg').click();
    await sleep(600);
  }
  chk('灯箱/月份面板/详情抽屉均已关闭',
    await p2.locator('#viewer[hidden]').count() === 1 &&
    await p2.locator('#sheet[hidden]').count() === 1 &&
    await p2.locator('#vDetail[hidden]').count() === 1);
  // 等待在途缩略图请求收尾，避免 ⓘ 的三个 API 调用被连接池排队饿死
  await p2.waitForFunction(() => {
    const r = performance.getEntriesByType('resource');
    const done = r.length;
    if (window.__lastN === done) { window.__idle = (window.__idle || 0) + 1; }
    else { window.__idle = 0; }
    window.__lastN = done;
    return window.__idle >= 6;           // 连续 6 次轮询资源数不再增长
  }, null, { timeout: 20000 }).catch(() => {});
  // 顶栏 ⓘ：灯箱关闭时也必须能弹出「服务与索引」（回归：曾因抽屉嵌在 hidden 灯箱内而不可见）
  const tInfo = Date.now();
  await p2.locator('#btnInfo').click();
  const infoVisible = await waitFor(async () =>
    await p2.locator('#vDetail:not([hidden])').count() === 1, 25000);
  console.log(`     [计时] 顶栏 ⓘ 面板显现 ${Date.now() - tInfo}ms`);
  const infoHead = infoVisible
    ? (await p2.locator('#vDetail .vd-head').innerText()).trim() : '';
  const infoRows = await p2.locator('#vDetail #vdList dt').count();
  chk('顶栏 ⓘ 可在灯箱关闭时弹出「服务与索引」',
    !!infoVisible && infoHead === '服务与索引', `${infoHead} / ${infoRows} 行`);
  await p2.screenshot({ path: path.join(OUT, '13-detail-info.png') });
  await p2.keyboard.press('Escape');
  await sleep(600);
  chk('Esc 可关闭详情抽屉', await p2.locator('#vDetail[hidden]').count() === 1);

  // 灯箱内详情：点 vInfoBtn，标题需还原为「详情」
  await p2.locator('.chip[data-lib="live"]').click();
  await sleep(2200);
  await p2.locator('.tile').first().click();
  await p2.waitForSelector('#viewer:not([hidden])', { timeout: 8000 });
  await sleep(1600);
  await p2.evaluate(() => document.querySelector('#vInfoBtn').click());
  const detVisible = await waitFor(async () =>
    await p2.locator('#vDetail:not([hidden])').count() === 1, 6000);
  const detHead = detVisible
    ? (await p2.locator('#vDetail .vd-head').innerText()).trim() : '';
  chk('灯箱内详情可打开且标题为「详情」', !!detVisible && detHead === '详情', detHead);
  await p2.keyboard.press('Escape');
  await sleep(500);
  chk('Esc 先关详情、灯箱仍开启',
    await p2.locator('#viewer:not([hidden])').count() === 1 &&
    await p2.locator('#vDetail[hidden]').count() === 1);
  await p2.keyboard.press('Escape');
  await sleep(700);
  chk('再按 Esc 关闭灯箱', await p2.locator('#viewer[hidden]').count() === 1);

  console.log('\n=== 8c. 索引进度面板（轻量，只读状态 + 轮询）===');
  chk('顶栏含「索引进度」入口', await p2.locator('#btnIdx').count() === 1);
  await p2.locator('#btnIdx').click();
  await p2.locator('#idxSheet:not([hidden])').waitFor({ timeout: 8000 });
  chk('索引进度面板已打开',
    await p2.locator('#idxSheet:not([hidden])').count() === 1);
  const idxStage = (await p2.locator('#idxStage').innerText()).trim();
  chk('面板显示当前阶段文案', idxStage.length > 0, idxStage.slice(0, 40));
  const idxKv = (await p2.locator('#idxKv').innerText()).trim();
  chk('完成汇总含 总照片数 / 视频数 / Live Photo',
    /总照片数/.test(idxKv) && /视频数/.test(idxKv) && /Live Photo/.test(idxKv),
    idxKv.replace(/\s+/g, ' ').slice(0, 60));
  const idxBarIdle = await p2.locator('#idxBarWrap').getAttribute('hidden');
  chk('空闲时不显示进度条（只在索引进行中才有）', idxBarIdle !== null);
  await p2.screenshot({ path: path.join(OUT, '19-idx-panel.png') });

  // 触发一次增量索引（非破坏性）→ 面板应给出「本次检查 / 新增 / 变更」
  await p2.locator('#btnIdxRescan').click();
  const incOk = await waitFor(async () => {
    const t = (await p2.locator('#idxInc').innerText()).trim();
    return /本次检查/.test(t) && /新增/.test(t) && /变更/.test(t) ? t : null;
  }, 20000);
  chk('增量索引给出「本次检查 / 新增 / 变更」',
    !!incOk, (incOk || '').replace(/\s+/g, ' ').slice(0, 60));
  // 计数阶段（总数未知）绝不出现百分比
  const sawCounting = await p2.evaluate(() =>
    (document.querySelector('#idxNums') || {}).textContent || '');
  if (/正在扫描图库，统计文件数量/.test(sawCounting)) {
    chk('总数未统计出时不含百分比', sawCounting.indexOf('%') < 0,
      sawCounting.slice(0, 46));
  } else {
    chk('总数未统计出时不含百分比', true, '（本次采样未落在计数阶段）');
  }
  await p2.locator('#btnIdxClose').click();
  await sleep(600);
  chk('可关闭索引进度面板',
    await p2.locator('#idxSheet[hidden]').count() === 1);

  console.log('\n=== 9. 运行期无 JS 异常 ===');
  chk('无未捕获错误', errs.length === 0, errs.slice(0, 3).join(' | ') || '0 条');

  console.log('\n' + '='.repeat(56));
  console.log(`  通过 ${pass.length} / ${pass.length + fail.length}`);
  if (fail.length) { console.log('  失败：'); fail.forEach(f => console.log('   - ' + f)); }
  console.log('='.repeat(56));

  await browser.close();
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('运行异常:', e); process.exit(2); });
