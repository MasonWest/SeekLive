/**
 * 「删除/恢复后保持浏览位置」回归
 * 用法： EXE=<Edge 路径> NODE_PATH=<playwright-core 所在目录> node tests/e2e/qa_keep_scroll.js
 *
 * 背景：时间轴是「滚到哪渲染到哪」的懒加载（renderNextBatch 一批 20 个日期分组）。
 * 删除后 refreshAfterChange() 会整段重建，而 selectLib() 结尾固定 window.scrollTo(0,0)，
 * 于是用户被抛回顶部 —— 顶部永远是最新的月份，体感就像「跳到 25 年某月去了」。
 *
 * 本脚本**不删任何真实照片**：它直接调用删除后要走的同一条重建路径
 * refreshAfterChange()，比较重建前后「视口顶部的日期分组」是否一致。
 */
const { chromium } = require('playwright-core');

const EXE = process.env.EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const BASE = process.env.BASE || 'http://127.0.0.1:9000';
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 与 app.js 的 captureViewAnchor 同口径：取第一个尚未完全滚出导航栏下方的分组 */
const PROBE = `(() => {
  const tb = document.getElementById('topbar');
  const rail = document.getElementById('rail');
  const off = (tb ? tb.offsetHeight : 0) + (rail && !rail.hidden ? rail.offsetHeight : 0);
  let key = null, rel = null;
  for (const el of document.querySelectorAll('.day-blk')) {
    const r = el.getBoundingClientRect();
    if (r.bottom > off + 4) { key = el.dataset.key; rel = Math.round(r.top - off); break; }
  }
  return { key: key, rel: rel, scrollY: Math.round(window.scrollY),
           rendered: document.querySelectorAll('.day-blk').length };
})()`;

const CANDIDATES = ['2024-05', '2024-06', '2024-04', '2024-03', '2023-11', '2024-07'];

(async () => {
  let fail = 0;
  const ok = (cond, msg) => { console.log(`  ${cond ? '✓' : '✗'} ${msg}`); if (!cond) fail++; };

  const b = await chromium.launch({ executablePath: EXE, headless: true });
  const p = await b.newPage({ viewport: { width: 1280, height: 860 } });
  const errs = [];
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', e => errs.push('PAGEERROR ' + e.message));

  await p.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.day-blk', { timeout: 40000 });
  await sleep(1200);

  const months = await p.evaluate(() => (state.tree.months || []).map(m => m.ym));
  const target = CANDIDATES.find(m => months.indexOf(m) >= 0);
  const topMonth = months[0];
  console.log(`库内顶部月 = ${topMonth}；选定目标月 = ${target || '(无)'}`);
  if (!target) { console.log('!! 库里没有可用于验证的历史月份，跳过'); await b.close(); process.exit(1); }

  /* ── 1. 滚到某个历史日期，走一次「删除后重建」 ── */
  console.log('\n[1] 删除后重建（主界面）');
  await p.evaluate(ym => jumpToMonth(ym), target);
  await sleep(2500);
  const before = await p.evaluate(PROBE);
  console.log(`    重建前: 顶部 ${before.key} / 偏移 ${before.rel}px / scrollY ${before.scrollY}`);
  await p.evaluate(async () => { await refreshAfterChange(); });
  await sleep(2200);
  const after = await p.evaluate(PROBE);
  console.log(`    重建后: 顶部 ${after.key} / 偏移 ${after.rel}px / scrollY ${after.scrollY}`);
  ok(before.key === after.key, `顶部日期保持为 ${before.key}（实际 ${after.key}）`);
  ok(Math.abs(after.rel - before.rel) <= 12, `组内偏移基本不变（${before.rel} → ${after.rel}px）`);
  ok(String(after.key).slice(0, 7) === target, `仍停留在目标月 ${target}`);
  ok(String(after.key).slice(0, 7) !== topMonth, `没有跳回顶部月 ${topMonth}`);

  /* ── 2. 灯箱里删图：关灯箱后重建 ── */
  console.log('\n[2] 删除后重建（灯箱关闭路径）');
  const opened = await p.evaluate(async ym => {
    const r = await api('/api/items', { lib: state.lib, ym: ym, lim: 40 });
    const items = (r.items || []);
    if (!items.length) return null;
    openViewer(items, 0);
    return { p: items[0].p, ts: items[0].t };
  }, target);
  ok(!!opened, `灯箱成功打开 ${opened ? opened.p : ''}`);
  await sleep(900);
  await p.evaluate(async () => { closeViewer(); await refreshAfterChange(); });
  await sleep(2200);
  const a2 = await p.evaluate(PROBE);
  console.log(`    重建后: 顶部 ${a2.key} / scrollY ${a2.scrollY}`);
  ok(String(a2.key).slice(0, 7) === target, `仍在目标月 ${target}（实际 ${a2.key}）`);

  /* ── 3. 回归：无 anchor 的常规选择仍然回顶部 ── */
  console.log('\n[3] 回归 · 常规选库应回顶部');
  await p.evaluate(async () => { state.lib = null; await selectLib('photos', true); });
  await sleep(1400);
  const a3 = await p.evaluate(PROBE);
  console.log(`    scrollY = ${a3.scrollY} / 顶部 ${a3.key}`);
  ok(a3.scrollY <= 5, '普通选库仍回到顶部');
  ok(String(a3.key).slice(0, 7) === topMonth, `顶部是最新月 ${topMonth}`);

  /* ── 4. 控制台干净 ── */
  console.log('');
  ok(errs.length === 0, `控制台无报错（${errs.length}）`);
  if (errs.length) console.log('   ', errs.slice(0, 3));

  await b.close();
  console.log(fail ? `\n结果：${fail} 项未通过` : '\n结果：全部通过');
  process.exit(fail ? 1 : 0);
})();
