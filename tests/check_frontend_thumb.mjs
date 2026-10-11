#!/usr/bin/env node
/**
 * 前端缩略图调用链 · 静态守卫（无需浏览器）
 * ==========================================
 * 本机未安装 Playwright，浏览器 E2E 无法执行；用这个零依赖的静态检查兜住
 * 「前端还会不会请求旧档 / 还会不会用 srcset 让浏览器挑多档」这一类回归。

 * 断言（对应实施计划 §三.2、§十.2）：
 *   1. THUMB_SIZE === 640，且不存在 THUMB_VARIANTS 数组
 *   2. 不再输出 srcset / sizes（单档后它们会让浏览器按 DPR 挑资源）
 *   3. 所有 /thumb 构造点都走 thumbURL() 或 THUMB_SIZE，不出现任何旧档字面量
 *   4. 回收站缩略图默认档位 = THUMB_SIZE
 *   5. 灯箱占位 / 视频 poster 也都走 thumbURL()
 *
 * 运行：node tests/check_frontend_thumb.mjs
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
console.log('  前端缩略图调用链 · 静态守卫（web/app.js）');
console.log('='.repeat(66));

// 1. 单档常量
const m = src.match(/const\s+THUMB_SIZE\s*=\s*(\d+)\s*;/);
chk('THUMB_SIZE 定义为 640', !!m && Number(m[1]) === 640, m ? m[0] : '未找到');
chk('不存在 THUMB_VARIANTS 多档常量', !/THUMB_VARIANTS/.test(src));
chk('不存在 thumbSrcset 函数', !/thumbSrcset/.test(src));
chk('thumbURL() 走 THUMB_SIZE', /function\s+thumbURL\s*\([^)]*\)\s*\{[^}]*THUMB_SIZE/.test(src));

// 2. 不再输出 srcset / sizes
chk('不再输出 srcset 属性', !/srcset\s*=/.test(src), 'srcset 出现次数 ' + (src.match(/srcset\s*=/g) || []).length);
chk('不再输出 sizes 属性', !/sizes\s*=/.test(src), 'sizes 出现次数 ' + (src.match(/sizes\s*=/g) || []).length);
chk('不再写 img.sizes', !/\.sizes\s*=/.test(src));

// 3. 所有 /thumb 构造点
const thumbLines = src.split('\n')
  .map((l, i) => [i + 1, l])
  .filter(([, l]) => l.includes("'/thumb'") || l.includes('"/thumb"') || l.includes('`/api/trash/thumb'));
chk('存在缩略图构造点', thumbLines.length > 0, `共 ${thumbLines.length} 处`);
const badLines = thumbLines.filter(([, l]) =>
  !/thumbURL\(/.test(l) && !/THUMB_SIZE/.test(l) && !/\$\{side\}/.test(l));
chk('每个 /thumb 构造点都走 thumbURL() / THUMB_SIZE（或重试用的 ${side}）',
  badLines.length === 0, JSON.stringify(badLines));

// 4. 旧档字面量
const oldLits = src.match(/&s=\s*(120|160|240|320|480|960)\b/g) || [];
chk('不存在旧档字面量（&s=120/160/240/320/480/960）',
  oldLits.length === 0, JSON.stringify(oldLits));

// 5. 关键位置
chk('回收站缩略图默认档位使用 THUMB_SIZE',
  /function trashThumbURL\([\s\S]{0,120}THUMB_SIZE/.test(src));
chk('回收站调用点显式传 THUMB_SIZE',
  /trashThumbURL\(first\.itemId,\s*THUMB_SIZE\)/.test(src));
chk('灯箱占位走 thumbURL()', /low\.src\s*=\s*thumbURL\(/.test(src));
chk('视频 poster 走 thumbURL()', /v\.poster\s*=\s*thumbURL\(/.test(src));

console.log('-'.repeat(66));
console.log(`  通过 ${pass} / 失败 ${fail}`);
console.log('='.repeat(66));
process.exit(fail ? 1 : 0);
