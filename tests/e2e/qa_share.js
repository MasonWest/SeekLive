/**
 * 「访问控制 + 免登录分享」浏览器端回归（真 Edge + 真 HTTP 服务）
 *
 * 用法： NODE_PATH=<playwright-core 目录> node tests/e2e/qa_share.js
 * 可选： EXE=<Edge 路径>  PY=<python.exe 路径>  PORT=<夹具端口>
 *
 * 特点：**自带夹具服务**（tests/fixture_server.py）—— 一套临时的假图库 +
 * 临时 shares.db，不碰真实照片目录、不写真实 data/shares.db。
 *
 * 覆盖：
 *   1) 未授权访客：只有壳，看不到任何照片；数据接口 401
 *   2) 管理员：口令授权后一切正常（管理入口可见）
 *   3) 整月「全选本月」→ 生成分享链接
 *   3b) 日期标题后的选择框：整日选中 / 手动取消一张变半选 / 补齐 / 整天取消，
 *       且只在选择模式出现
 *   4) 分享访客（全新无 Cookie 上下文）：只见授权集合、管理入口全隐藏、
 *      未授权照片 403、分享媒体带 private, no-store
 *   5) 撤销后：分享页立即失效（先直接问服务端，再刷新页面；含 keep-alive
 *      连接上的后续请求也必须重新判定身份，不得沿用旧结论）
 *   6) 全程控制台 0 报错
 */
const { chromium } = require('playwright-core');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const APP = path.resolve(__dirname, '..', '..');
const EXE = process.env.EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
// 默认用 0 = 让操作系统分配空闲端口，彻底排除「上一次残留的夹具服务占着固定端口」
// 造成的假象。（夹具会把真实端口打印成 READY <url>，父进程据此连。）
const PORT = parseInt(process.env.PORT || '0', 10);
const TOKEN = 'test-pw-123';
const PY = process.env.PY || (fs.existsSync(
  'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe')
  ? 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe'
  : 'python');

const sleep = ms => new Promise(r => setTimeout(r, ms));
let fail = 0;
const ok = (cond, msg, extra) => {
  console.log(`  ${cond ? '✓' : '✗'} ${msg}${extra !== undefined ? `   [${extra}]` : ''}`);
  if (!cond) fail++;
};
const sect = t => console.log(`\n=== ${t} ===`);

function startFixture() {
  return new Promise((resolve, reject) => {
    const child = spawn(PY, ['tests/fixture_server.py', '--port', String(PORT),
      '--token', TOKEN], { cwd: APP });
    let buf = '';
    const to = setTimeout(() => reject(new Error('夹具服务启动超时:\n' + buf)), 30000);
    child.stdout.on('data', d => {
      buf += d.toString();
      const m = buf.match(/READY (\S+)/);
      if (m) { clearTimeout(to); resolve({ child, base: m[1].trim() }); }
    });
    child.stderr.on('data', d => { buf += d.toString(); });
    child.on('exit', c => { clearTimeout(to); reject(new Error('夹具退出 code=' + c + '\n' + buf)); });
  });
}

const status = (page, url) => page.evaluate(async (u) => {
  const r = await fetch(u);
  return { status: r.status, cc: r.headers.get('cache-control') || '' };
}, url);

const visible = (page, sel) => page.evaluate(
  s => { const el = document.querySelector(s); return !!el && !el.hidden; }, sel);

(async () => {
  let fx = null, browser = null;
  try {
    console.log('='.repeat(66));
    console.log('  SeekLive · 访问控制 / 分享 —— 浏览器回归（夹具隔离，不碰真实图库）');
    console.log('='.repeat(66));
    fx = await startFixture();
    const BASE = fx.base;
    console.log(`  夹具服务已就绪：${BASE}`);

    browser = await chromium.launch({ executablePath: EXE, headless: true });
    const errs = [];
    // 只关心真正的脚本错误；资源加载失败（本脚本自己用 fetch 探测 401/403 会造成）
    // 不计入 —— 那不是页面 bug。
    const onErr = (tag, m) => {
      if (m.type() !== 'error') return;
      const t = m.text();
      if (/Failed to load resource/i.test(t)) return;
      errs.push(`${tag}: ${t}`);
    };

    /* ---------------- 1) 未授权访客 ---------------- */
    sect('1 · 未授权访客：只有壳，看不到任何照片');
    const anonCtx = await browser.newContext({ viewport: { width: 420, height: 880 } });
    const anon = await anonCtx.newPage();
    anon.on('console', m => onErr('anon', m));
    anon.on('pageerror', e => errs.push('anon PAGEERROR ' + e.message));
    await anon.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await anon.waitForSelector('#splashSub', { timeout: 15000 });
    await sleep(700);
    const gateText = await anon.textContent('#splashSub');
    ok(/访问保护/.test(gateText), '未授权访客看到「访问保护」说明页', gateText.replace(/\s+/g, ' ').trim());
    ok(await visible(anon, '#gateLogin'), '「输入管理口令」入口可见');
    ok((await anon.$$('.tile')).length === 0, '未授权访客页面上没有任何照片格子');
    ok((await status(anon, '/api/libs')).status === 401, '未授权 /api/libs → 401');
    ok((await status(anon, '/api/tree?lib=t')).status === 401, '未授权 /api/tree → 401');
    ok((await status(anon, '/raw?lib=t&p=' + encodeURIComponent('2024-09/IMG_0001.JPG'))).status === 401,
      '未授权取原图 → 401');
    const anonSess = await anon.evaluate(async () => (await (await fetch('/api/session')).json()).scope);
    ok(anonSess === 'anon', '/api/session → scope=anon', anonSess);

    /* ---------------- 2) 管理员：授权后一切正常 ---------------- */
    sect('2 · 管理员：口令授权后原功能可用');
    await anon.click('#gateLogin');
    await anon.waitForSelector('#aaPw', { timeout: 8000 });
    await anon.fill('#aaPw', TOKEN);
    await anon.click('#cfmYes');
    await anon.waitForSelector('.day-blk', { timeout: 25000 });
    await sleep(500);
    ok(true, '口令授权成功并载入时间轴');
    ok((await anon.$$('.tile')).length > 0, '管理员能看到照片', (await anon.$$('.tile')).length);
    ok(await visible(anon, '#btnSelect'), '管理入口「选择」可见');
    ok(await visible(anon, '#btnTrash'), '管理入口「最近删除」可见');
    const adminSess = await anon.evaluate(async () => (await (await fetch('/api/session')).json()).scope);
    ok(adminSess === 'admin', '/api/session → scope=admin', adminSess);

    /* ---------------- 3) 整月全选 → 生成分享 ---------------- */
    sect('3 · 整月「全选本月」→ 生成分享链接');
    await anon.click('#btnSelect');
    await anon.waitForSelector('#selBar:not([hidden])', { timeout: 6000 });
    ok(await visible(anon, '#selMonth'), '「全选本月」按钮可见');
    await anon.click('#selMonth');
    await anon.waitForFunction(
      () => document.querySelector('#selCount').textContent !== '已选 0 项',
      null, { timeout: 15000 });
    const selText = await anon.textContent('#selCount');
    ok(/已选\s*3\s*项/.test(selText), '一键选中本月全部 3 项', selText);

    await anon.click('#selShare');
    await anon.waitForSelector('#shTitle', { timeout: 8000 });
    await anon.fill('#shTitle', 'E2E 分享');
    await anon.click('#cfmYes');
    await anon.waitForSelector('#shUrl', { timeout: 12000 });
    const shareUrl = (await anon.textContent('#shUrl')).trim();
    ok(/^https?:\/\/.+\/s\/[A-Za-z0-9_-]{16,}$/.test(shareUrl), '生成了高随机令牌的分享链接', shareUrl);
    await anon.click('#cfmYes');                       // 关闭「已生成」对话框
    await sleep(300);

    /* ---------------- 3b) 日期标题后的选择框 ---------------- */
    sect('3b · 日期标题后的选择框：整日选中 / 半选 / 取消');
    const dayPickVisible = () => anon.locator('.day-blk .d-pick').first().isVisible();
    ok(!(await dayPickVisible()), '浏览模式下日期选择框不出现（避免误触）');

    await anon.click('#btnSelect');
    await anon.waitForSelector('#selBar:not([hidden])', { timeout: 6000 });
    ok(await dayPickVisible(), '进入选择模式后，日期标题后出现选择框');
    ok(await visible(anon, '#selMonth'), '「全选本月」仍在（两个入口共存）');

    // 夹具里最老的那一天有 2 张，用它验证「半选」
    const lastDay = anon.locator('.day-blk').last();
    await lastDay.scrollIntoViewIfNeeded();
    await anon.waitForFunction(() => {
      const all = document.querySelectorAll('.day-blk');
      const last = all[all.length - 1];
      return !!last && last.querySelectorAll('.tile:not(.sk)').length >= 2;
    }, null, { timeout: 15000 });
    const dayTotal = +(await lastDay.getAttribute('data-total'));
    ok(dayTotal === 2, '夹具最老一天有 2 张（用于验证半选）', 'data-total=' + dayTotal);

    const dayCb = lastDay.locator('.d-pick-cb');
    await dayCb.click();                                  // ① 整日选中
    await anon.waitForFunction(
      () => /已选\s*2\s*项/.test(document.querySelector('#selCount').textContent),
      null, { timeout: 12000 });
    ok(await dayCb.isChecked(), '勾选日期选择框 → 这一天 2 张全部选中');
    ok((await lastDay.locator('.tile.sel').count()) === 2, '这一天 2 张都高亮了');

    await lastDay.locator('.tile').first().click();        // ② 取消一张 → 半选
    await anon.waitForFunction(
      () => /已选\s*1\s*项/.test(document.querySelector('#selCount').textContent),
      null, { timeout: 10000 });
    ok((await dayCb.evaluate(el => el.indeterminate)) === true,
      '手动取消一张后，日期选择框变为「半选」（indeterminate）');

    await dayCb.click();                                  // ③ 半选点一下 → 补齐
    await anon.waitForFunction(
      () => /已选\s*2\s*项/.test(document.querySelector('#selCount').textContent),
      null, { timeout: 10000 });
    ok(await dayCb.isChecked(), '半选状态再点一次 → 补齐为整日全选');

    await dayCb.click();                                  // ④ 再点一下 → 取消整天
    await anon.waitForFunction(
      () => /已选\s*0\s*项/.test(document.querySelector('#selCount').textContent),
      null, { timeout: 10000 });
    ok(!(await dayCb.isChecked()) && !(await dayCb.evaluate(el => el.indeterminate)),
      '再点一次 → 整天取消，回到未选');

    await anon.click('#selCancel');
    await anon.waitForFunction(
      () => document.querySelector('#selBar').hidden, null, { timeout: 5000 });
    ok(!(await dayPickVisible()), '退出选择模式后日期选择框隐藏');

    /* ---------------- 4) 分享访客 ---------------- */
    sect('4 · 分享访客（全新无 Cookie）：只见授权集合');
    const shCtx = await browser.newContext({ viewport: { width: 420, height: 880 } });
    const sh = await shCtx.newPage();
    sh.on('console', m => onErr('share', m));
    sh.on('pageerror', e => errs.push('share PAGEERROR ' + e.message));
    await sh.goto(shareUrl, { waitUntil: 'domcontentloaded' });
    await sh.waitForSelector('.day-blk', { timeout: 25000 });
    await sleep(500);
    ok(await visible(sh, '#shareBar'), '分享模式提示条可见',
      (await sh.textContent('#shareBarText')).slice(0, 40));
    ok(/E2E 分享/.test(await sh.textContent('#shareBarText')), '提示条显示分享标题');
    ok(!(await visible(sh, '#btnSelect')), '分享访客看不到「选择/删除」入口');
    ok(!(await visible(sh, '#btnTrash')), '分享访客看不到「最近删除」入口');
    ok(!(await visible(sh, '#btnIdx')), '分享访客看不到「索引进度」入口');
    ok(!(await visible(sh, '#btnShareManage')), '分享访客看不到「分享管理」入口');

    const shSess = await sh.evaluate(async () => (await (await fetch('/api/session')).json()));
    ok(shSess.scope === 'share' && shSess.share && shSess.share.count === 3,
      '/api/session → scope=share 且 count=3', JSON.stringify(shSess.share || {}));

    const shItems = await sh.evaluate(async () =>
      (await (await fetch('/api/items?lib=t&lim=600')).json()));
    ok(shItems.total === 3, '分享访客只能取到 3 项', 'total=' + shItems.total);

    const allowedRel = shItems.items[0].p;
    const rAllowed = await status(sh, '/raw?lib=t&p=' + encodeURIComponent(allowedRel));
    ok(rAllowed.status === 200, '授权照片可读（' + allowedRel + '）', 'HTTP ' + rAllowed.status);
    ok(/private/.test(rAllowed.cc) && /no-store/.test(rAllowed.cc),
      '分享媒体带 Cache-Control: private, no-store', rAllowed.cc);

    const rDenied = await status(sh, '/raw?lib=t&p=' + encodeURIComponent('2024-09/IMG_0001.JPG'));
    ok(rDenied.status === 403, '未授权照片 → 403', 'HTTP ' + rDenied.status);
    ok((await status(sh, '/api/stats')).status === 403, '分享访客取统计 → 403');
    ok((await status(sh, '/api/trash')).status === 403, '分享访客取回收站 → 403');
    ok((await status(sh, '/api/cache/gc?apply=1')).status === 403, '分享访客触发缓存回收 → 403');

    /* ---------------- 5) 撤销后立即失效 ---------------- */
    sect('5 · 撤销后：分享页与媒体请求立即失效');
    await anon.click('#btnMenu');
    await anon.waitForSelector('#sheet:not([hidden])', { timeout: 6000 });
    await anon.click('#btnShareManage');
    await anon.waitForSelector('.sh-item', { timeout: 10000 });
    ok((await anon.$$('.sh-item')).length === 1, '分享管理列表里有 1 条', (await anon.$$('.sh-item')).length);
    await anon.click('.sh-revoke');
    await anon.waitForSelector('#cfmYes', { timeout: 6000 });
    await anon.click('#cfmYes');
    await anon.waitForFunction(
      () => /还没有分享/.test(document.querySelector('#shareList').textContent),
      null, { timeout: 10000 });
    ok(true, '撤销成功，列表回到空态');

    // —— 先在**刷新之前**直接问服务端：令牌是否已判失效 ——
    //    这一步能精确区分「服务端仍在放行」与「只是页面没刷新」，也顺带覆盖
    //    keep-alive：同一连接上的后续请求必须重新判定，不得沿用旧身份。
    const afterRevoke = await sh.evaluate(async () => {
      const s = await (await fetch('/api/session', { cache: 'no-store' })).json();
      const raw = await fetch('/raw?lib=t&p=' +
        encodeURIComponent('2024-09/IMG_0001.JPG'));
      return { scope: s.scope, raw: raw.status };
    });
    ok(afterRevoke.scope === 'anon' && afterRevoke.raw === 401,
      '撤销后服务端立即改判 anon（刷新前）', JSON.stringify(afterRevoke));

    await sh.reload({ waitUntil: 'domcontentloaded' });
    await sh.waitForSelector('#splashSub', { timeout: 15000 });
    await sleep(900);
    ok(/已失效/.test(await sh.textContent('#splashSub')), '分享访客刷新后看到「链接已失效」',
      (await sh.textContent('#splashSub')).replace(/\s+/g, ' ').trim());
    ok((await sh.$$('.tile')).length === 0, '失效后页面上不再有任何照片');
    ok((await status(sh, '/raw?lib=t&p=' + encodeURIComponent(allowedRel))).status === 401,
      '失效后取原图 → 401');

    /* ---------------- 6) 控制台 ---------------- */
    sect('6 · 控制台');
    ok(errs.length === 0, '全程 0 条控制台错误', errs.slice(0, 3).join(' | '));

    await anonCtx.close();
    await shCtx.close();
  } catch (e) {
    console.error('\n[异常]', e && e.message ? e.message : e);
    fail++;
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (fx && fx.child) { try { fx.child.kill(); } catch (e) {} }
  }

  console.log('\n' + '='.repeat(66));
  console.log(fail ? `  失败 ${fail} 项` : '  全部通过 ✅');
  console.log('='.repeat(66));
  process.exit(fail ? 1 : 0);
})();
