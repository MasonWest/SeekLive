#!/usr/bin/env node
/**
 * 照片墙「五档密度 + 时间浏览层级」· 静态守卫（无需浏览器）
 * ======================================================
 * 本机没有常驻 Playwright，浏览器验收脚本（tmp/qa_density.js）需要真实服务 +
 * 管理口令；这个零依赖守卫把「五档 / 分层 / 单档契约」这三件容易悄悄退化的事
 * 钉死在源码层面，任何一次改动都能在没有浏览器的机器上先跑一遍。

 * 断言：
 *   1. 恰好五档，名称与时间分组正确（day/day/day/month/year）
 *   2. 目标宽度严格递减（越靠后越密）；不存在旧的 DENSITY_STEPS 三档
 *   3. computeCols 写 --cols 并切换 grp-day/month/year
 *   4. 分组只靠 CSS class：年份分隔时刻在 DOM 里，可见性由 app.css 决定
 *   5. setDensity 只改样式：不发起任何网络请求、不改图片 URL
 *   6. 切换前后浏览位置由锚点续接（topVisibleBlock + scrollBy）
 *   7. index.html / app.css 的控制器结构与样式齐全，且选择模式下让位
 *   8. 单档 640 契约不因密度功能回归（THUMB_SIZE / 无 srcset）
 *
 * 运行：node tests/check_frontend_density.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = join(HERE, '..');
const js = readFileSync(join(APP, 'web', 'app.js'), 'utf8');
const html = readFileSync(join(APP, 'web', 'index.html'), 'utf8');
const css = readFileSync(join(APP, 'web', 'app.css'), 'utf8');

let pass = 0, fail = 0;
const chk = (name, ok, detail = '') => {
  if (ok) { pass++; console.log(`  OK  ${name}` + (detail ? `   [${detail}]` : '')); }
  else { fail++; console.log(`  FAIL ${name}` + (detail ? `   [${detail}]` : '')); }
};
const body = (fn) => {
  const i = js.indexOf('function ' + fn + '(');
  if (i < 0) return '';
  const j = js.indexOf('\n}', i);
  return j < 0 ? '' : js.slice(i, j);
};

console.log('='.repeat(66));
console.log('  照片墙五档密度 · 静态守卫');
console.log('='.repeat(66));

/* 1. 五档定义 */
const block = js.match(/const\s+DENSITY_LEVELS\s*=\s*\[([\s\S]*?)\];/);
const rows = block ? block[1].split('\n').map(l => l.trim()).filter(l => l.startsWith('{')) : [];
chk('DENSITY_LEVELS 恰好 5 档', rows.length === 5, `实际 ${rows.length}`);
const names = rows.map(r => (r.match(/name:\s*'([^']+)'/) || [])[1]);
chk('档位名称 = 大图/标准/密集/月份/年份',
  JSON.stringify(names) === JSON.stringify(['大图', '标准', '密集', '月份', '年份']),
  JSON.stringify(names));
const groups = rows.map(r => (r.match(/group:\s*'([^']+)'/) || [])[1]);
chk('时间分组 = day/day/day/month/year',
  JSON.stringify(groups) === JSON.stringify(['day', 'day', 'day', 'month', 'year']),
  JSON.stringify(groups));
const targets = rows.map(r => parseFloat((r.match(/target:\s*([\d.]+)/) || [])[1]));
chk('目标宽度严格递减（越靠后越密）',
  targets.every((v, i) => i === 0 || v < targets[i - 1]), JSON.stringify(targets));
chk('不存在旧的三档 DENSITY_STEPS', !/DENSITY_STEPS/.test(js));

/* 2. 列数 / 分组 class */
const cc = body('computeCols');
chk('computeCols 写 --cols', /setProperty\(\s*'--cols'/.test(cc));
chk('computeCols 切换 grp-day / grp-month / grp-year',
  /'grp-'\s*\+\s*lv\.group/.test(cc) && /remove\(\s*'grp-day',\s*'grp-month',\s*'grp-year'/.test(cc));
chk('列数按 target 换算并做 min/max 钳制',
  /colsForLevel\(/.test(cc) && /function\s+colsForLevel/.test(js) &&
  /Math\.max\(lv\.min,\s*Math\.min\(lv\.max/.test(body('colsForLevel')));

/* 3. 年份分隔常驻 DOM，可见性交给 CSS */
const rnb = js.slice(js.indexOf('function renderNextBatch('));
chk('renderNextBatch 生成 .year-sep 年份分隔',
  /className\s*=\s*'year-sep'/.test(rnb.slice(0, 2600)));
const yearsep = css.match(/\.year-sep\{[\s\S]*?\}/);
chk('app.css 里 .year-sep 默认 display:none', !!yearsep && /display:none/.test(yearsep[0]));
chk('app.css 里 grp-year 才显示年份分隔',
  /html\.grp-year\s+\.year-sep\{[^}]*display:block/.test(css));
chk('app.css 里 grp-month / grp-year 收起日期头',
  /html\.grp-month\s+\.day-hd,\s*html\.grp-year\s+\.day-hd\{[^}]*display:none/.test(css));
chk('app.css 里年份档把月份分隔降级为小标签',
  /html\.grp-year\s+\.month-sep\{/.test(css));

/* 4. 切档不触网、不改图片 URL */
const sd = body('setDensity');
chk('setDensity 不发起任何请求', sd && !/fetch\(|api\(|thumbURL\(|\/thumb/.test(sd), sd ? '' : '未找到 setDensity');
chk('setDensity 只改样式：computeCols + sizeSkeletons',
  /computeCols\(\)/.test(sd) && /sizeSkeletons\(/.test(sd));
chk('setDensity 重算预加载余量（rearmObserver）', /rearmObserver\(\)/.test(sd));
chk('setDensity 记住选择（localStorage）', /saveDensity\(/.test(sd));
chk('localStorage 读写都有 try/catch 兜底',
  /function\s+loadDensity/.test(js) && /catch/.test(body('loadDensity')) &&
  /catch/.test(body('saveDensity')));

/* 5. 位置保持 */
chk('切档前记录锚点分组', /topVisibleBlock\(\)/.test(sd));
chk('切档后按锚点回正（scrollBy）', /window\.scrollBy\(\s*0,/.test(sd));
chk('锚点 = 视口内最上面的已渲染日期分组',
  /#timeline\s+\.day-blk/.test(body('topVisibleBlock')));

/* 6. 切档动画（可降级） */
chk('切档有 FLIP 形变（不是硬跳）', /function\s+flipTiles/.test(js) && /flipTiles\(probes/.test(sd));
chk('尊重 prefers-reduced-motion', /prefers-reduced-motion/.test(body('prefersReduce')));
chk('FLIP 前摘掉 .tile 的进场动画（否则内联 transform 会被动画吃掉）',
  /style\.animation\s*=\s*'none'/.test(body('flipTiles')));

/* 7. 控制器结构 / 样式 */
for (const id of ['denCtl', 'denOut', 'denMid', 'denIn', 'denDots', 'denSheet', 'denList']) {
  chk(`index.html 存在 #${id}`, new RegExp(`id="${id}"`).test(html));
}
chk('app.css 控制器固定居中（left:50% + translateX(-50%)）',
  /\.den-ctl\{[\s\S]*?left:50%/.test(css) && /translateX\(-50%\)/.test(css));
chk('控制器入场动画自带居中 transform（不得用通用 dropIn 覆盖）',
  /@keyframes\s+denIn\{[\s\S]*?translateX\(-50%\)[\s\S]*?\}/.test(css) &&
  /\.den-ctl\{[\s\S]*?animation:denIn/.test(css));
chk('触控区域 ≥ 40px', /\.den-btn\{[\s\S]*?width:4[0-9]px;height:4[0-9]px/.test(css));
chk('选择模式下控制器让位', /body\.selecting\s+\.den-ctl\{display:none\}/.test(css));
chk('工具栏密度按钮复用同一个五档面板', /#btnDensity'\)\.onclick\s*=\s*openDenSheet/.test(js));
chk('面板列出 5 个档位且显示实际列数',
  /DENSITY_LEVELS\.forEach/.test(body('buildDenSheet')) && /colsForLevel\(lv\)/.test(body('buildDenSheet')));

/* 8. 单档 640 契约不回归 */
chk('THUMB_SIZE 仍为 640', /const\s+THUMB_SIZE\s*=\s*640\s*;/.test(js));
chk('仍然不输出 srcset', !/srcset\s*=/.test(js));

console.log('-'.repeat(66));
console.log(`  通过 ${pass} / 失败 ${fail}`);
console.log('='.repeat(66));
process.exit(fail ? 1 : 0);
