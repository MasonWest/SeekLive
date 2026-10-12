#!/usr/bin/env node
/**
 * 首屏合并（/api/bootstrap）· 静态守卫（无需浏览器）
 * ================================================
 * 背景：boot() 原本是 session→libs→tree→items 串行 4 步；在高延迟链路上
 * 光这几次小请求就要好几秒。合并成一次往返后，必须锁住这些不变量：
 *
 *   1. 首屏只打 /api/bootstrap，**不再**单独打 /api/session、/api/libs
 *   2. 匿名访客在拿到 libs 之前就早退（服务端这时只回身份、不附相册数据）
 *   3. 服务端没升级时能回退到旧链路（401/403/404 都算「对面是旧版」）
 *   4. 服务端侧：/api/bootstrap 在匿名白名单里，且 anon 下**只回 session**
 *   5. 首日 items 随 bootstrap 下发，前端按 day.key 塞进缓存
 *
 * 运行：node tests/check_frontend_boot.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = join(HERE, '..');
const js = readFileSync(join(APP, 'web', 'app.js'), 'utf8');
const py = readFileSync(join(APP, 'server.py'), 'utf8');

let pass = 0, fail = 0;
const chk = (name, ok, detail = '') => {
  if (ok) { pass++; console.log(`  OK  ${name}` + (detail ? `   [${detail}]` : '')); }
  else { fail++; console.log(`  FAIL ${name}` + (detail ? `   [${detail}]` : '')); }
};

console.log('='.repeat(66));
console.log('  首屏合并 /api/bootstrap · 静态守卫（web/app.js + server.py）');
console.log('='.repeat(66));

const boot = js.match(/async function boot\(\)\s*\{[\s\S]*?\n\}/);
chk('找到 boot()', !!boot);
const bootSrc = boot ? boot[0] : '';
chk('boot() 走 /api/bootstrap', /api\('\/api\/bootstrap'\)/.test(bootSrc));
chk('boot() 主路径不再直接请求 /api/session', !/api\('\/api\/session'\)/.test(bootSrc));
chk('boot() 主路径不再直接请求 /api/libs', !/api\('\/api\/libs'\)/.test(bootSrc));

// 匿名必须早退（在 applyLibs 之前），否则会把未授权的 libs 当数据用
const iAnon = bootSrc.indexOf("sess.scope === 'anon'");
const iLibs = bootSrc.indexOf('applyLibs(');
chk('匿名早退发生在 applyLibs 之前',
  iAnon >= 0 && iLibs >= 0 && iAnon < iLibs, `anon@${iAnon} libs@${iLibs}`);
chk('匿名早退条件含 protectRead', /sess\.scope === 'anon' && sess\.protectRead/.test(bootSrc));

// 回退：旧服务端对该路径给的是 401（匿名被 read_guard 挡），不是 404
chk('回退条件覆盖 401/403/404 → bootLegacy',
  /e\.status === 401[\s\S]{0,80}e\.status === 403[\s\S]{0,80}e\.status === 404[\s\S]{0,40}bootLegacy\(\)/
    .test(bootSrc));

const legacy = js.match(/async function bootLegacy\(\)\s*\{[\s\S]*?\n\}/);
chk('存在 bootLegacy() 兜底', !!legacy);
const legSrc = legacy ? legacy[0] : '';
chk('bootLegacy 仍按 session→libs 顺序取数',
  /api\('\/api\/session'\)/.test(legSrc) && /api\('\/api\/libs'\)/.test(legSrc)
  && legSrc.indexOf("/api/session'") < legSrc.indexOf("/api/libs'"));

// selectLib 的预取通道
const sl = js.match(/async function selectLib\(([^)]*)\)/);
chk('selectLib 接受第 4 个参数 pre', !!sl && /\bpre\b/.test(sl[1]), sl ? sl[1] : '');
chk('selectLib 优先用 pre.tree', /pre\s*&&\s*pre\.tree[\s\S]{0,120}state\.tree\s*=\s*pre\.tree/.test(js));
chk('selectLib 把首日 items 塞进缓存（按 state.days[0].key）',
  /pre\.items[\s\S]{0,160}state\.items\.set\(state\.days\[0\]\.key/.test(js));
chk('首日 items 的缓存键与 loadDay 一致（都用 day.key）', /state\.items\.get\(key\)/.test(js));

// 服务端侧不变量
chk('server.py: /api/bootstrap 在匿名白名单 ANON_GET 中',
  /ANON_GET\s*=\s*frozenset\(\{[\s\S]*?"\/api\/bootstrap"[\s\S]*?\}\)/.test(py));
const ab = py.match(/def api_bootstrap\(self\):[\s\S]*?\n    def /);
const abSrc = ab ? ab[0] : '';
chk('server.py: anon 下只回 session（不附 libs）',
  /ACCESS_PROTECT and scope not in \("admin", "share"\)[\s\S]{0,80}return self\.send_json\(out\)/.test(abSrc));
chk('server.py: 复用 session_payload / libs_payload / tree_payload / items_payload',
  ['session_payload', 'libs_payload', 'tree_payload', 'items_payload']
    .every(n => abSrc.includes(`self.${n}(`)));
chk('server.py: /api/session 与 api_session 共用同一份载荷',
  /def api_session\(self\):[\s\S]{0,400}return self\.send_json\(self\.session_payload\(\)\)/.test(py));
chk('server.py: 未知 /api 路径仍 404（bootstrap 已单独路由）',
  /if path == "\/api\/bootstrap":\s*\n\s*return self\.api_bootstrap\(\)/.test(py));

console.log('-'.repeat(66));
console.log(`  通过 ${pass} / 失败 ${fail}`);
console.log('='.repeat(66));
process.exit(fail ? 1 : 0);
