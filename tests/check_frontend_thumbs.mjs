#!/usr/bin/env node
/**
 * 前端缩略图「节流 / 让路」· 静态守卫（无需浏览器）
 * ================================================
 * 背景：浏览器对同一个源只有 ~6 条并发连接。缓存被清空后快滚，几百个 <img>
 * 会同时被派发，连接池排到几百名之后 —— 用户点开大图，/photo 只能排在队尾，
 * 真机实测等了 18.0s（而缩略图本身只要 0.5s）。
 *
 * 本守卫锁定修复后的契约（web/app.js）：
 *   1. 有节流常量 THUMB_MAX，且是有意义的小数字
 *   2. 网格里的 <img> **不在 HTML 里写 src**，改由节流器按视口派发
 *   3. 存在观察器（离视口够近才入队）与队列/在传集合
 *   4. 灯箱打开 → 挂起并撤回在途缩略图；关闭 → 恢复
 *   5. 邻居大图预取只从当前大图 onload 之后触发，且只预取一张
 *   6. 失败重试也走节流队列（否则一批失败会瞬间再打几百个请求）
 *   7. 时间轴整体重建时释放队列与观察器
 *
 * 运行：node tests/check_frontend_thumbs.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = join(HERE, '..');
const src = readFileSync(join(APP, 'web', 'app.js'), 'utf8');

let pass = 0, fail = 0;
const chk = (name, ok, detail = '') => {
  if (ok) { pass++; console.log(`  OK  ${name}` + (detail ? `   [${detail}]` : '')); }
  else { fail++; console.log(`  FAIL ${name}` + (detail ? `   [${detail}]` : '')); }
};

console.log('='.repeat(66));
console.log('  前端缩略图节流 · 静态守卫（web/app.js）');
console.log('='.repeat(66));

// 1. 节流常量
const mMax = src.match(/const\s+THUMB_MAX\s*=\s*(\d+)\s*;/);
chk('定义 THUMB_MAX', !!mMax, mMax ? mMax[0] : '未找到');
chk('THUMB_MAX 是小数字（1~8），不超过浏览器单源连接数', !!mMax && +mMax[1] >= 1 && +mMax[1] <= 8);
const mAhead = src.match(/const\s+THUMB_AHEAD\s*=\s*(\d+)\s*;/);
chk('定义 THUMB_AHEAD（视口外预取余量）', !!mAhead, mAhead ? mAhead[0] : '未找到');

// 2. 网格不再直接把 src 写进 HTML
const paint = src.match(/function\s+paintGrid\s*\([\s\S]*?\n\}/);
chk('找到 paintGrid', !!paint);
const paintSrc = paint ? paint[0] : '';
chk('paintGrid 的 <img> 不再带 src= 属性',
  !/<img[^>]*\$\{thumbURL\(/.test(paintSrc) && !/`<img[^>]*src=/i.test(paintSrc));
chk('paintGrid 设置 data-thumb-url（交给节流器）',
  /dataset\.thumbUrl\s*=\s*thumbURL\(/.test(paintSrc));
chk('paintGrid 把条目挂在 tile 上（供失败重试）', /a\.item\s*=\s*it/.test(paintSrc));
chk('paintGrid 把图片交给观察器', /thumbObserver\.observe\(/.test(paintSrc));

const paintSearch = src.match(/function\s+paintSearch\s*\([\s\S]*?\n\}/);
chk('搜索结果同样走节流器',
  !!paintSearch && /dataset\.thumbUrl\s*=\s*thumbURL\(/.test(paintSearch[0])
  && !/<img[^>]*\$\{thumbURL\(/.test(paintSearch[0]));

// 3. 节流器本体
chk('存在 IntersectionObserver 驱动的入队', /thumbObserver\s*=\s*new IntersectionObserver\(/.test(src));
chk('观察器带 rootMargin（用 THUMB_AHEAD）', /rootMargin:\s*`\$\{THUMB_AHEAD\}px 0px`/.test(src));
chk('有在传集合 thumbLive', /const\s+thumbLive\s*=\s*new Set\(\)/.test(src));
chk('有等待集合 thumbWaiting', /const\s+thumbWaiting\s*=\s*new Set\(\)/.test(src));
chk('有队列 thumbQueue', /const\s+thumbQueue\s*=\s*\[\]/.test(src));
chk('派发受 THUMB_MAX 限制',
  /while\s*\(\s*thumbLive\.size\s*<\s*THUMB_MAX\s*&&\s*thumbQueue\.length\s*\)/.test(src));
chk('thumbStart 里才写 src', /img\.src\s*=\s*img\.dataset\.thumbUrl/.test(src));
chk('load 后才加 .loaded 并 fitTile',
  /tile\.classList\.add\('loaded'\)[\s\S]{0,40}fitTile\(img\)/.test(src));

// 4. 灯箱打开 → 挂起并撤回
chk('有 thumbHold 挂起函数', /function\s+thumbHold\s*\(/.test(src));
chk('挂起时真的取消在途请求',
  /removeAttribute\('src'\)/.test(src)
  && /if\s*\(!img\.isConnected\)\s*return;[\s\S]{0,90}removeAttribute\('src'\)/.test(src));
const openV = src.match(/function\s+openViewer\s*\([\s\S]*?\n\}/);
chk('openViewer 里挂起缩略图', !!openV && /thumbHold\(true\)/.test(openV[0]));
const closeV = src.match(/function\s+closeViewer\s*\([\s\S]*?\n\}/);
chk('closeViewer 里恢复缩略图', !!closeV && /thumbHold\(false\)/.test(closeV[0]));

// 5. 邻居预取：等当前大图出来之后再取，且只取一张
const pre = src.match(/function\s+preloadNeighbors\s*\([\s\S]*?\n\}/);
chk('preloadNeighbors 只预取一张', !!pre && !/\[1,\s*-1\]/.test(pre[0]));
chk('preloadNeighbors 不再在 showSlide 里同步调用',
  !/vMedia\.appendChild\(wrap\);\s*\n\s*if \(it\.live\) attachLive\(it\);\s*\n\s*preloadNeighbors\(\)/.test(src));
chk('邻居预取挂在当前大图 onload 之后',
  /hi\.onload\s*=\s*\(\)\s*=>\s*\{[\s\S]{0,180}preloadNeighbors\(\)/.test(src));

// 6. 重试走队列
const fb = src.match(/function\s+fallbackThumb\s*\([\s\S]*?\n\}/);
chk('失败重试走节流队列（不再直接写 src）',
  !!fb && /dataset\.thumbUrl\s*=/.test(fb[0]) && /thumbProbe\(img\)/.test(fb[0])
  && !/img\.src\s*=/.test(fb[0]));

// 7. 重建时释放
chk('有 thumbReset', /function\s+thumbReset\s*\(/.test(src));
chk('thumbReset 断开观察器', /thumbObserver\.disconnect\(\)/.test(src));
const resets = (src.match(/thumbReset\(\)/g) || []).length;
chk('时间轴重建点都调用了 thumbReset（≥3 处）', resets >= 3, `${resets} 处`);

console.log('-'.repeat(66));
console.log(`  通过 ${pass} / 失败 ${fail}`);
console.log('='.repeat(66));
process.exit(fail ? 1 : 0);
