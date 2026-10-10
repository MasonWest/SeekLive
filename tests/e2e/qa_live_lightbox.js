/**
 * 灯箱「实况」回归：静图与动图必须同框 + 声音开关 + 三种条目都不报错
 * 用法： EXE=<Edge 路径> NODE_PATH=<playwright-core 所在目录> node tests/e2e/qa_live_lightbox.js
 *
 * 背景：手机实况视频常常只有 960x720，比视口小。视频若只受 max-width/max-height
 * 约束（max-* 只缩不放），就会停在自己的原始尺寸 —— 表现为「外面一圈静图不动、
 * 中间一小块在动」。测试同时注入「修复前」的规则做对照，确认能复现该现象。
 */
const { chromium } = require('playwright-core');
const path = require('path');
const fs = require('fs');

const EXE = process.env.EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const BASE = process.env.BASE || 'http://127.0.0.1:9000';
const SHOTS = path.join(__dirname, 'shots');
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 用户报过的那几张（2048x1536 横图）+ 一张竖图做对照 */
const TARGETS = ['IMG_2358.JPEG', 'IMG_2359.JPEG', 'IMG_2360.JPEG',
                 'IMG_2361.JPEG', 'IMG_2387.JPEG', 'IMG_2388.JPEG', 'IMG_1644.JPEG'];

const PROBE = `(() => {
  const M = document.getElementById('vMedia');
  const img = M.querySelector('.v-still img.v-hi');
  const v = M.querySelector('video.v-motion');
  const box = e => { const b = e.getBoundingClientRect();
    return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }; };
  // object-fit:contain 真正画出来的内容框 = 按原始比例缩放进元素框并居中
  const content = (e, iw, ih) => {
    const b = e.getBoundingClientRect();
    if (!iw || !ih) return box(e);
    const s = Math.min(b.width / iw, b.height / ih), w = iw * s, h = ih * s;
    return { x: Math.round(b.x + (b.width - w) / 2), y: Math.round(b.y + (b.height - h) / 2),
             w: Math.round(w), h: Math.round(h) };
  };
  return {
    still: img ? content(img, img.naturalWidth, img.naturalHeight) : null,
    video: v ? content(v, v.videoWidth, v.videoHeight) : null,
    stillNat: img ? img.naturalWidth + 'x' + img.naturalHeight : null,
    videoNat: v ? v.videoWidth + 'x' + v.videoHeight : null,
    playing: v ? !v.paused : false
  };
})()`;

const sameBox = (a, b) => !!a && !!b && ['x', 'y', 'w', 'h']
  .every(k => Math.abs(a[k] - b[k]) <= 2);

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const b = await chromium.launch({
    executablePath: fs.existsSync(EXE) ? EXE : undefined, headless: true,
    args: ['--autoplay-policy=no-user-gesture-required']
  });
  const ctx = await b.newContext({ viewport: { width: 1512, height: 900 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', e => errs.push('pageerror: ' + e.message));
  const fails = [];

  await p.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.tile', { timeout: 40000 });

  // 翻页收集样本，挂到 window.__pick（reload 后要重新收集）
  const collect = () => p.evaluate(async want => {
    const out = {};
    let off = 0;
    while (off < 6000) {
      const r = await api('/api/items', { lib: state.lib, off: off, lim: 600 });
      const items = r.items || [];
      for (const x of items) {
        const nm = String(x.p || '').split('/').pop();
        if (want.includes(nm)) out[nm] = x;
        if (x.k === 'vid' && !out.__vid) out.__vid = x;
        if (x.k === 'img' && !x.live && !out.__photo) out.__photo = x;
      }
      if (items.length < 600) break;
      off += 600;
    }
    window.__pick = out;
    return Object.keys(out);
  }, TARGETS);

  const pick = await collect();
  console.log('取到条目:', pick.length, '个；样本命中:', TARGETS.filter(n => pick.includes(n)).length, '/', TARGETS.length);

  /* ── 1. 静图 / 动图同框 ── */
  for (const n of TARGETS) {
    if (!pick.includes(n)) { console.log(`  ${n} 未在库中找到，跳过`); continue; }
    await p.evaluate(n => { openViewer([window.__pick[n]], 0); }, n);
    await sleep(2400);
    const m = await p.evaluate(PROBE);
    const ok = sameBox(m.still, m.video);
    if (!ok) fails.push(`${n} 静图与动图不同框`);
    console.log(`  ${n}  静图=${m.stillNat} 视频=${m.videoNat} 播放=${m.playing}  同框=${ok ? 'OK' : 'FAIL'}  ` +
                `静图${JSON.stringify(m.still)} 视频${JSON.stringify(m.video)}`);
    await p.screenshot({ path: path.join(SHOTS, `live_${n.replace('.JPEG', '')}.png`) });
  }

  /* ── 2. 对照：注入「修复前」的规则，确认能复现「大图套小图」 ── */
  await p.evaluate(() => { openViewer([window.__pick['IMG_2358.JPEG']], 0); });
  await sleep(2000);
  await p.addStyleTag({ content:
    '.v-media video.v-motion{width:auto!important;height:auto!important;' +
    'max-width:100%!important;max-height:100%!important;background:#111!important;}' });
  await sleep(1500);
  const old = await p.evaluate(PROBE);
  console.log(`  对照(修复前规则): 静图${JSON.stringify(old.still)} 视频${JSON.stringify(old.video)}` +
              `  -> 外圈比视频宽 ${old.still.w - old.video.w}px、高 ${old.still.h - old.video.h}px`);
  await p.screenshot({ path: path.join(SHOTS, 'live_before_fix.png') });

  /* ── 3. 声音开关（先 reload，清掉注入的对照样式） ── */
  await p.reload({ waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.tile', { timeout: 40000 });
  await collect();
  await p.evaluate(() => {
    const hit = Object.values(window.__pick).find(x => x && x.live && x.k === 'img');
    openViewer([hit], 0);
  });
  await sleep(2400);
  const silent = await p.evaluate(() => {
    const v = document.querySelector('video.v-motion');
    return { muted: v && v.muted, btn: !document.getElementById('vSound').hidden, on: document.getElementById('vSound').classList.contains('sound-on') };
  });
  await p.click('#vSound'); await sleep(1200);
  const loud = await p.evaluate(() => {
    const v = document.querySelector('video.v-motion');
    return { muted: v && v.muted, playing: v && !v.paused, on: document.getElementById('vSound').classList.contains('sound-on') };
  });
  await p.click('#vSound'); await sleep(1000);
  const back = await p.evaluate(() => {
    const v = document.querySelector('video.v-motion');
    return { muted: v && v.muted, on: document.getElementById('vSound').classList.contains('sound-on') };
  });
  console.log('  声音开关: 初始', JSON.stringify(silent), '-> 开声', JSON.stringify(loud), '-> 静音', JSON.stringify(back));
  if (!(silent.btn && silent.muted === true && loud.muted === false && loud.playing && back.muted === true)) {
    fails.push('声音开关状态不符合预期');
  }

  /* ── 4. 普通图 / 独立视频：按钮应隐藏、元素正确 ── */
  for (const [k, tag] of [['__photo', '普通图片'], ['__vid', '独立视频']]) {
    if (!pick.includes(k)) continue;
    await p.evaluate(k => { openViewer([window.__pick[k]], 0); }, k);
    await sleep(k === '__vid' ? 3000 : 1800);
    const r = await p.evaluate(() => ({
      still: !!document.querySelector('.v-still img.v-hi'),
      motion: !!document.querySelector('video.v-motion'),
      vid: !!document.querySelector('video.v-video'),
      liveHidden: document.getElementById('vLive').hidden,
      soundHidden: document.getElementById('vSound').hidden
    }));
    const ok = k === '__vid'
      ? (r.vid && r.liveHidden && r.soundHidden)
      : (r.still && !r.motion && r.liveHidden && r.soundHidden);
    if (!ok) fails.push(`${tag} 渲染异常`);
    console.log(`  ${tag}: ${JSON.stringify(r)}  ${ok ? 'OK' : 'FAIL'}`);
  }

  await p.click('#vClose').catch(() => {});
  await sleep(500);
  const closed = await p.evaluate(() => ({
    hidden: document.getElementById('viewer').hidden,
    left: document.getElementById('vMedia').children.length
  }));
  if (!closed.hidden || closed.left !== 0) fails.push('关闭灯箱未清空媒体层');
  console.log('  关闭灯箱:', JSON.stringify(closed));

  console.log('\n控制台报错:', errs.length ? errs : '（无）');
  if (errs.length) fails.push('控制台有报错');
  console.log('结果:', fails.length ? 'FAIL -> ' + fails.join('; ') : 'PASS');
  await b.close();
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('FAILED', e); process.exit(1); });
