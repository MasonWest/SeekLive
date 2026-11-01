/* =====================================================
   时光轴 · SeekLive  |  前端应用逻辑
   双层懒加载：日期分组按需加载 → 图片按需加载
   ===================================================== */
'use strict';

/* ---------------- 基础 ---------------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const QS = new URLSearchParams(location.search);

/* 管理授权状态（不承载口令）：
   required = 服务端是否配置了管理口令；authed = 当前会话是否已授权。
   口令只在本文件的内存变量里短暂存在，随登录请求一次性发出，
   **不写 localStorage / sessionStorage、不进 URL、不进 <img src>**。 */
const admin = { required: false, authed: false, expiresAt: 0 };

/* 访问作用域（唯一权威来源 = 服务端 /api/session）：
     admin = 完整相册 + 管理能力；share = 只读、且仅限被授权的集合；anon = 未授权。
   ★ 绝不用 URL 上 ?share=1 之类的提示参数当授权凭证 —— 那只是 UI 提示。 */
const sess = { scope: 'anon', share: null, protectRead: true,
               adminRequired: false, shareEnabled: true, canManage: false };
const IS_SHARE = () => sess.scope === 'share';
function applySession(d) {
  sess.scope = d.scope || 'anon';
  sess.share = d.share || null;
  sess.protectRead = !!d.protectRead;
  sess.adminRequired = !!d.adminRequired;
  sess.shareEnabled = d.shareEnabled !== false;
  sess.canManage = !!d.canManage;
  if (d.admin) {
    admin.required = !!d.adminRequired;
    admin.authed = !!d.admin.authed;
    admin.expiresAt = d.admin.expiresAt || 0;
  }
}
function fmtExpire(ts) {
  const days = Math.ceil((ts * 1000 - Date.now()) / 86400000);
  if (days <= 0) return '即将过期';
  if (days === 1) return '明天过期';
  if (days < 30) return `${days} 天后过期`;
  const d = new Date(ts * 1000), p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} 过期`;
}

const IS_IOS = /iP(hone|od|ad)/.test(navigator.userAgent) ||
  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const IS_SAFARI = /^((?!chrome|android|crios|fxios|edg).)*safari/i.test(navigator.userAgent);

/* 浏览器是否原生支持 HEVC(H.265)：iOS/Safari 是，Chrome/Edge/Firefox 不是 */
function canPlayHevc() {
  try {
    return !!document.createElement('video')
      .canPlayType('video/mp4; codecs="hvc1"');
  } catch (e) { return false; }
}

/* ★ 缩略图只有一个规格：640（与后端 THUMB_KEEP_SIDES 对齐）。
   不再有 srcset 候选集 —— 同一张源图只生成/请求一份缓存，网格密度只是 CSS 缩放。 */
const THUMB_SIZE = 640;

/* ★ 照片墙五档密度 —— 密度和「时间浏览层级」一起变，不是单纯加两个列数选项。
   L1 大图   2 张/行（手机）  逐日分组，适合认真看细节 / 认人
   L2 标准   4 张/行（手机）  逐日分组，日常浏览（默认档）
   L3 密集   6 张/行（手机）  逐日分组，快速扫图
   L4 月份  整月连续照片墙，日期之间不再另起一行
   L5 年份  月份封面卡片，点击展开对应月份
   · target＝单元格「目标宽度(px)」，列数 = clamp(round(可用宽/target), min, max)。
     min/max 只用来兜住极端窄屏 / 超宽屏，不改变档位之间的相对关系。
   · ★ 无论哪一档都只请求 640 单档缩略图；切换层级不增加媒体缓存规格。 */
const DENSITY_LEVELS = [
  { key: 'l1', name: '大图', target: 210, min: 2, max: 12, group: 'day' },
  { key: 'l2', name: '标准', target: 110, min: 3, max: 22, group: 'day' },
  { key: 'l3', name: '密集', target: 72, min: 4, max: 30, group: 'day' },
  { key: 'l4', name: '月份', target: 58, min: 5, max: 24, group: 'month' },
  { key: 'l5', name: '年份', target: 13.5, min: 8, max: 44, group: 'year' },
];
const DENSITY_LS_KEY = 'sl.density';
const DENSITY_DEFAULT = 1;                    // L2 标准
const TILE_BATCH = 20;                      // 每次追加的日期分组数
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

/* 记住上次选择（读不到 / 越界 → 回到默认档） */
function loadDensity() {
  try {
    const v = parseInt(localStorage.getItem(DENSITY_LS_KEY), 10);
    if (Number.isFinite(v) && v >= 0 && v < DENSITY_LEVELS.length) return v;
  } catch (e) { /* 隐私模式 / 被禁用 → 用默认 */ }
  return DENSITY_DEFAULT;
}
function saveDensity(i) {
  try { localStorage.setItem(DENSITY_LS_KEY, String(i)); } catch (e) {}
}

const state = {
  libs: [], lib: null, tree: null, viewEpoch: 0,
  days: [], renderedDayIdx: 0, done: false,
  density: loadDensity(), cols: 3, yearCounts: {},
  search: { active: false, q: '', off: 0, total: 0, done: false },
  viewer: { list: [], i: 0, scale: 1, tx: 0, ty: 0 },
  years: [], railYear: null, railYm: null,   // 年份 / 月份导航
  collection: { tab: 'photos', context: null, albums: [] },
};

/* Bounded browsing data. Selected items and the current viewer item have separate owners. */
const PAGE_SIZE = 120;
const PAGE_CACHE_LIMIT = 32;
const BROWSE_CONCURRENCY = 4;
const pageCache = new Map();
const nearbyGrids = new Set();
let browseController = new AbortController();
let browseActive = 0;
const browseWaiters = new Set();
function abortError() { return new DOMException('请求已取消', 'AbortError'); }
function resetBrowseRequests() {
  browseController.abort();
  browseController = new AbortController();
  pageCache.clear();
  nearbyGrids.clear();
  if (groupObserver) groupObserver.disconnect();
  $$('#timeline .grid').forEach(releaseGrid);
  yearObserver.disconnect();
  $$('.month-card').forEach(releaseYearCard);
}
function pageKey(context, off) {
  return JSON.stringify([context.epoch, context.lib, context.q || '', context.key, off]);
}
function cachedPage(context, off) {
  const key = pageKey(context, off), page = pageCache.get(key);
  if (page) { pageCache.delete(key); pageCache.set(key, page); }
  return page;
}
function rememberPage(context, off, page) {
  if (context.lib !== state.lib || context.epoch !== state.viewEpoch) return;
  const key = pageKey(context, off);
  pageCache.delete(key); pageCache.set(key, page);
  while (pageCache.size > PAGE_CACHE_LIMIT) pageCache.delete(pageCache.keys().next().value);
}
async function browseSlot(signal) {
  if (signal.aborted) throw abortError();
  if (browseActive >= BROWSE_CONCURRENCY) {
    await new Promise((resolve, reject) => {
      const waiter = () => { cleanup(); resolve(); };
      const cancel = () => { cleanup(); reject(abortError()); };
      const cleanup = () => { browseWaiters.delete(waiter); signal.removeEventListener('abort', cancel); };
      browseWaiters.add(waiter);
      signal.addEventListener('abort', cancel, { once: true });
    });
    // The releasing job transfers its slot to this waiter.
  } else browseActive++;
  if (signal.aborted) { releaseBrowseSlot(); throw abortError(); }
}
function releaseBrowseSlot() {
  const next = browseWaiters.values().next().value;
  if (next) next(); else browseActive--;
}
async function fetchBrowsePage(context, off, signal = browseController.signal) {
  if (signal.aborted || context.epoch !== state.viewEpoch || context.lib !== state.lib) throw abortError();
  const hit = cachedPage(context, off);
  if (hit) return hit;
  await browseSlot(signal);
  try {
    if (context.epoch !== state.viewEpoch || context.lib !== state.lib) throw abortError();
    const params = { lib: context.lib, off, lim: PAGE_SIZE };
    if (context.story) Object.assign(params, {id:context.story,mode:'full'});
    else if (context.album) Object.assign(params, context.album);
    else if (context.q) params.q = context.q;
    else { params.ym = context.ym; if (context.d) params.d = context.d; }
    const page = await api(context.story ? '/api/vision/story/items' : context.album ? '/api/vision/album' : context.q ? '/api/search' : '/api/items', params, signal);
    if (signal.aborted || context.epoch !== state.viewEpoch || context.lib !== state.lib) throw abortError();
    rememberPage(context, off, page);
    return page;
  } finally { releaseBrowseSlot(); }
}
function gridContext(el) {
  return { lib: state.lib, epoch: state.viewEpoch, key: el.dataset.key,
    ym: el.dataset.ym, d: el.dataset.d };
}
function releaseGrid(grid) {
  if (grid._job) { grid._job.abort(); grid._job = null; }
  grid.querySelectorAll('img').forEach(img => { thumbObserver.unobserve(img); thumbDefer(img); });
  grid.replaceChildren();
  grid._range = '';
}
// The full grid keeps its height; only rows near the viewport own photo nodes.
function rowWindow(total, cols, cell, gap, top, viewport, ahead = 400) {
  const rows = Math.ceil(total / cols), step = cell + gap;
  const first = Math.max(0, Math.min(rows, Math.floor((-top - ahead) / step)));
  const last = Math.max(first, Math.min(rows, Math.ceil((viewport - top + ahead) / step)));
  return { start: first * cols, end: Math.min(total, last * cols),
    height: Math.max(0, rows * step - gap) };
}
function gridGeometry(grid, total) {
  const cs = getComputedStyle(grid), gap = parseFloat(cs.columnGap) ||
    parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--gap')) || 4;
  const left = parseFloat(cs.paddingLeft) || 0, right = parseFloat(cs.paddingRight) || 0;
  const cell = Math.max(1, (grid.clientWidth - left - right - (state.cols - 1) * gap) / state.cols);
  return { ...rowWindow(total, state.cols, cell, gap, grid.getBoundingClientRect().top, window.innerHeight),
    cell, gap, left };
}
function positionTile(tile, index, geometry) {
  const { cell, gap, left } = geometry;
  tile.style.cssText = `position:absolute;width:${cell}px;height:${cell}px;left:${left + (index % state.cols) * (cell + gap)}px;top:${Math.floor(index / state.cols) * (cell + gap)}px`;
}
function createPhotoTile(it, index, context) {
  const a = document.createElement('a');
  a.className = 'tile'; a.href = '#'; a.dataset.idx = index; a.dataset.ctx = context.key;
  a.item = it; a.browseContext = context;
  a.classList.toggle('sel', sel.active && sel.map.has(it.p));
  a.innerHTML = (it.live ? '<span class="badge live">LIVE</span>' :
    it.k === 'vid' ? '<span class="badge">▶</span>' : '') + '<span class="pick"></span>' +
    `<img alt="${esc(it.n)}" decoding="async">`;
  a.querySelector('img').dataset.thumbUrl = thumbURL(it.p);
  return a;
}
function paintWindow(grid, context, geometry) {
  const existing = new Map([...grid.querySelectorAll('.tile')].map(t => [+t.dataset.idx, t]));
  for (const [index, tile] of existing) {
    if (index >= geometry.start && index < geometry.end) continue;
    tile.querySelectorAll('img').forEach(img => { thumbObserver.unobserve(img); thumbDefer(img); });
    tile.remove(); existing.delete(index);
  }
  let nextTile = null;
  for (let index = geometry.end - 1; index >= geometry.start; index--) {
    const off = Math.floor(index / PAGE_SIZE) * PAGE_SIZE;
    const page = cachedPage(context, off), it = page && page.items[index - off];
    let tile = existing.get(index);
    if (tile && (!it || tile.item === it)) {
      positionTile(tile, index, geometry);
      if (tile.nextSibling !== nextTile) grid.insertBefore(tile, nextTile);
      nextTile = tile; continue;
    }
    if (tile) {
      tile.querySelectorAll('img').forEach(img => { thumbObserver.unobserve(img); thumbDefer(img); });
      tile.remove();
    }
    tile = it ? createPhotoTile(it, index, context) : document.createElement('div');
    if (!it) { tile.className = 'tile sk'; tile.dataset.idx = index; }
    positionTile(tile, index, geometry); grid.insertBefore(tile, nextTile); nextTile = tile;
    const img = tile.querySelector('img'); if (img) thumbObserver.observe(img);
  }
}
async function loadGridWindow(grid, context, total, force = false) {
  if (!grid.isConnected) return;
  grid.classList.add('window-grid');
  context.total = total;
  grid._context = context; grid._total = total;
  const geometry = gridGeometry(grid, total);
  grid.style.height = geometry.height + 'px'; grid.style.minHeight = '';
  const signature = [geometry.start, geometry.end, state.cols, geometry.cell].join(':');
  if (!force && grid._range === signature) return;
  if (grid._job) grid._job.abort();
  grid._range = signature;
  grid.querySelectorAll('.window-status').forEach(el => el.remove());
  if (geometry.start === geometry.end) { releaseGrid(grid); return; }
  paintWindow(grid, context, geometry);
  const job = new AbortController(), parent = browseController.signal;
  const cancel = () => job.abort();
  parent.addEventListener('abort', cancel, { once: true });
  if (parent.aborted) job.abort();
  grid._job = job;
  const current = () => !job.signal.aborted && grid.isConnected && grid._job === job &&
    context.epoch === state.viewEpoch && context.lib === state.lib;
  try {
    // Visible rows precede overscan pages; each response can paint immediately.
    const pages = [];
    for (let off = Math.floor(geometry.start / PAGE_SIZE) * PAGE_SIZE; off < geometry.end; off += PAGE_SIZE) pages.push(off);
    const focus = Math.max(geometry.start, Math.floor(-grid.getBoundingClientRect().top / (geometry.cell + geometry.gap)) * state.cols);
    pages.sort((a, b) => Math.abs(a + PAGE_SIZE / 2 - focus) - Math.abs(b + PAGE_SIZE / 2 - focus));
    for (const off of pages) {
      await fetchBrowsePage(context, off, job.signal);
      if (!current()) return;
      paintWindow(grid, context, geometry);
    }
  } catch (e) {
    if (!current() || e.name === 'AbortError') return;
    const retry = document.createElement('button');
    retry.className = 'day-retry window-status'; retry.textContent = '加载失败，点击重试';
    retry.style.top = Math.max(0, Math.floor(geometry.start / state.cols) * (geometry.cell + geometry.gap)) + 'px';
    retry.onclick = () => loadGridWindow(grid, context, total, true);
    grid.appendChild(retry);
  } finally {
    parent.removeEventListener('abort', cancel);
    if (grid._job === job) grid._job = null;
  }
}
let windowFrame = 0;
function scheduleWindows() {
  if (windowFrame) return;
  windowFrame = requestAnimationFrame(() => { windowFrame = 0; updateWindows(); });
}
function updateWindows() {
  if (state.switching || !viewer.hidden) return;
  nearbyGrids.forEach(grid => {
    const r = grid.getBoundingClientRect();
    if (r.bottom < -500 || r.top > window.innerHeight + 500) {
      if (grid.children.length || grid._job) releaseGrid(grid);
      return;
    }
    if (grid._context) loadGridWindow(grid, grid._context, grid._total);
    else if (grid.closest('.day-blk')) loadDay(grid.closest('.day-blk'));
  });
}
window.addEventListener('scroll', scheduleWindows, { passive: true });


/* Month walls and year overview share the same bounded pages and thumbnail lane. */
function wallMode() {
  return state.search.active ? 'day' : DENSITY_LEVELS[state.density].group;
}
function monthDayOffset(tree, key) {
  const [ym, d] = key.split('|'), month = (tree.months || []).find(m => m.ym === ym);
  let off = 0;
  for (const day of month?.days || []) { if (day.d === d) return off; off += day.n; }
  return 0;
}
function convertWallAnchor(anchor, mode, tree) {
  if (!anchor) return null;
  const ym = anchor.key.split('|')[0], month = (tree.months || []).find(m => m.ym === ym);
  if (!month) return { ...anchor, rel: 0 };
  const monthlyIndex = (anchor.index || 0) + (anchor.key.includes('|') ? monthDayOffset(tree, anchor.key) : 0);
  const base = { ...anchor, rel: 0, tileRel: anchor.tileRel ?? 0 };
  if (mode !== 'day') return { ...base, key: ym, index: mode === 'year' ? undefined : monthlyIndex };
  let off = 0;
  for (const day of month.days || []) {
    if (monthlyIndex < off + day.n)
      return { ...base, key: ym + '|' + day.d, index: monthlyIndex - off };
    off += day.n;
  }
  return { key: ym + '|' + (month.days?.[0]?.d || '01'), rel: 0 };
}
function rebuildWall(anchor) {
  resetBrowseRequests();
  thumbReset();
  $('#timeline').replaceChildren();
  state.renderedDayIdx = 0; state.done = false;
  $('#endTip').hidden = true;
  renderNextBatch();
  if (anchor) restoreViewAnchor(anchor);
  else window.scrollTo(0, 0);
  requestAnimationFrame(syncRail);
}
function renderMonthBatch() {
  const frag = document.createDocumentFragment(), created = [];
  let index = state.renderedDayIdx, months = 0;
  while (index < state.days.length && months++ < 3) {
    const ym = state.days[index].ym;
    const month = (state.tree.months || []).find(m => m.ym === ym);
    const sep = document.createElement('div');
    sep.className = 'month-sep'; sep.dataset.ym = ym;
    sep.innerHTML = `${fmtMonthLabel(ym)}<span>${countOfMonth(ym)} 张 · ${month.days.length} 天</span>`;
    frag.appendChild(sep);
    const group = document.createElement('section');
    group.className = 'day-blk month-wall';
    Object.assign(group.dataset, { key: ym, ym, d: '', total: month.n });
    group.innerHTML = '<div class="grid"></div>';
    created.push(group); frag.appendChild(group); groupObserver.observe(group);
    while (index < state.days.length && state.days[index].ym === ym) index++;
  }
  $('#timeline').appendChild(frag);
  state.renderedDayIdx = index;
  sizeSkeletons(created);
  if (index >= state.days.length) { state.done = true; $('#endTip').hidden = false; }
  else requestAnimationFrame(() => { if (nearBottom(1800)) renderNextBatch(); });
}
function releaseYearCard(card) {
  if (card._job) { card._job.abort(); card._job = null; }
  card.querySelectorAll('img').forEach(img => { thumbObserver.unobserve(img); thumbDefer(img); });
  card.querySelector('.year-preview')?.replaceChildren();
  card.dataset.loaded = '';
}
async function loadYearCard(card) {
  if (!card.isConnected || card._job || card.dataset.loaded === '1') return;
  const job = new AbortController(), parent = browseController.signal;
  const cancel = () => job.abort();
  parent.addEventListener('abort', cancel, { once: true });
  if (parent.aborted) job.abort();
  card._job = job;
  const context = { lib: state.lib, epoch: state.viewEpoch, key: card.dataset.ym, ym: card.dataset.ym };
  try {
    const page = await fetchBrowsePage(context, 0, job.signal);
    if (job.signal.aborted || !card.isConnected) return;
    const preview = card.querySelector('.year-preview');
    preview.replaceChildren();
    const count = Math.min(4, page.items.length);
    preview.dataset.count = count;
    for (let i = 0; i < count; i++) {
      const item = page.items[Math.floor(i * page.items.length / count)];
      const cell = document.createElement('span');
      cell.className = 'cover-cell'; cell.item = item;
      const img = document.createElement('img'); img.alt = '';
      img.dataset.thumbUrl = thumbURL(item.p);
      cell.appendChild(img); preview.appendChild(cell); thumbObserver.observe(img);
    }
    card.dataset.loaded = '1';
    card.querySelector('.month-card-hint').textContent = '展开这个月 ↗';
  } catch (e) {
    if (!job.signal.aborted && card.isConnected) {
      card.querySelector('.month-card-hint').textContent = '封面暂不可用 · 仍可展开';
    }
  } finally {
    parent.removeEventListener('abort', cancel);
    if (card._job === job) card._job = null;
  }
}
const yearObserver = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) loadYearCard(entry.target);
    else releaseYearCard(entry.target);
  });
}, { rootMargin: '300px 0px' });
function renderYearOverview() {
  const timeline = $('#timeline');
  const years = new Map();
  for (const month of state.tree.months || []) {
    const yr = month.ym.slice(0, 4);
    if (!years.has(yr)) years.set(yr, []);
    years.get(yr).push(month);
  }
  for (const [year, months] of years) {
    const section = document.createElement('section'); section.className = 'year-collection';
    section.innerHTML = `<div class="year-sep" data-yr="${year}">${year}<span>${countOfYear(year)} 张 · ${months.length} 个月</span></div>`;
    const cards = document.createElement('div'); cards.className = 'year-months';
    for (const month of months) {
      const card = document.createElement('button');
      card.type = 'button'; card.className = 'month-card'; card.dataset.ym = month.ym;
      card.setAttribute('aria-label', `展开 ${fmtMonthLabel(month.ym)}，${month.n} 张照片`);
      card.innerHTML = `<span class="year-preview" aria-hidden="true"></span><span class="month-card-meta"><strong>${+month.ym.slice(5)} 月</strong><span>${month.n.toLocaleString()} 张</span></span><span class="month-card-hint">展开这个月 ↗</span>`;
      card.onclick = () => { setDensity(3); jumpToMonth(month.ym); };
      cards.appendChild(card); yearObserver.observe(card);
    }
    section.appendChild(cards); timeline.appendChild(section);
  }
  state.renderedDayIdx = state.days.length; state.done = true;
  $('#endTip').hidden = false;
}

/* ---------------- 工具 ---------------- */
function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtSize(n) {
  const u = ['B', 'KB', 'MB', 'GB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
}
function fmtDayHead(ym, dd) {
  const [y, m] = ym.split('-');
  const d = new Date(+y, +m - 1, +dd);
  return `${+m} 月 ${+dd} 日`;
}
function fmtMonthLabel(ym) {
  const [y, m] = ym.split('-');
  return `${y} 年 ${+m} 月`;
}
function fmtFull(ts) {
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  const w = WEEK[d.getDay()];
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${w} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function weekdayOf(ym, dd) {
  const [y, m] = ym.split('-');
  return WEEK[new Date(+y, +m - 1, +dd).getDay()];
}
const enc = encodeURIComponent;

function api(path, params = {}, signal) {
  const q = new URLSearchParams(params).toString();
  // 浏览类接口：凭证只走 Cookie（同源自动携带），URL 保持干净。
  return fetch(path + (q ? '?' + q : ''), { credentials: 'same-origin', signal })
    .then(r => {
      if (!r.ok) { const e = new Error('HTTP ' + r.status); e.status = r.status; throw e; }
      return r.json();
    });
}

/* 分享链接失效（被撤销 / 过期）→ 退回说明页。判定始终以服务端 401/403 为准。 */
function shareExpired() {
  sess.scope = 'anon'; sess.share = null; sess.canManage = false;
  $('#shareBar').hidden = true;
  splashAsk('分享链接已失效（可能已被撤销或已过期）。<br>请向分享人索取新的链接。');
  $('#gateLogin').hidden = true;
}
function mediaURL(ep, rel, extra = '') {
  return `${ep}?lib=${enc(state.lib)}&p=${enc(rel)}${extra}`;
}
/* 缩略图 URL：单档 640，永远只有这一个规格。
   ★ 服务端仍会把任意历史参数（s=240/480/960…）吸附到 640，因此老书签 / 老页面
     也不会报错、更不会生成旧档；这里统一走常量，不依赖隐式吸附。 */
function thumbURL(rel) {
  return mediaURL('/thumb', rel, '&s=' + THUMB_SIZE);
}

let toastTimer = null;
function toast(msg, ms = 2000) {
  const el = $('#toast');
  el.textContent = msg; el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

/* ---------------- 布局 / 密度 ---------------- */
/* 某个档位在「当前视口宽」下会排成几列（面板里给用户看的就是这个数） */
function colsForLevel(lv) {
  const w = Math.min(window.innerWidth, 2200);
  return Math.max(lv.min, Math.min(lv.max, Math.round((w - 16) / lv.target)));
}
function computeCols() {
  const lv = DENSITY_LEVELS[state.density] || DENSITY_LEVELS[DENSITY_DEFAULT];
  const cols = colsForLevel(lv);
  state.cols = cols;
  const root = document.documentElement;
  root.style.setProperty('--cols', cols);
  // 时间浏览层级：day / month / year —— 用 class 切换，不重排 DOM，滚动位置才好保
  root.classList.remove('grp-day', 'grp-month', 'grp-year');
  root.classList.add('grp-' + lv.group);
  root.dataset.density = lv.key;
}
/* 网格里单个格子的实际宽度（含 gap 影响），预加载余量按它折算 */
function tileCellPx() {
  const w = Math.min(window.innerWidth, 2200);
  const gap = parseFloat(getComputedStyle(document.documentElement)
    .getPropertyValue('--gap')) || 4;
  return Math.max(6, (w - 16 - (state.cols - 1) * gap) / state.cols);
}
/* 预加载余量：格子越大越可以早拉（但别超过 900px）；
   高密度档格子很小，若仍用 900px 会一次触发几十天的 /api/items → 请求风暴。 */
function observerMargin() {
  return Math.max(320, Math.min(900, Math.round(tileCellPx() * 7)));
}
function prefersReduce() {
  try { return window.matchMedia('(prefers-reduced-motion:reduce)').matches; }
  catch (e) { return false; }
}
function syncTopbarHeight() {
  const h = $('#topbar').offsetHeight;
  document.documentElement.style.setProperty('--topbar-h', h + 'px');
}

/* =====================================================
   启动
   ===================================================== */
async function boot() {
  computeCols();
  rearmObserver();                            // 用真实列数定下预加载余量
  syncTopbarHeight();

  // ① 首屏只问一次：身份 + 图库 + 时间轴 + 首日照片，服务端一次往返给全
  //    （旧的 session→libs→tree→items 串行 4 步在高延迟链路上要多花 3 次往返）。
  //    身份判定仍然**只**以服务端为准 —— 这里用的就是同一份 /api/session 载荷。
  let b = null;
  try { b = await api('/api/bootstrap'); }
  catch (e) {
    // 服务端还没升级时回退到旧的串行链路 —— 前端可以先于服务端上线，
    // 滚动升级的中间状态不会把页面打死。
    // ★ 老服务端对这个新路径给的不是 404：它不在旧版的匿名白名单里，
    //   所以匿名访问会先被 read_guard 挡成 401；带上身份后才是 404。
    //   而**新**服务端把 /api/bootstrap 列为匿名白名单、永远回 200 ——
    //   因此 401/403/404 在这里只可能意味着「对面是旧版」。
    if (e.status === 401 || e.status === 403 || e.status === 404) return bootLegacy();
    splashFail('无法连接服务：' + e.message); return;
  }

  applySession(b.session || {});

  // ② 未授权访客：服务端此时**只回身份、不附任何相册数据**，所以这里必须早退。
  if (sess.scope === 'anon' && sess.protectRead) {
    // ?share=1 只是「我是点了分享链接过来的」这一**界面提示**，绝不是授权凭证
    // （授权一律由服务端 /api/session 判定）。有了它，才能把「链接失效」与
    // 「普通未授权访客」区分开、给出更准确的提示。
    if (QS.get('share')) { shareExpired(); return; }
    splashAsk(sess.shareEnabled
      ? '这个相册已开启访问保护。<br>请使用收到的分享链接，或输入管理口令。'
      : '这个相册已开启访问保护。<br>请输入管理口令。');
    return;
  }

  applyLibs(b.libs || {});
  if (!afterLibs(b.libs || {})) return;
  // 服务端已在同一次响应里附上首个库的 tree + 首日 items → 交给 selectLib 直接用，
  // 不再各发一次请求。库不一致（或没带）时 pre=null，行为回到旧的「自己拉」。
  const first = state.libs.filter(l => l.count > 0)[0];
  const pre = (b.tree && b.lib === first.id) ? { tree: b.tree, items: b.items } : null;
  await selectLib(first.id, true, null, pre);
  if (sess.canManage && QS.get('view') === 'memories') openCollections('memories');
}

/* 老服务端（无 /api/bootstrap）的兜底首屏：与合并前完全一致地串行取数。
   存在的意义只有一个 —— 让前端可以先于服务端上线，不至于把页面打死。 */
async function bootLegacy() {
  try { applySession(await api('/api/session')); }
  catch (e) { splashFail('无法连接服务：' + e.message); return; }

  if (sess.scope === 'anon' && sess.protectRead) {
    if (QS.get('share')) { shareExpired(); return; }
    splashAsk(sess.shareEnabled
      ? '这个相册已开启访问保护。<br>请使用收到的分享链接，或输入管理口令。'
      : '这个相册已开启访问保护。<br>请输入管理口令。');
    return;
  }
  let d = null;
  try { d = await api('/api/libs'); }
  catch (e) { splashFail('无法读取相册：' + e.message); return; }
  applyLibs(d);
  if (!afterLibs(d)) return;
  await selectLib(state.libs.filter(l => l.count > 0)[0].id, true);
  if (sess.canManage && QS.get('view') === 'memories') openCollections('memories');
}

/* libs 载荷 → 全局状态（两条首屏路径共用，避免第二套分流逻辑）。 */
function applyLibs(d) {
  state.libs = d.libraries || [];
  if (d.trash && d.trash.retentionDays) trashDays = d.trash.retentionDays;
  // /api/libs 也会带回权威作用域（在分享作用域下顺带拿到分享元信息）
  if (d.scope) { sess.scope = d.scope; if (d.share) sess.share = d.share; }
  applyScopeUI();
}
/* libs 之后的分流。返回 false 表示已处理（调用方应直接 return）。 */
function afterLibs(d) {
  if (d && (d.ready === false || d.indexing)) { idxOpen(); return false; }  // 首次索引进行中
  const usable = state.libs.filter(l => l.count > 0);
  if (!usable.length) {
    const anyExist = state.libs.some(l => l.exists);
    splashFail(anyExist ? '图库里没有可显示的图片' : '没有找到可用的图库目录，请编辑 libraries.json');
    return false;
  }
  buildChips(usable);
  return true;
}

/* 未授权访客的落地页：复用 splash，附「输入管理口令」入口 */
function splashAsk(html) {
  const sub = $('#splashSub');
  sub.innerHTML = html;
  sub.style.color = 'var(--fg-2)';
  const bar = $('.splash-bar');
  if (bar) bar.style.display = 'none';
  const g = $('#gateLogin');
  if (g) g.hidden = !sess.adminRequired;
}

/* 按作用域调整界面：分享访客隐藏全部管理/破坏性入口。
   ★ 前端隐藏只是体验 —— 真正的控制始终在服务端（read_guard / media_guard）。 */
function applyScopeUI() {
  const m = sess.canManage;
  const collections = $('#collectionNav');
  if (collections) collections.hidden = !m;
  $('#searchInput').placeholder = m ? '搜索文件名、人物或动物…' : '搜索文件名…';
  for (const sel of ['#btnSelect', '#btnTrash', '#btnIdx', '#btnRefresh',
                     '#btnGc', '#btnShareManage', '#selMonth', '#selShare']) {
    const el = $(sel);
    if (el) el.hidden = !m;
  }
  const bar = $('#shareBar');
  if (IS_SHARE() && sess.share) {
    const n = (sess.share.count || 0).toLocaleString();
    $('#shareBarText').textContent = `分享：${sess.share.title} · ${n} 张` +
      (sess.share.expiresAt ? ` · ${fmtExpire(sess.share.expiresAt)}` : '') +
      '（只读，不能删除或修改）';
    bar.hidden = false;
  } else {
    bar.hidden = true;
  }
  document.body.classList.toggle('share-mode', IS_SHARE());
  syncTopbarHeight();
}

function splashFail(msg) {
  $('#splashSub').innerHTML = esc(msg);
  $('#splashSub').style.color = 'var(--fg-2)';
  $('.splash-bar').style.display = 'none';
  setTimeout(() => toast(msg, 6000), 600);
}

function buildChips(libs) {
  const box = $('#chips');
  box.innerHTML = '';
  libs.forEach(l => {
    const b = document.createElement('button');
    b.className = 'chip';
    b.dataset.lib = l.id;
    b.innerHTML = `${esc(l.name)}<span class="c-n">${l.count.toLocaleString()}` +
      (l.live ? ` · LIVE ${l.live}` : '') + '</span>' +
      (l.stale ? '<span class="c-off">离线</span>' : '');
    b.onclick = () => selectLib(l.id);
    box.appendChild(b);
  });
  if (libs.length < 2) box.style.display = 'none';
}

async function selectLib(id, isBoot = false, anchor = null, pre = null) {
  if (state.lib === id && !isBoot) return;
  const epoch = ++state.viewEpoch;
  resetBrowseRequests();
  if (!viewer.hidden) closeViewer();
  state.viewer.list = [];
  clearTimeout(searchTimer);
  if (sel.active) toggleSelectMode(false);
  state.lib = id;
  state.switching = true;                  // 阻止哨兵在切库期间用旧索引渲染
  $$('.chip').forEach(c => c.classList.toggle('on', c.dataset.lib === id));
  const meta = state.libs.find(l => l.id === id);
  $('#brandName').textContent = meta ? meta.name : '时光轴';
  document.title = (meta ? meta.name : '时光轴') + ' · 时光轴';
  if (meta && meta.stale && !isBoot) toast('该图库目录当前不可访问，显示的是上次索引', 3600);

  state.renderedDayIdx = 0; state.done = false;
  state.search.request = null;
  state.search.active = false;
  resetCollection();
  $('#searchBar').hidden = true;
  thumbReset();                               // 时间轴整体重建：放掉旧的缩略图队列/观察
  $('#timeline').innerHTML = '';
  $('#endTip').hidden = true;
  rail.hidden = true; syncRailHeight();
  hideToTop();
  syncTopbarHeight();

  if (isBoot) $('#splash').classList.add('hide');

  try {
    // 首屏：tree 已随 /api/bootstrap 一起拿到（且库对得上）→ 不再单独发一次请求。
    if (pre && pre.tree && pre.tree.lib && pre.tree.lib.id === id) {
      state.tree = pre.tree;
    } else {
      const tree = await api('/api/tree', { lib: id }, browseController.signal);
      if (epoch !== state.viewEpoch) return;
      state.tree = tree;
    }
  } catch (e) {
    if (epoch !== state.viewEpoch) return;
    state.switching = false;
    if ((e.status === 401 || e.status === 403) && IS_SHARE()) {
      shareExpired();                      // 分享已撤销/过期 → 明确告知，而不是「加载失败」
      return;
    }
    renderEmpty('加载失败', '无法读取索引：' + e.message, true);
    return;
  }
  state.days = (state.tree.dayOrder || []).map(k => {
    const [ym, d] = k.split('|');
    return { key: k, ym, d };
  });
  // 每年总张数（「年份」档的年份分隔上要显示）
  state.yearCounts = {};
  state.dayCounts = new Map();
  (state.tree.months || []).forEach(m => {
    const y = m.ym.slice(0, 4);
    state.yearCounts[y] = (state.yearCounts[y] || 0) + m.n;
    (m.days || []).forEach(day => state.dayCounts.set(m.ym + '|' + day.d, day.n));
  });
  buildMonthSheet();
  resetRail();
  state.switching = false;
  if (!state.days.length) {
    $('#denCtl').hidden = true;
    renderEmpty('暂无图片', '这个图库里还没有可显示的图片。'); return;
  }
  // Seed complete fixed-size pages, including partial bootstrap days.
  if (pre && pre.items && pre.items.items && state.days.length) {
    const context = { ...state.days[0], lib: id, epoch };
    const items = pre.items.items, total = pre.items.total;
    for (let off = 0; off < items.length; off += PAGE_SIZE) {
      const page = items.slice(off, off + PAGE_SIZE);
      if (page.length === PAGE_SIZE || off + page.length >= total)
        rememberPage(context, off, { items: page, total });
    }
  }
  $('#denCtl').hidden = false;                // 时间轴出来了，密度控制器才露面
  syncDen();
  renderNextBatch();
  requestAnimationFrame(syncRail);
  // anchor：删除/恢复后的原地重建 → 回到原来那个日期分组；
  //         首次进入 / 切库（anchor=null）才回顶部。
  if (anchor) restoreViewAnchor(anchor); else window.scrollTo(0, 0);
}

/* =====================================================
   时间轴渲染
   ===================================================== */
function renderEmpty(title, sub, retry) {
  const t = $('#timeline');
  t.innerHTML = `<div class="empty">
    <div class="e-ico"><svg viewBox="0 0 24 24"><rect x="3" y="4.5" width="18" height="15" rx="3"/><circle cx="8.5" cy="10" r="1.6"/><path d="M3.5 17l5-4.5 4 3.5 2.5-2 5.5 4.5"/></svg></div>
    <div class="e-t">${esc(title)}</div><div class="e-s">${esc(sub)}</div>
    ${retry ? '<button id="retryBtn">重试</button>' : ''}</div>`;
  if (retry) $('#retryBtn').onclick = () => location.reload();
}

function renderNextBatch() {
  if (!state.tree || !state.lib || state.search.active || state.switching || state.done) return;
  if (wallMode() === 'month') return renderMonthBatch();
  if (wallMode() === 'year') return renderYearOverview();
  const from = state.renderedDayIdx;
  if (from >= state.days.length) {            // 越界保护（切库/竞态时会出现）
    state.done = true;
    $('#endTip').hidden = false;
    return;
  }
  const tl = $('#timeline');
  const frag = document.createDocumentFragment();
  const to = Math.min(state.days.length, from + TILE_BATCH);
  let lastYm = from > 0 ? state.days[from - 1].ym : null;
  let lastYr = lastYm ? lastYm.slice(0, 4) : null;
  const created = [];

  for (let i = from; i < to; i++) {
    const day = state.days[i];
    if (!day) break;
    // 年份分隔：**所有档位都渲染**，只有「年份」档用 CSS 显示出来
    //   —— 这样切档只换 class，不用重排 DOM，浏览位置才不会跳。
    const yr = day.ym.slice(0, 4);
    if (yr !== lastYr) {
      const ysep = document.createElement('div');
      ysep.className = 'year-sep';
      ysep.dataset.yr = yr;
      ysep.innerHTML = `${yr} 年<span>${countOfYear(yr)} 张</span>`;
      frag.appendChild(ysep);
      lastYr = yr;
      lastYm = null;                          // 年变了 → 月分隔必须重新出一次
    }
    if (day.ym !== lastYm) {
      const sep = document.createElement('div');
      sep.className = 'month-sep';
      sep.dataset.ym = day.ym;
      sep.innerHTML = `${fmtMonthLabel(day.ym)}<span>${countOfMonth(day.ym)} 张</span>`;
      frag.appendChild(sep);
      lastYm = day.ym;
    }
    const blk = makeDayBlock(day);
    created.push(blk);
    frag.appendChild(blk);
  }
  tl.appendChild(frag);
  sizeSkeletons(created);                     // ★ 骨架撑到真实高度
  state.renderedDayIdx = to;
  if (sel.active) syncDayPicks(created);      // 选择模式下新渲染的分组要接上勾选态

  if (to >= state.days.length) {
    state.done = true;
    $('#endTip').hidden = false;
  } else {
    requestAnimationFrame(() => { if (!state.switching && nearBottom(1800)) renderNextBatch(); });
  }
}
function countOfMonth(ym) {
  const m = (state.tree.months || []).find(x => x.ym === ym);
  return m ? m.n.toLocaleString() : '';
}
function countOfYear(yr) {
  const n = state.yearCounts[yr];
  return n == null ? '' : n.toLocaleString();
}

/* 日期分组的懒加载：滚到附近才去拉这一天的 items。
   余量随格子尺寸自适应（高密度档格子很小，余量必须收窄，
   否则一次会命中几十天 → 一屏打出几百个 /api/items）。 */
let groupObserver = null;
function makeGroupObserver() {
  return new IntersectionObserver(entries => {
    entries.forEach(e => {
      const grid = e.target.classList.contains('grid') ? e.target : e.target.querySelector('.grid');
      if (!grid || !grid.isConnected) return;
      if (!e.isIntersecting) { nearbyGrids.delete(grid); releaseGrid(grid); return; }
      nearbyGrids.add(grid);
      scheduleWindows();
    });
  }, { rootMargin: `${observerMargin()}px 0px` });
}
groupObserver = makeGroupObserver();
function rearmObserver() {
  if (!groupObserver) return;
  groupObserver.disconnect();
  nearbyGrids.clear();
  groupObserver = makeGroupObserver();
  // 持续观察轻量日期锚点，离屏时释放照片，返回时恢复附近行。
  $$('#timeline .day-blk').forEach(el => {
    const g = el.querySelector('.grid');
    if (g) groupObserver.observe(el);
  });
  // Collections and story albums use standalone window grids too.
  $$('#timeline .grid').forEach(grid => {
    if (grid.closest('.day-blk') || !grid._context) return;
    groupObserver.observe(grid);
    const r = grid.getBoundingClientRect();
    if (r.bottom >= -500 && r.top <= window.innerHeight + 500) nearbyGrids.add(grid);
  });
}

function makeDayBlock(day) {
  const el = document.createElement('section');
  el.className = 'day-blk';
  el.dataset.key = day.key; el.dataset.ym = day.ym; el.dataset.d = day.d;
  const total = dayCount(day);
  if (total) el.dataset.total = total;
  // 只建立日期和网格锚点；真实高度由 sizeSkeletons() 撑出来。
  el.innerHTML =
    `<div class="day-hd"><span>${fmtDayHead(day.ym, day.d)}</span>` +
    `<span class="d-n">${total != null ? total + ' 张' : ''}</span>` +
    `<label class="d-pick" title="选中 / 取消这一天的全部照片">` +
    `<input type="checkbox" class="d-pick-cb" aria-label="选中这一天"></label>` +
    `<span class="d-w">${weekdayOf(day.ym, day.d)}</span></div>` +
    '<div class="grid"></div>';
  groupObserver.observe(el);
  return el;
}

/* 把骨架网格撑到「真实张数」对应的高度。
   否则骨架（24 个）→ 真图（可能几百个）时分组突然长高，
   会把正在跳转/滚动的位置顶走上万像素。
   高度必须算准：tile 是 aspect-ratio:1，行高 = 单元格宽。 */
function sizeSkeletons(blocks) {
  blocks.forEach(el => {
    const grid = el.querySelector('.grid'), total = +el.dataset.total || 0;
    if (!grid || !total) return;
    if (grid._job) { grid._job.abort(); grid._job = null; }
    grid.classList.add('window-grid');
    grid.style.height = gridGeometry(grid, total).height + 'px';
    grid.style.minHeight = '';
    grid._range = '';
  });
  scheduleWindows();
}
function dayCount(day) {
  if (!day.d) return (state.tree.months || []).find(m => m.ym === day.ym)?.n ?? 0;
  if (state.dayCounts) return state.dayCounts.get(day.key || day.ym + '|' + day.d) ?? null;
  for (const m of (state.tree.months || [])) {
    if (m.ym === day.ym) {
      const x = (m.days || []).find(d => d.d === day.d);
      if (x) return x.n;
    }
  }
  return null;
}

async function loadDay(el) {
  if (!el.isConnected || state.search.active) return;
  const context = gridContext(el), grid = el.querySelector('.grid');
  return loadGridWindow(grid, context, +el.dataset.total || dayCount(context) || 0);
}

/* =====================================================
   缩略图节流器
   -----------------------------------------------------
   浏览器对同一个源只有 ~6 条并发连接。缓存被清空后快滚，一屏一屏地过，
   几百个 <img> 会同时被派发，连接池一路排到几百名之后 —— 此时用户点开大图，
   /photo 只能排在队尾（实测等 18.0s，比缩略图本身慢两个数量级）。
   所以缩略图不再直接写 src，而是进自己的队列：
     · 由观察器判定「离视口够近」才入队（取代 loading=lazy，避免双重延迟）
     · 同时最多 THUMB_MAX 个在传 —— 滚得再快也攒不出几百个积压
     · 打开灯箱时整体挂起，并撤回在途请求，连接池全部让给大图

   ★ 并发上限自适应（2026-10-12）：
     6 是 **HTTP/1.1「同源 6 连接」的硬限制**，LAN / EasyTier 下这是正确上限；
     但在 CF Tunnel / 公网这类高延迟链路上，浏览器↔CDN 走 HTTP/2 多路复用、
     没有 6 连接限制，此时 6 反而成了**人为的串行瓶颈**。
     所以上限按**实测往返延迟**决定，绝不按协议/域名猜：
       · 默认 THUMB_MAX_LOW（=6）—— 局域网、EasyTier 的现有体验完全不变；
       · 仅当**成功**缩略图请求的往返**中位数** > RTT_HIGH_MS 时放宽到 THUMB_MAX_HIGH；
       · **失败 / 中断的请求一律不计入样本** —— 一次失败不足以改变链路判断；
       · 样本不足 RTT_MIN_SAMPLES 之前保持默认（宁可保守）；
       · 中位数会在滑动窗口里随后的样本里回落 → 误判会自行纠正回 6。
   ===================================================== */
const THUMB_MAX_LOW = 6;              // 低延迟链路（LAN / EasyTier）：HTTP/1.1 单源上限
const THUMB_MAX_HIGH = 20;            // 高延迟链路（隧道 / 公网）：多路复用下可放宽
const RTT_HIGH_MS = 500;              // 往返中位数超过它才认定「高延迟」
const RTT_MIN_SAMPLES = 6;            // 至少这么多个**成功**样本才敢改判
const THUMB_RTT_WINDOW = 24;          // 滑动窗口长度（中位数只受最近样本影响）
/* 仅供性能测试用：?slthumbmax=N 固定并发上限（越界/非法 → 0 = 走自适应）。
   纯前端、不参与任何鉴权；方便在真实链路上 A/B 对比 6 / 12 / 20。 */
const THUMB_MAX_FIXED = (() => {
  const v = parseInt(QS.get('slthumbmax'), 10);
  return (v > 0 && v <= 32) ? v : 0;
})();
let THUMB_MAX = THUMB_MAX_FIXED || THUMB_MAX_LOW;
const thumbRtt = [];                  // 成功请求的往返耗时样本（ms）

function noteThumbRtt(ms) {
  if (!(ms > 0) || !isFinite(ms)) return;          // 失败/中断：不记
  thumbRtt.push(ms);
  if (thumbRtt.length > THUMB_RTT_WINDOW) thumbRtt.shift();
  if (THUMB_MAX_FIXED || thumbRtt.length < RTT_MIN_SAMPLES) return;
  const s = thumbRtt.slice().sort((a, b) => a - b);
  const median = s[s.length >> 1];
  const want = median > RTT_HIGH_MS ? THUMB_MAX_HIGH : THUMB_MAX_LOW;
  if (want !== THUMB_MAX) { THUMB_MAX = want; thumbPump(); }
}

const THUMB_AHEAD = 200;              // 视口外多远开始预取（px）
const thumbViewId = (crypto.randomUUID ? crypto.randomUUID() :
  Date.now().toString(36) + Math.random().toString(36).slice(2) + 'seeklive');
let thumbEpoch = 0;
function thumbAdvance() {
  thumbEpoch++;
  // Every surviving visible request must carry the new epoch as well.
  [...thumbLive].forEach(img => {
    thumbDefer(img);
    if (img.isConnected && thumbDistance(img) <= THUMB_AHEAD) {
      thumbWaiting.add(img); thumbQueue.push(img);
    }
  });
  // The small notice invalidates old server-side waiters even before new day metadata arrives.
  api('/api/thumb/view', { _slview: thumbViewId, _slepoch: thumbEpoch }).catch(() => {});
}
function thumbRequestURL(url) {
  return url + (url.includes('?') ? '&' : '?') +
    '_slview=' + encodeURIComponent(thumbViewId) + '&_slepoch=' + thumbEpoch;
}
const thumbQueue = [];                // 待发元素
const thumbWaiting = new Set();       // 已入队（防重复）
const thumbLive = new Set();          // 在传
let thumbHeld = false;                // 灯箱打开时挂起

/* 诊断用（只读）：当前并发上限与链路估计，便于回答「为什么并发是这个数」 */
window.__slThumbNet = () => {
  const s = thumbRtt.slice().sort((a, b) => a - b);
  return { max: THUMB_MAX, fixed: THUMB_MAX_FIXED, samples: s.length,
           waiting: thumbQueue.length, active: thumbLive.size,
           median: s.length ? Math.round(s[s.length >> 1]) : null };
};

const thumbObserver = new IntersectionObserver(entries => {
  if (entries.some(e => !e.isIntersecting && thumbLive.has(e.target) && thumbDistance(e.target) > THUMB_AHEAD)) thumbAdvance();
  entries.forEach(e => {
    if (!e.isIntersecting) { if (thumbDistance(e.target) > THUMB_AHEAD) thumbDefer(e.target); return; }
    thumbProbe(e.target);
  });
}, { rootMargin: `${THUMB_AHEAD}px 0px` });

function thumbDistance(img) {
  const r = img.getBoundingClientRect();
  return Math.max(0, -r.bottom, r.top - window.innerHeight);
}
function thumbDefer(img) {
  thumbWaiting.delete(img);
  const i = thumbQueue.indexOf(img);
  if (i >= 0) thumbQueue.splice(i, 1);
  if (thumbLive.delete(img)) {
    img.onload = img.onerror = null;
    img.removeAttribute('src');
  }
}
function thumbReprioritize() {
  if ([...thumbLive].some(img => !img.isConnected || thumbDistance(img) > THUMB_AHEAD))
    thumbAdvance();
  [...thumbQueue, ...thumbLive].forEach(img => {
    if (!img.isConnected || thumbDistance(img) > THUMB_AHEAD) thumbDefer(img);
  });
  thumbPump();
}
let thumbScrollFrame = 0;
window.addEventListener('scroll', () => {
  if (!thumbScrollFrame) thumbScrollFrame = requestAnimationFrame(() => {
    thumbScrollFrame = 0; thumbReprioritize();
  });
}, { passive: true });

function thumbProbe(img) {
  if (!img || !img.isConnected || !img.dataset.thumbUrl) return;
  if (thumbWaiting.has(img) || thumbLive.has(img)) return;
  thumbWaiting.add(img);
  thumbQueue.push(img);
  thumbPump();
}
function thumbPump() {
  if (thumbHeld) return;
  thumbQueue.sort((a, b) => thumbDistance(a) - thumbDistance(b));
  while (thumbLive.size < THUMB_MAX && thumbQueue.length) {
    const img = thumbQueue.shift();
    thumbWaiting.delete(img);
    if (!img.isConnected || !img.dataset.thumbUrl || thumbDistance(img) > THUMB_AHEAD) continue;
    thumbStart(img);
  }
}
function thumbStart(img) {
  thumbLive.add(img);
  const tile = img.closest('.tile,.cover-cell');
  const t0 = performance.now();
  img.onload = () => {
    img.onload = img.onerror = null;
    thumbLive.delete(img);
    noteThumbRtt(performance.now() - t0);      // 只用**成功**请求估链路延迟
    thumbObserver.unobserve(img);
    if (tile) tile.classList.add('loaded');
    fitTile(img);
    thumbPump();
  };
  img.onerror = () => {
    img.onload = img.onerror = null;
    thumbLive.delete(img);
    fallbackThumb(img, tile, tile && tile.item, THUMB_SIZE);
    thumbPump();
  };
  img.src = thumbRequestURL(img.dataset.thumbUrl);
}
/* 挂起 / 恢复。挂起时把在途的**撤回**（取消请求、腾出连接），URL 留在
   data-thumb-url 上，灯箱关掉后自动重排 —— 正在看的那张大图因此独占连接池。 */
function thumbHold(on) {
  if (on === thumbHeld) return;
  thumbHeld = on;
  if (on) thumbAdvance();
  if (!on) { thumbPump(); return; }
  Array.from(thumbLive).forEach(img => {
    thumbLive.delete(img);
    img.onload = img.onerror = null;
    if (!img.isConnected) return;
    if (img.complete && img.naturalWidth) return;     // 已经出来的不动它
    img.removeAttribute('src');                       // ★ 真正取消在途请求
    if (!thumbWaiting.has(img)) { thumbWaiting.add(img); thumbQueue.push(img); }
  });
}
/* 时间轴整体重建前调用：清掉队列并断开观察，避免旧节点被观察器一直持有 */
function thumbReset() {
  thumbAdvance();
  thumbObserver.disconnect();
  thumbQueue.length = 0;
  thumbWaiting.clear();
  thumbLive.forEach(img => { img.onload = img.onerror = null; img.removeAttribute('src'); });
  thumbLive.clear();
}


/* 缩略图加载失败：先静默重试一次（服务端偶发抽帧失败 / 缓存竞态），
   仍失败才落成明确占位（视频给播放图标），绝不留「空白格 / 问号」。
   ★ 单档后不再有 srcset 需要摘除，重试就是给同一个 640 URL 加一个破缓存参数。
   ★ 重试同样走节流队列，否则一批失败重试会瞬间再打几百个请求。 */
function fallbackThumb(img, tile, it, side) {
  if (!img || !img.isConnected) return;      // 已被替换 → 不再处理
  if (!it) return;
  if (img.dataset.retried !== '1') {
    img.dataset.retried = '1';
    img.dataset.thumbUrl = mediaURL('/thumb', it.p, `&s=${side}&r=${Date.now()}`);
    thumbProbe(img);
    return;
  }
  if (!tile) return;
  tile.classList.add('loaded');
  tile.style.background = 'var(--bg-3)';
  const ph = document.createElement('span');
  ph.className = 'tile-ph' + (it.k === 'vid' ? ' vid' : '');
  ph.innerHTML = it.k === 'vid'
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6.5l9 5.5-9 5.5z"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4z"/>' +
      '<path d="M4 15l4-4 3 3 3-3 6 6"/></svg>';
  img.replaceWith(ph);
}

/* 竖长图（截图 / 文档）用 contain 完整展示；横向照片用 cover 铺满 */
function fitTile(img) {
  if (img.naturalWidth && img.naturalHeight > img.naturalWidth * 1.35) {
    img.style.objectFit = 'contain';
  }
}

/* 网格点击 → 选择模式勾选 / 灯箱 */
document.addEventListener('click', e => {
  const a = e.target.closest('.tile');
  if (!a) return;
  e.preventDefault();
  if (sel.active) { toggleTileSel(a); return; }
  const key = a.dataset.ctx;
  if (!a.item || !a.browseContext) return;
  openPagedViewer(a.browseContext, +a.dataset.idx, a.item);
});

/* 哨兵 + 滚动 */
function nearBottom(margin) {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - margin;
}
new IntersectionObserver(es => {
  if (es.some(x => x.isIntersecting)) renderNextBatch();
}, { rootMargin: '1400px 0px' }).observe($('#sentinel'));

let densityScrollY = 0;
function revealDensity() { $('#denCtl').classList.remove('den-resting'); }
window.addEventListener('scroll', () => {
  const y = window.scrollY, delta = y - densityScrollY;
  if (Math.abs(delta) < 12) return;
  $('#denCtl').classList.toggle('den-resting', delta > 0 && y > 260);
  densityScrollY = y;
}, { passive: true });
$('#denCtl').addEventListener('focusin', revealDensity);
$('#denCtl').addEventListener('pointerdown', revealDensity);
document.addEventListener('keydown', e => { if (e.key === 'Tab') revealDensity(); });
let lastY = 0;
window.addEventListener('scroll', () => {
  const y = window.scrollY;
  $('#topbar').classList.toggle('scrolled', y > 4);
  const show = y > 900;
  const t = $('#toTop');
  if (t.hidden !== !show) t.hidden = !show;
  lastY = y;
  onRailScroll();
}, { passive: true });
$('#toTop').onclick = () => window.scrollTo({ top: 0, behavior: 'smooth' });
function hideToTop() { $('#toTop').hidden = true; }

/* =====================================================
   搜索
   ===================================================== */
$('#btnSearch').onclick = () => {
  const bar = $('#searchBar');
  bar.hidden = !bar.hidden;
  if (!bar.hidden) setTimeout(() => $('#searchInput').focus(), 40);
  syncTopbarHeight();
};
$('#searchCancel').onclick = () => exitSearch();

let searchTimer = null;
$('#searchInput').addEventListener('input', e => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  searchTimer = setTimeout(() => runSearch(q), 220);
});

async function runSearch(q) {
  if (!q) { exitSearch(); return; }
  resetCollection();
  ++state.viewEpoch;
  resetBrowseRequests();
  if (!viewer.hidden) closeViewer();
  state.search.request = null;
  state.search.active = true;
  rail.hidden = true; syncRailHeight();
  state.search.q = q; state.search.off = 0; state.search.done = false;
  $('#endTip').hidden = true;
  thumbReset();
  $('#timeline').innerHTML = '<div class="month-sep">搜索结果</div><div class="grid" id="searchGrid"></div>';
  window.scrollTo(0, 0);
  await loadSearchPage();
}

async function loadSearchPage() {
  const grid = $('#searchGrid');
  if (!grid || state.search.done || state.search.request) return;
  const request = {};
  state.search.request = request;
  const lib = state.lib, q = state.search.q, epoch = state.viewEpoch;
  const context = { lib, q, epoch, key: 'search-results' };
  const current = () => state.search.request === request && state.lib === lib &&
    state.search.q === q && state.viewEpoch === epoch && $('#searchGrid') === grid;
  try {
    const r = await fetchBrowsePage(context, 0);
    if (!current()) return;
    state.search.total = r.total; state.search.done = true;
    $('.month-sep').innerHTML = `搜索结果 <span>${r.total.toLocaleString()} 张</span>`;
    if (!r.total) {
      grid.outerHTML = '<div class="empty"><div class="e-t">没有找到匹配的图片</div>' +
        '<div class="e-s">换个关键词试试</div></div>';
    } else {
      groupObserver.observe(grid); nearbyGrids.add(grid);
      await loadGridWindow(grid, context, r.total);
    }
  } catch (e) {
    if (current() && e.name !== 'AbortError') {
      grid.innerHTML = '<button class="day-retry">搜索失败，点击重试</button>';
      grid.querySelector('button').onclick = () => loadSearchPage();
    }
  } finally { if (state.search.request === request) state.search.request = null; }
}


function exitSearch() {
  resetCollection();
  clearTimeout(searchTimer);
  ++state.viewEpoch;
  resetBrowseRequests();
  if (!viewer.hidden) closeViewer();
  state.search.request = null;
  state.search.active = false;
  $('#searchBar').hidden = true;
  $('#searchInput').value = '';
  syncTopbarHeight();
  const tl = $('#timeline');
  thumbReset();
  tl.innerHTML = '';
  state.renderedDayIdx = 0; state.done = false;
  $('#endTip').hidden = true;
  renderNextBatch();
  resetRail();
  requestAnimationFrame(syncRail);
}

window.addEventListener('scroll', () => {
  if (!state.search.active || state.search.done) return;
  if (nearBottom(900)) loadSearchPage();
}, { passive: true });

/* =====================================================
   灯箱
   ===================================================== */
const viewer = $('#viewer');
const vMedia = $('#vMedia');
let lpkReady = null;
let viewerRevision = 0;
let viewerRequest = null;
let neighborImage = null;
function viewerCurrent(revision, item) {
  return !viewer.hidden && revision === viewerRevision && (!item || currentItem() === item);
}
function openPagedViewer(context, index, item) {
  state.viewer.context = { ...context };
  state.viewer.offset = 0;
  state.viewer.item = item;
  openViewer([], index);
}

/* ---- 实况声音偏好 ----
   默认静音，因为「静音 + playsinline」是浏览器允许自动播放的前提。
   iOS 上还有个额外的事实：Apple 的 LivePhotosKit 根本不出声 —— 它内部把
   video.muted = true、video.volume = 0，再把帧画到 canvas 上（源码可查），
   所以想听声音必须换回原生 <video>。点「声音」按钮即完成这次切换。 */
let liveMuted = true;
let soundWarned = false;
try { liveMuted = localStorage.getItem('liveMuted') !== '0'; } catch (e) {}

function setLiveMuted(m, persist = true) {
  liveMuted = m;
  if (persist) { try { localStorage.setItem('liveMuted', m ? '1' : '0'); } catch (e) {} }
  const b = $('#vSound');
  if (b) {
    b.classList.toggle('sound-on', !m);
    b.title = m ? '实况声音：已静音（点击开启）' : '实况声音：已开启（点击静音）';
    b.setAttribute('aria-label', b.title);
  }
}

function ensureLPK() {
  if (lpkReady) return lpkReady;
  lpkReady = new Promise(res => {
    if (window.LivePhotosKit) return res(true);
    const s = document.createElement('script');
    s.src = 'https://cdn.apple-livephotoskit.com/lpk/1/livephotoskit.js';
    s.onload = () => res(!!window.LivePhotosKit);
    s.onerror = () => res(false);
    document.head.appendChild(s);
    setTimeout(() => res(!!window.LivePhotosKit), 6000);
  });
  return lpkReady;
}

function openViewer(list, idx) {
  state.viewer.list = list;
  state.viewer.i = idx;
  viewer.hidden = false;
  document.body.style.overflow = 'hidden';
  // ★ 用户要看大图了：先把手上的缩略图全部挂起并撤回，把 6 条连接让出来。
  //   否则大图请求会排在那几百个缩略图后面（实测 18s）。
  thumbHold(true);
  $$('#timeline .grid').forEach(grid => {
    if (grid._job) { grid._job.abort(); grid._job = null; grid._range = ''; }
  });
  $('#vDetail').hidden = true;
  const meta = libMeta() || {};
  $('#vDel').hidden = IS_SHARE() || !meta.canManage || !admin.required;  // 分享访客永不可删
  $('#vDel').title = IS_SHARE() ? '分享访客不能删除照片'
    : (!meta.canManage ? (meta.manageReason || '')
      : (!admin.required ? '未配置管理口令，删除功能已关闭' : '移入回收站'));
  showSlide(idx, 0);
}
function closeViewer() {
  viewer.hidden = true;
  document.body.style.overflow = '';
  stopMedia();
  vMedia.innerHTML = '';
  state.viewer.scale = 1; state.viewer.tx = 0; state.viewer.ty = 0;
  state.viewer.list = []; state.viewer.item = null; state.viewer.context = null;
  thumbHold(false);
  scheduleWindows();
}

function stopMedia() {
  ++viewerRevision;
  if (viewerRequest) { viewerRequest.abort(); viewerRequest = null; }
  if (neighborImage) { neighborImage.onload = neighborImage.onerror = null; neighborImage.removeAttribute('src'); neighborImage = null; }
  $$('img', vMedia).forEach(img => { img.onload = img.onerror = null; img.removeAttribute('src'); });
  $$('video', vMedia).forEach(v => {
    v.onplaying = v.onwaiting = v.oncanplay = v.onerror = null;
    try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) {}
  });
  if (state.viewer.lpk) {
    try { state.viewer.lpk.destroy(); } catch (e) {}
    state.viewer.lpk = null;
  }
  $$('.lpk-host', vMedia).forEach(el => el.remove());
}

/* 拆掉画面上的实况层（LPK 实例 + motion 视频），静图保留。
   用于「声音开关」在两个播放引擎之间切换。 */
function detachMotion() {
  ++viewerRevision;
  if (state.viewer.lpk) {
    try { state.viewer.lpk.destroy(); } catch (e) {}
    state.viewer.lpk = null;
  }
  $$('.lpk-host,video.v-motion,.v-motion-status', vMedia).forEach(el => {
    if (el.tagName === 'VIDEO') { el.onerror = null; el.pause(); el.removeAttribute('src'); el.load(); }
    el.remove();
  });
  vMedia.classList.remove('motion');
}
function currentItem() { return state.viewer.item || null; }

async function showSlide(idx, dir) {
  const context = state.viewer.context;
  if (!context || viewer.hidden) return;
  const total = context.total || 0;
  if (!total) return;
  const absolute = ((state.viewer.offset || 0) + idx + total) % total;
  stopMedia();
  const revision = viewerRevision;
  state.viewer.i = absolute - (state.viewer.offset || 0);
  state.viewer.item = null;
  $('#vLive').hidden = true; $('#vSound').hidden = true;
  vMedia.innerHTML = '<div class="v-loading" role="status">正在加载照片…</div>';
  $('#vTitle').textContent = `${absolute + 1} / ${total}`;
  $('#vCaption').textContent = '';
  const request = new AbortController(), parent = browseController.signal;
  const cancel = () => request.abort();
  parent.addEventListener('abort', cancel, { once: true });
  if (parent.aborted) request.abort();
  viewerRequest = request;
  try {
    const off = Math.floor(absolute / PAGE_SIZE) * PAGE_SIZE;
    const page = await fetchBrowsePage(context, off, request.signal);
    if (!viewerCurrent(revision) || request.signal.aborted) return;
    if (!page.items[absolute - off]) throw new Error('照片索引已变化，请刷新相册');
    state.viewer.offset = off;
    state.viewer.list = page.items;
    renderSlide(absolute - off, dir);
  } catch (e) {
    if (!viewerCurrent(revision) || e.name === 'AbortError') return;
    vMedia.innerHTML = '<div class="v-loading"><button class="v-load-retry">加载失败，点击重试</button></div>';
    vMedia.querySelector('button').onclick = () => showSlide(state.viewer.i, 0);
  } finally {
    parent.removeEventListener('abort', cancel);
    if (viewerRequest === request) viewerRequest = null;
  }
}

function renderSlide(idx, dir) {
  const list = state.viewer.list;
  if (!list.length) return;
  state.viewer.i = (idx + list.length) % list.length;
  state.viewer.scale = 1; state.viewer.tx = 0; state.viewer.ty = 0;
  applyTransform();
  state.viewer.item = list[state.viewer.i];
  const it = currentItem();
  stopMedia();
  const revision = viewerRevision;
  vMedia.innerHTML = '';
  vMedia.classList.remove('motion');

  const n = (state.viewer.offset || 0) + state.viewer.i + 1, N = state.viewer.context.total;
  $('#vTitle').textContent = `${n} / ${N}`;
  $('#vCaption').innerHTML =
    `<div>${fmtFull(it.t)}</div><div class="c2">${esc(it.n)} · ${fmtSize(it.z)}</div>`;
  $('#vLive').hidden = !it.live;
  $('#vSound').hidden = !it.live;
  setLiveMuted(liveMuted, false);            // 同步喇叭图标

  // 播放按钮标签
  $('#vLive').classList.toggle('offline', false);

  if (it.k === 'vid') {
    // ── 独立视频（单 MOV / MP4）─────────────────────────────
    // 能力路由：服务端已探测码流（it.c）。H.264 等通用码流直接原样播放，
    // 省掉一次转码；HEVC 在 iOS/Safari 原生直出，其它浏览器懒转码 H.264。
    // 直出失败（例如探测不准）→ onerror 自动兜底切到转码，用户无感。
    const v = document.createElement('video');
    v.className = 'v-video';
    v.controls = true; v.playsInline = true; v.preload = 'metadata';
    v.setAttribute('playsinline', '');
    v.setAttribute('webkit-playsinline', '');
    v.poster = thumbURL(it.p);

    let useTrans = (it.c === 'hevc' || !it.c) ? !canPlayHevc() : false;
    const setSrc = () => { v.src = mediaURL('/video', it.p, useTrans ? '&t=1' : ''); };
    setSrc();
    const status = document.createElement('div');
    status.className = 'v-loading'; status.textContent = useTrans ? '正在准备视频，首次转码可能需要一些时间…' : '正在加载视频…';
    vMedia.appendChild(status);
    v.onplaying = () => { if (viewerCurrent(revision, it)) status.remove(); };
    v.oncanplay = () => { if (viewerCurrent(revision, it)) status.remove(); };
    v.onwaiting = () => { if (viewerCurrent(revision, it) && !status.isConnected) vMedia.appendChild(status); };
    v.onerror = () => {
      if (!viewerCurrent(revision, it)) return;
      if (!useTrans) {                      // 直出失败 → 兜底转码一次
        useTrans = true; status.textContent = '正在准备视频，首次转码可能需要一些时间…'; setSrc(); v.play().catch(() => {});
      } else if (!v.dataset.failed) {       // 转码也不行（或体积超限）→ 明确告知
        v.dataset.failed = '1'; status.textContent = '视频无法播放，可使用右上角下载原文件';
        toast('该视频无法在线播放（体积过大或格式不支持），可点右上角下载', 4200);
      }
    };
    vMedia.appendChild(v);
    v.play().catch(() => {});
    $('#vLive').hidden = true;
    preloadNeighbors();
  } else {
    // 缩略图先占位（已在缓存里，秒出），再叠加高清
    const wrap = document.createElement('div');
    wrap.className = 'v-still';
    const low = document.createElement('img');
    low.className = 'v-low'; low.alt = '';
    low.src = thumbURL(it.p);
    const hi = document.createElement('img');
    hi.className = 'v-hi'; hi.alt = esc(it.n);
    hi.onload = () => {
      if (!viewerCurrent(revision, it)) return;
      hi.classList.add('ready'); wrap.classList.add('done');
      preloadNeighbors();                      // 当前这张到了，再去预取下一张
    };
    hi.onerror = () => {
      if (!viewerCurrent(revision, it)) return;
      low.style.filter = 'none';
      const retry = document.createElement('button');
      retry.className = 'v-load-retry'; retry.textContent = '高清图加载失败，点击重试';
      retry.style.cssText = 'position:absolute;bottom:90px;z-index:5';
      retry.onclick = () => { retry.remove(); hi.src = mediaURL('/photo', it.p, '&fmt=jpeg&r=' + Date.now()); };
      wrap.appendChild(retry);
    };
    hi.src = mediaURL('/photo', it.p, '&fmt=jpeg');
    wrap.appendChild(low); wrap.appendChild(hi);
    vMedia.appendChild(wrap);

    if (it.live) attachLive(it);
  }
  setChromeVisible(true);
}

/* ---- Live Photo 播放 ----
   iOS Safari → Apple 原生 LivePhotosKit（长按有"实况"动效 + 防抖对齐）
               但 LPK 是**设计上就静音**的（内部 muted + volume=0，画到 canvas），
               所以「打开声音」时会切到下面的 <video> 分支。
   其它浏览器 → 静图 + H.264 动图视频叠加（无 CDN 依赖，离线可用）
   注：photoSrc 统一用服务端转码的 JPEG —— 原始 HEIC 在非 Safari 上解不出，
       这是原型阶段已经踩过的坑。 */
function attachLive(it) {
  const revision = viewerRevision;
  if (IS_IOS && liveMuted) {
    ensureLPK().then(ok => {
      if (!viewerCurrent(revision, it)) return;
      if (!ok) return fallbackMotion(it);
      try {
        const host = document.createElement('div');
        host.className = 'lpk-host';
        host.style.cssText =
          'position:absolute;inset:0;margin:auto;width:100%;height:100%';
        vMedia.appendChild(host);
        const pl = window.LivePhotosKit.Player(host);
        pl.photoSrc = mediaURL('/photo', it.p, '&fmt=jpeg');
        pl.videoSrc = mediaURL('/video', it.mv || it.p, canPlayHevc() ? '' : '&t=1');
        pl.play();
        state.viewer.lpk = pl;
      } catch (e) { fallbackMotion(it); }
    });
  } else {
    fallbackMotion(it);
  }
}

function fallbackMotion(it) {
  const revision = viewerRevision;
  if (!viewerCurrent(revision, it)) return;
  const status = document.createElement('button');
  status.className = 'v-motion-status'; status.textContent = '正在准备实况…';
  status.disabled = true;
  vMedia.appendChild(status);
  const v = document.createElement('video');
  v.className = 'v-motion';
  // 静音是「自动播放」的前提（Chrome / iOS 都只放行 muted + playsinline）；
  // 用户点过喇叭之后才尝试带声自动播，被策略拦下就再退回静音。
  v.muted = liveMuted;
  v.loop = true; v.playsInline = true;
  v.autoplay = true; v.preload = 'auto';
  v.setAttribute('playsinline', '');
  v.setAttribute('webkit-playsinline', '');
  // 桌面 / Android：浏览器普遍不认 HEVC → 走 H.264 转码；
  // 转码顺带把 iPhone 的旋转矩阵烘进画面，方向与静图一致。
  v.src = mediaURL('/video', it.mv || it.p, canPlayHevc() ? '' : '&t=1');
  v.onplaying = v.oncanplay = () => { if (viewerCurrent(revision, it)) status.remove(); };
  // 转码失败（服务端没装 ffmpeg 会返回 503）时别把黑盒子留给用户：
  // 撤掉 .motion 让静图继续显示，并说清楚原因。
  v.onerror = () => {
    if (!viewerCurrent(revision, it)) return;
    v.dataset.failed = '1';
    vMedia.classList.remove('motion');
    $('#vLive').classList.toggle('offline', false);
    v.style.display = 'none';
    status.textContent = '实况加载失败，点击重试'; status.disabled = false;
    if (!status.isConnected) vMedia.appendChild(status);
    status.onclick = () => { if (viewerCurrent(revision, it)) { detachMotion(); attachLive(it); } };
    toast('实况视频暂时无法播放，可重试或下载原文件', 5000);
  };
  vMedia.appendChild(v);
  v.play().then(() => {
    if (!viewerCurrent(revision, it) || v.dataset.failed) return;
    vMedia.classList.add('motion');
    $('#vLive').hidden = false;
  }).catch(() => {
    if (!viewerCurrent(revision, it)) return;
    if (!v.muted) {
      // 带声自动播放被浏览器拦下 → 静音重试一次（声音偏好这次不落盘）
      v.muted = true;
      setLiveMuted(true, false);
      if (!soundWarned) {
        soundWarned = true;
        toast('浏览器不允许自动带声播放，已静音；点喇叭可手动开声', 3000);
      }
      v.play().catch(() => {});
    }
    // 自动播放被拒时，保留 LIVE 徽标，用户点一下即可
    $('#vLive').hidden = false;
  });
}

$('#vLive').onclick = () => {
  const it = currentItem();
  if (!it || !it.live) return;
  const v = vMedia.querySelector('video.v-motion');
  if (v && v.dataset.failed) {              // 源已失效，别给用户一个黑盒子
    toast('实况视频不可用：服务端缺少 ffmpeg 或文件格式不支持', 3200);
    return;
  }
  const on = vMedia.classList.toggle('motion');
  if (v) { on ? v.play().catch(() => {}) : v.pause(); }
  // iOS 走 LPK 时没有 <video> 可点，得直接控制播放器实例
  const pl = state.viewer.lpk;
  if (pl && !v) { try { on ? pl.play() : pl.pause(); } catch (e) {} }
  $('#vLive').classList.toggle('offline', !on);
  toast(on ? '实况已播放' : '已暂停实况', 1200);
};

/* 声音开关：在「LPK（静音、有原生手感）」与「原生视频（有声）」之间切换 */
$('#vSound').onclick = () => {
  const it = currentItem();
  if (!it || !it.live) return;
  setLiveMuted(!liveMuted);
  detachMotion();
  attachLive(it);
  toast(liveMuted ? '实况已静音' : '实况声音已开启', 1400);
};

function preloadNeighbors() {
  const context = state.viewer.context, revision = viewerRevision;
  if (!context || !viewerCurrent(revision)) return;
  const absolute = ((state.viewer.offset || 0) + state.viewer.i + 1) % context.total;
  const off = Math.floor(absolute / PAGE_SIZE) * PAGE_SIZE;
  const page = cachedPage(context, off);
  // Metadata is fetched when navigating; speculative work never competes with it.
  const it = page && page.items[absolute - off];
  if (!it || it.k === 'vid') return;
  if (neighborImage) neighborImage.removeAttribute('src');
  neighborImage = new Image();
  neighborImage.src = mediaURL('/photo', it.p, '&fmt=jpeg');
}

/* ---- 缩放 / 平移 ---- */
function applyTransform() {
  const { scale, tx, ty } = state.viewer;
  vMedia.style.transform = `translate3d(${tx}px,${ty}px,0) scale(${scale})`;
  vMedia.style.transition = 'transform .3s var(--ease)';
}
function setChromeVisible(v) {
  viewer.classList.toggle('imm', !v);
}
viewer.addEventListener('click', e => {
  if (e.target.closest('.v-btn,.v-nav,.v-detail,.v-media img,.v-media video')) return;
  setChromeVisible(viewer.classList.contains('imm'));
});

/* ---- 触摸：滑动切换 + 双指缩放 ---- */
let touch = null;
const stage = $('#vStage');
stage.style.touchAction = 'none';

stage.addEventListener('touchstart', e => {
  if (e.touches.length === 1) {
    touch = { x: e.touches[0].clientX, y: e.touches[0].clientY,
              dx: 0, dy: 0, t: Date.now(), mode: 'swipe', lastTap: touch && touch.lastTap };
    if (Date.now() - (touch.lastTap || 0) < 300) touch.mode = 'dbl';
  } else if (e.touches.length === 2) {
    const d = Math.hypot(
      e.touches[0].clientX - e.touches[1].clientX,
      e.touches[0].clientY - e.touches[1].clientY);
    touch = { mode: 'zoom', d0: d, s0: state.viewer.scale };
  }
}, { passive: true });

stage.addEventListener('touchmove', e => {
  if (!touch) return;
  if (touch.mode === 'zoom' && e.touches.length === 2) {
    const d = Math.hypot(
      e.touches[0].clientX - e.touches[1].clientX,
      e.touches[0].clientY - e.touches[1].clientY);
    state.viewer.scale = Math.min(5, Math.max(1, touch.s0 * d / touch.d0));
    vMedia.style.transition = 'none';
    applyTransform();
    return;
  }
  if (touch.mode === 'swipe' && e.touches.length === 1) {
    touch.dx = e.touches[0].clientX - touch.x;
    touch.dy = e.touches[0].clientY - touch.y;
    if (state.viewer.scale <= 1 && Math.abs(touch.dx) > 6 && Math.abs(touch.dx) > Math.abs(touch.dy)) {
      vMedia.style.transition = 'none';
      vMedia.style.transform = `translate3d(${touch.dx}px,0,0)`;
    }
  }
}, { passive: true });

stage.addEventListener('touchend', e => {
  if (!touch) return;
  if (touch.mode === 'zoom') { touch = null; applyTransform(); return; }
  if (touch.mode === 'dbl') {
    state.viewer.scale = state.viewer.scale > 1 ? 1 : 2.4;
    applyTransform(); touch = null; return;
  }
  const dx = touch.dx, dt = Date.now() - touch.t;
  if (state.viewer.scale <= 1 && Math.abs(dx) > 55 && dt < 900) {
    showSlide(state.viewer.i + (dx < 0 ? 1 : -1), dx);
  } else {
    applyTransform();
  }
  if (touch && Math.abs(dx) < 8 && dt < 280) {
    setChromeVisible(viewer.classList.contains('imm'));
  }
  if (touch) touch.lastTap = Date.now() + 0;
  touch = null;
}, { passive: true });

/* ---- 灯箱按钮 ---- */
$('#vClose').onclick = closeViewer;
$('#vPrev').onclick = () => showSlide(state.viewer.i - 1, -1);
$('#vNext').onclick = () => showSlide(state.viewer.i + 1, 1);
$('#vDownload').onclick = () => {
  const it = currentItem(); if (!it) return;
  const a = document.createElement('a');
  a.href = mediaURL('/raw', it.p);
  a.download = it.n;
  document.body.appendChild(a); a.click(); a.remove();
};
$('#vInfoBtn').onclick = () => openDetail(currentItem());
$('#vdClose').onclick = () => { $('#vDetail').hidden = true; };
$('#vDetail').addEventListener('click', e => {
  if (e.target.id === 'vDetail') $('#vDetail').hidden = true;
});

function openDetail(it) {
  if (!it) return;
  const img = vMedia.querySelector('img');
  const dim = img && img.naturalWidth ? `${img.naturalWidth} × ${img.naturalHeight}` : '—';
  const rows = [
    ['文件名', esc(it.n)],
    ['时间', fmtFull(it.t)],
    ['大小', fmtSize(it.z)],
    ['分辨率', dim],
    ['类型', it.live ? '实况照片 (Live Photo)'
      : (it.k === 'vid' ? ('视频' + (it.c ? ' · ' + it.c.toUpperCase() : '')) : '图片')],
    ['配对方式', it.live ? (it.pair === 'cid' ? 'Content Identifier 严格匹配' : '同名 + Live 标记') : '—'],
  ];
  // 分享访客看不到图库内相对路径（属管理信息）
  if (!IS_SHARE()) rows.push(['路径', `<span class="mono">${esc(it.p)}</span>`]);
  $('#vdList').innerHTML = rows.map(
    ([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  $('#vdList').closest('.vd-card').querySelector('.vd-head').textContent = '详情';
  $('#vDetail').hidden = false;
}

/* ---- 键盘 ---- */
document.addEventListener('keydown', e => {
  // 详情抽屉可在灯箱关闭时独立打开（顶栏 ⓘ），Escape 优先关它
  if (e.key === 'Escape' && !$('#vDetail').hidden) { $('#vDetail').hidden = true; return; }
  if (viewer.hidden) return;
  if (e.key === 'Escape') closeViewer();
  if (e.key === 'ArrowLeft') showSlide(state.viewer.i - 1, -1);
  if (e.key === 'ArrowRight') showSlide(state.viewer.i + 1, 1);
  if (e.key === ' ') { e.preventDefault(); setChromeVisible(viewer.classList.contains('imm')); }
});

/* =====================================================
   年份 / 月份导航栏（< 2025 > + 月份条）
   - 年份倒序，‹ 往更早、› 往更新
   - 滚动时间轴时自动同步当前年月
   ===================================================== */
const rail = $('#rail');

function syncRailHeight() {
  document.documentElement.style.setProperty(
    '--rail-h', (rail.hidden ? 0 : rail.offsetHeight) + 'px');
}
function railYears() {
  const ys = [];
  ((state.tree && state.tree.months) || []).forEach(m => {
    const y = m.ym.slice(0, 4);
    if (ys[ys.length - 1] !== y) ys.push(y);
  });
  return ys;                                   // 倒序：最新在前
}
function resetRail() {
  const first = ((state.tree && state.tree.months) || [])[0];
  state.railYm = first ? first.ym : null;
  state.railYear = first ? first.ym.slice(0, 4) : null;
  buildRail();
}
function buildRail() {
  const ys = railYears();
  state.years = ys;
  if (!ys.length) { rail.hidden = true; syncRailHeight(); return; }
  if (!state.railYear || ys.indexOf(state.railYear) < 0) state.railYear = ys[0];
  rail.hidden = false;
  paintRail();
  syncRailHeight();
}
function paintRail() {
  const ys = state.years;
  const i = ys.indexOf(state.railYear);
  $('#yrLabel').textContent = state.railYear + ' 年';
  $('#yrPrev').disabled = i >= ys.length - 1;     // 更早（倒序 → 索引更大）
  $('#yrNext').disabled = i <= 0;                 // 更新（索引更小）
  const box = $('#railMonths');
  box.innerHTML = '';
  ((state.tree && state.tree.months) || [])
    .filter(m => m.ym.startsWith(state.railYear))
    .forEach(m => {
      const b = document.createElement('button');
      b.className = 'rm' + (m.ym === state.railYm ? ' on' : '');
      b.dataset.ym = m.ym;
      b.textContent = (+m.ym.slice(5)) + '月';
      b.title = m.n.toLocaleString() + ' 张';
      b.onclick = () => jumpToMonth(m.ym);
      box.appendChild(b);
    });
  const on = box.querySelector('.rm.on');       // 当前月份滚到条带中间
  if (on) box.scrollLeft = on.offsetLeft - box.clientWidth / 2 + on.clientWidth / 2;
}
function focusRail(ym) {
  if (!ym) return;
  const y = ym.slice(0, 4);
  const changed = state.railYm !== ym;
  state.railYm = ym;
  if (state.railYear !== y) { state.railYear = y; paintRail(); }
  else if (changed) {
    $$('.rm', $('#railMonths')).forEach(
      b => b.classList.toggle('on', b.dataset.ym === ym));
  }
}
let railRaf = 0;
function onRailScroll() {
  if (railRaf) return;
  railRaf = requestAnimationFrame(() => { railRaf = 0; syncRail(); });
}
function syncRail() {
  if (rail.hidden || state.search.active || pinActive) return;
  const seps = $$('#timeline .month-sep[data-ym], #timeline .month-card[data-ym]');
  if (!seps.length) return;
  const off = topbarOffset() + 8;
  let cur = seps[0].dataset.ym;
  let rowTop = -Infinity;
  for (const s of seps) {
    const top = s.getBoundingClientRect().top;
    if (top > off) break;
    if (Math.abs(top - rowTop) > 1) cur = s.dataset.ym;
    rowTop = top;
  }
  focusRail(cur);
}
/* dir = +1 往更早，-1 往更新 */
function stepYear(dir) {
  const ys = state.years || [];
  const j = ys.indexOf(state.railYear) + dir;
  if (j < 0 || j >= ys.length) return;
  const ms = ((state.tree && state.tree.months) || [])
    .filter(m => m.ym.startsWith(ys[j]));
  if (!ms.length) return;
  state.railYear = ys[j];
  jumpToMonth(ms[0].ym);                        // 落到该年年头
  paintRail();                                  // 用新年份重建月份条
}
$('#yrPrev').onclick = () => stepYear(1);
$('#yrNext').onclick = () => stepYear(-1);
$('#yrLabel').onclick = () => $('#btnMenu').click();

/* =====================================================
   月份跳转 / 密度 / 信息
   ===================================================== */
function buildMonthSheet() {
  const box = $('#monthList');
  box.innerHTML = '';
  (state.tree.months || []).forEach(m => {
    const b = document.createElement('button');
    b.className = 'm-item'; b.dataset.ym = m.ym;
    b.innerHTML = `<div class="mym">${fmtMonthLabel(m.ym)}</div>` +
      `<div class="mn">${m.n.toLocaleString()} 张</div>`;
    b.onclick = () => { $('#sheet').hidden = true; jumpToMonth(m.ym); };
    box.appendChild(b);
  });
}
function jumpToMonth(ym) {
  if (state.search.active) exitSearch();
  let idx = state.days.findIndex(d => d.ym === ym);
  if (idx < 0) return;
  thumbAdvance();
  // Navigation supersedes pending work from the old viewport.
  [...thumbQueue, ...thumbLive].forEach(img => {
    thumbDefer(img);
    // Re-observing an already observed visible image does not emit a new entry.
    thumbObserver.unobserve(img); thumbObserver.observe(img);
  });
  requestAnimationFrame(thumbReprioritize);
  // 确保已渲染到该月
  while (state.renderedDayIdx <= idx && !state.done) renderNextBatch();
  focusRail(ym);
  pinMonth(ym);
}

/* 把目标月稳定停在导航栏正下方。
   骨架→真图是异步的，页面高度会变，锚点会被推走，
   所以连续几帧重新对齐；对齐期间冻结滚动同步，避免导航栏被刷回旧值。 */
let pinToken = 0, pinActive = false;
function pinMonth(ym) {
  const my = ++pinToken;
  pinActive = true;
  let last = -1, stable = 0, n = 0, want = -1;
  const align = () => {
    const el = $(`.month-sep[data-ym="${ym}"], .month-card[data-ym="${ym}"]`);
    if (!el) return false;
    // 外部滚动保护：页面若已被移开（程序 scrollTo / 浏览器自身滚动），
    // 立即放弃对齐，否则会把页面硬拽回目标月、盖掉真实滚动位置。
    if (want >= 0 && Math.abs(window.scrollY - want) > 24) return false;
    const top = el.getBoundingClientRect().top + window.scrollY - topbarOffset() + 6;
    if (Math.abs(top - last) < 2) stable++; else stable = 0;
    last = top;
    window.scrollTo({ top, behavior: 'auto' });
    want = window.scrollY;
    return true;
  };
  const bail = () => {                    // 放弃对齐后，按真实滚动位置重算导航栏
    pinActive = false;
    requestAnimationFrame(syncRail);
  };
  (function frame() {
    if (my !== pinToken) return;
    if (!align()) return bail();
    if (stable < 4 && ++n < 60) { requestAnimationFrame(frame); return; }
    setTimeout(() => {                    // 等网格换图/图片布局稳定后再校一次
      if (my !== pinToken) return;
      if (!align()) return bail();
      setTimeout(() => {
        if (my !== pinToken) return;
        if (!align()) return bail();
        focusRail(ym);                    // 收尾：把导航栏钉到目标年月
        pinActive = false;
      }, 320);
    }, 320);
  })();
}
/* 用户一旦自己滚动/操作，立刻放弃对齐 */
['wheel', 'touchstart', 'mousedown', 'keydown'].forEach(ev =>
  window.addEventListener(ev, () => { pinToken++; pinActive = false; }, { passive: true }));

function topbarOffset() {
  return $('#topbar').offsetHeight + (rail.hidden ? 0 : rail.offsetHeight);
}

$('#btnMenu').onclick = () => {
  $('#sheet').hidden = false;
  const cur = state.days[state.renderedDayIdx - 1]?.ym;
  $$('.m-item').forEach(x => x.classList.toggle('on', x.dataset.ym === cur));
  const on = $('.m-item.on'); if (on) on.scrollIntoView({ block: 'center' });
};
$('#sheet').addEventListener('click', e => { if (e.target.dataset.close !== undefined) $('#sheet').hidden = true; });

/* 手动重新扫描索引（增量；根目录离线则保留原索引） */
$('#btnRefresh').onclick = async e => {
  const btn = e.currentTarget;
  btn.disabled = true;
  $('#refreshHint').textContent = '扫描中…';
  try {
    const r = await apiGetAdmin('/api/refresh', { lib: 'all' });
    const d = await api('/api/libs');
    state.libs = d.libraries || [];
    buildChips(state.libs.filter(l => l.count > 0));
    $('#sheet').hidden = true;
    const cur = state.lib;
    state.lib = null;                      // 强制重建时间轴
    await selectLib(cur, false);
    const n = r.libraries.reduce((a, x) =>
      a + (x.added || 0) + (x.changed || 0) + (x.removed || 0), 0);
    const mv = r.libraries.reduce((a, x) => a + (x.movParsed || 0), 0);
    toast(n ? `扫描完成：新增/变更/删除 ${n} 项` : '扫描完成：没有变化', 2600);
    if (mv) toast(`重新解析 MOV ${mv} 个`, 2600);
  } catch (err) {
    toast('扫描失败：' + err.message, 3000);
  } finally {
    btn.disabled = false;
    $('#refreshHint').textContent = '';
  }
};

/* 孤儿缓存清理：第一下只「预览」，第二下（10 秒内）才真正执行
   —— 清理范围严格限定在 cache/ 目录，绝不触碰源照片 */
let gcArmed = 0;
$('#btnGc').onclick = async e => {
  const btn = e.currentTarget;
  btn.disabled = true;
  $('#gcHint').textContent = '计算中…';
  try {
    if (!gcArmed || Date.now() - gcArmed > 10000) {
      const p = await apiGetAdmin('/api/cache/gc');
      const mb = (p.orphan.bytes / 1048576).toFixed(1);
      if (!p.orphan.files) {
        gcArmed = 0;
        $('#gcHint').textContent = '无孤儿缓存';
        toast(`没有可回收的孤儿缓存（缓存共 ${p.cache.files.toLocaleString()} 个 / ` +
              `${(p.cache.bytes / 1048576).toFixed(1)} MB）`, 3600);
        return;
      }
      gcArmed = Date.now();
      const cls = p.byClass.map(c => `${c.cls} ${c.files} 个`).join('、');
      $('#gcHint').textContent = `可回收 ${p.orphan.files} 个 / ${mb} MB`;
      toast(`孤儿缓存 ${p.orphan.files} 个 / ${mb} MB：${cls}。` +
            `10 秒内再点一次执行清理（不影响源照片）。`, 6500);
      return;
    }
    const r = await apiGetAdmin('/api/cache/gc', { apply: 1 });
    gcArmed = 0;
    $('#gcHint').textContent = '';
    toast(`已清理 ${r.deleted} 个缓存文件，释放 ${(r.bytes / 1048576).toFixed(1)} MB` +
          (r.ghostRows ? `（另清理清单幽灵行 ${r.ghostRows} 条）` : '') +
          '。源照片未受影响。', 5200);
  } catch (err) {
    toast('缓存清理失败：' + err.message, 3600);
  } finally {
    btn.disabled = false;
  }
};

/* =====================================================
   密度控制（五档）—— 常驻 pill ＋ 五档选择面板
   ===================================================== */
/* 当前视口最顶上那个「已渲染的日期分组」。
   列数一变整页高度会大幅变化，切档后把它拉回原来的屏幕位置，
   用户才不会突然被弹回顶部。 */
function topVisibleBlock() {
  const off = topbarOffset() + 4;
  let best = null, bestTop = -Infinity;
  $$('#timeline .day-blk').forEach(el => {
    const t = el.getBoundingClientRect().top;
    if (t <= off && t > bestTop) { bestTop = t; best = el; }
  });
  return best || $('#timeline .day-blk');
}
/* FLIP 采样：在屏上撒点取元素，比遍历上千个 tile 快得多 */
function visibleTiles(n) {
  const out = [], seen = new Set();
  const H = window.innerHeight, W = window.innerWidth;
  for (let r = 1; r <= 8 && out.length < n; r++) {
    const y = H * r / 9;
    for (let c = 1; c <= 6 && out.length < n; c++) {
      const hits = document.elementsFromPoint(W * c / 7, y);
      for (const el of hits) {
        if (el.classList && el.classList.contains('tile')) {
          if (!seen.has(el)) { seen.add(el); out.push(el); }
          break;
        }
      }
    }
  }
  return out;
}
/* 让采样到的格子从旧几何「飞」到新几何 —— 切档是一个形变，不是硬跳。
   ★ .tile 的进场动画是 animation-fill:both，动画值优先级高于内联样式，
     不摘掉它，下面写的 transform 会被吃掉。 */
function flipTiles(tiles, before) {
  const after = tiles.map(t => t.getBoundingClientRect());
  let moved = 0;
  tiles.forEach((t, k) => {
    const b = before[k], a = after[k];
    if (!b || !a || !b.width || !a.width) return;
    const dx = b.left - a.left, dy = b.top - a.top, sc = b.width / a.width;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(sc - 1) < .03) return;
    t.style.animation = 'none';
    t.style.transition = 'none';
    t.style.transformOrigin = 'top left';
    t.style.zIndex = '6';
    t.style.transform = `translate(${dx}px,${dy}px) scale(${sc})`;
    moved++;
  });
  if (!moved) return;
  requestAnimationFrame(() => {
    tiles.forEach(t => {
      if (!t.style.transform) return;
      t.style.transition = 'transform .34s var(--ease)';
      t.style.transform = '';
    });
    setTimeout(() => tiles.forEach(t => {
      t.style.transition = ''; t.style.transform = '';
      t.style.transformOrigin = ''; t.style.zIndex = '';
    }), 460);
  });
}
/* 切档：更新列数与时间层级，按照片锚点恢复附近行；缩略图规格保持不变。 */
function setDensity(i) {
  i = Math.max(0, Math.min(DENSITY_LEVELS.length - 1, i | 0));
  if (state.switching) {
    state.density = i; computeCols(); saveDensity(i); syncDenUI(); return;
  }
  if (i === 4 && document.body.classList.contains('selecting')) {
    toast('先完成选择，再打开年份概览', 2200); return;
  }
  if (state.density < 3) state.lastDayDensity = state.density;
  if (i < 3) state.lastDayDensity = i;
  if (i === state.density) { syncDenUI(); return; }

  const anchor = topVisibleBlock();
  const photoAnchor = captureViewAnchor();
  const previousMode = wallMode();
  const anchorTop = anchor ? anchor.getBoundingClientRect().top : 0;
  const anim = !prefersReduce() && !state.search.active;
  const probes = anim ? visibleTiles(16) : [];
  const before = probes.map(t => t.getBoundingClientRect());

  state.density = i;
  computeCols();
  if (!state.search.active && previousMode !== wallMode()) {
    rebuildWall(convertWallAnchor(photoAnchor, wallMode(), state.tree));
    saveDensity(i); syncDenUI(); revealDensity();
    toast(DENSITY_LEVELS[i].name + '视图', 1000); return;
  }
  sizeSkeletons($$('#timeline .day-blk'));        // 列数变了，骨架高度必须重算
  rearmObserver();
  updateWindows();                               // 回收旧行并按新列数恢复

  if (anchor) {                                   // 位置保持
    window.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
  }
  if (photoAnchor) restoreViewAnchor(photoAnchor);
  scheduleWindows();
  if (probes.length) flipTiles(probes, before);

  saveDensity(i);
  syncDenUI();
  revealDensity();
  toast(DENSITY_LEVELS[i].name + '视图', 1000);
}
function syncDenUI() {
  const lv = DENSITY_LEVELS[state.density];
  if (!lv) return;
  const name = $('#denName');
  if (name) name.textContent = lv.name;
  const dots = $('#denDots');
  if (dots) {
    if (dots.children.length !== DENSITY_LEVELS.length) {
      dots.innerHTML = DENSITY_LEVELS.map(() => '<i></i>').join('');
    }
    // ★ 圆点从左到右必须与两侧按钮同向：左「−」= 更密集（图更小），右「+」= 更宽松（图更大）。
    //   而 DENSITY_LEVELS 是按「大图 → 年份」排列的，所以第 k 个点对应第 N-1-k 档。
    //   否则点「+」圆点反而往左跑，看着别扭。
    const N = DENSITY_LEVELS.length;
    Array.prototype.forEach.call(dots.children,
      (d, k) => d.classList.toggle('on', k === N - 1 - state.density));
  }
  const out = $('#denOut'), inn = $('#denIn');
  if (out) out.disabled = state.density >= DENSITY_LEVELS.length - 1;  // 已是最密
  if (inn) inn.disabled = state.density <= 0;                          // 已是最宽
  const mid = $('#denMid');
  if (mid) mid.title = '浏览方式：' + lv.name;
  $$('.wall-levels button').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.wallMode === wallMode()));
    button.disabled = state.search.active || (button.dataset.wallMode === 'year' && document.body.classList.contains('selecting'));
  });
}
function buildDenSheet() {
  const box = $('#denList');
  if (!box) return;
  box.innerHTML = '';
  DENSITY_LEVELS.forEach((lv, k) => {
    const cols = colsForLevel(lv);
    const hint = lv.group === 'day' ? `${cols} 张 / 行`
      : lv.group === 'month' ? `${cols} 张 / 行 · 按月浏览`
        : '月份封面概览 · 点击展开';
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'm-item' + (k === state.density ? ' on' : '');
    b.disabled = k === 4 && document.body.classList.contains('selecting');
    b.innerHTML = `<div class="mym">${lv.name}</div><div class="mn">${hint}</div>`;
    b.onclick = () => { $('#denSheet').hidden = true; setDensity(k); };
    box.appendChild(b);
  });
}
let denOpener = null;
function openDenSheet(event) {
  denOpener = event?.currentTarget || document.activeElement;
  revealDensity();
  buildDenSheet();
  $('#denSheet').hidden = false;
  syncDen();
}
/* 灯箱 / 弹层 / 选择模式打开时把 pill 收起来（让位，也避免误触） */
const DEN_OVERLAYS = ['#viewer', '#sheet', '#trashSheet', '#idxSheet',
  '#shareSheet', '#denSheet', '#preview', '#confirm', '#vDetail'];
function syncDen() {
  const el = $('#denCtl');
  if (!el) return;
  const blocked = DEN_OVERLAYS.some(s => {
    const o = $(s); return o && !o.hidden;
  }) || document.body.classList.contains('selecting');
  el.classList.toggle('den-off', blocked);
}
$('#btnDensity').onclick = openDenSheet;          // 工具栏图标 → 同一个五档面板
$('#denOut').onclick = () => setDensity(state.density + 1);   // − 更密集
$('#denIn').onclick = () => setDensity(state.density - 1);    // ＋ 更宽松
$('#denMid').onclick = openDenSheet;
$('#denClose').onclick = () => { $('#denSheet').hidden = true; };
$('#denSheet').addEventListener('click', e => {
  if (e.target.dataset.close !== undefined) $('#denSheet').hidden = true;
});
{
  const mo = new MutationObserver(syncDen);
  DEN_OVERLAYS.forEach(s => {
    const o = $(s);
    if (o) mo.observe(o, { attributes: true, attributeFilter: ['hidden'] });
  });
  new MutationObserver(syncDen)
    .observe(document.body, { attributes: true, attributeFilter: ['class'] });
}
syncDenUI();

// The view chooser is a modal: keyboard focus stays inside and returns to its opener.
{
  const panel = $('#denSheet');
  panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-labelledby', 'denSheetTitle');
  let opener = null, wasOpen = false;
  new MutationObserver(() => {
    const open = !panel.hidden;
    if (open === wasOpen) return;
    wasOpen = open;
    if (open) { opener = denOpener || document.activeElement; $('#denClose').focus(); }
    else if (opener?.isConnected) {
      const menu = opener.closest('details');
      const target = menu && !menu.open ? menu.querySelector('summary') : opener;
      requestAnimationFrame(() => { if (panel.hidden && target.isConnected) target.focus(); });
      denOpener = null;
    }
  }).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
  panel.addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); panel.hidden = true; return; }
    if (e.key !== 'Tab') return;
    const buttons = [...panel.querySelectorAll('button:not(:disabled)')];
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

$('#btnInfo').onclick = async () => {
  // 分享访客：统计 / 索引 / 缓存都是 admin-only（服务端会 403），只展示分享自身信息
  if (IS_SHARE()) {
    const s = sess.share || {};
    const lines = [
      ['正在浏览', `分享相册「${s.title || ''}」`],
      ['照片数量', (s.count || 0).toLocaleString() + ' 张'],
      ['所属图库', s.libName || ''],
      ['有效期', s.expiresAt ? fmtExpire(s.expiresAt) : '长期有效'],
      ['权限', '只读：不能删除、恢复或修改任何照片'],
      ['说明', '这是别人分享给你的相册，只能看到被授权的那部分照片。'],
    ];
    $('#vdList').innerHTML = lines.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('');
    $('#vdList').closest('.vd-card').querySelector('.vd-head').textContent = '分享信息';
    $('#vdAdminBtn').hidden = true;
    $('#vDetail').hidden = false;
    return;
  }
  try {
    const [l, s, ix, ca] = await Promise.all([
      api('/api/libs'), api('/api/stats'), api('/api/index'), api('/api/cache')]);
    const MB = b => (b / 1048576).toFixed(1);
    const lines = [
      ['图库', l.libraries.map(x => `${x.name} ${x.count.toLocaleString()}` +
        (x.stale ? '(离线)' : '')).join('、')],
      ['索引库', `${(ix.db.rows || 0).toLocaleString()} 行 / ${(ix.db.pairs || 0).toLocaleString()} 组配对 / ${ix.db.dbMB} MB`],
      ['最近核对', ix.libraries.map(x =>
        `${x.name} +${x.added}/~${x.changed}/-${x.removed} ${x.lastScanMs}ms`).join('；')],
      ['重解析 MOV', ix.libraries.reduce((a, x) => a + (x.movParsed || 0), 0) + ' 个'],
      ['后台核对', (ix.scan && ix.scan.recheckSeconds) ? `每 ${ix.scan.recheckSeconds} 秒` : '关闭'],
      ['缓存总量', `${ca.total.files.toLocaleString()} 个 / ${MB(ca.total.bytes)} MB ` +
        `（图片 ${MB(ca.image.bytes)} + 转码 ${MB(ca.trans.bytes)} MB）`],
      ['缓存容量管理', ca.policy && ca.policy.maxGB > 0
        ? `上限 ${ca.policy.maxGB} GiB，超限后清至 ${Math.round(ca.policy.lowWatermark * 100)}%`
        : '未设置上限，不自动淘汰有效缓存'],
      ['磁盘空间保护', ca.policy ? `至少保留 ${ca.policy.minFreeGB} GiB` : '—'],
      ['最近自动回收', ca.governance && ca.governance.at
        ? `${ca.governance.deleted || 0} 个 / ${MB(ca.governance.bytes || 0)} MB` +
          (ca.governance.blocked ? '（清理受限：部分缓存正在使用或受保护）' : '')
        : '尚未执行'],
      ['缓存清单', `${(ca.manifest.entries || 0).toLocaleString()} 条记录 / db ${ca.manifest.dbMB || 0} MB`],
      ['孤儿缓存', ca.orphan
        ? `${ca.orphan.files.toLocaleString()} 个 / ${MB(ca.orphan.bytes)} MB 可回收`
        : '点下方「孤儿缓存清理」预览'],
      ['缓存命中 / 生成', `${s.hit} / ${s.gen}`],
      ['并发单飞', `${s.singleflight_wait} 次合并`],
      ['转码次数', `${s.transcode}`],
      ['HEIC 支持', s.heif ? '是' : '否'],
      ['ffmpeg', s.ffmpeg ? '可用' : '不可用'],
    ];
    const expText = admin.expiresAt
      ? new Date(admin.expiresAt * 1000).toLocaleTimeString('zh-CN',
        { hour: '2-digit', minute: '2-digit' }) : '';
    lines.push(['源文件管理',
      !admin.required ? '未配置 → 删除功能关闭（浏览不受影响）'
        : (admin.authed ? `已授权${expText ? '（至 ' + expText + '）' : ''}`
          : '需要授权（首次管理操作时提示）')]);
    $('#vdList').innerHTML = lines.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('');
    $('#vdList').closest('.vd-card').querySelector('.vd-head').textContent = '服务与索引';

    const ab = $('#vdAdminBtn');
    if (!admin.required) {
      ab.hidden = true;
    } else if (admin.authed) {
      ab.hidden = false;
      ab.textContent = '退出管理授权';
      ab.onclick = async () => { await adminLogout(); $('#vDetail').hidden = true; };
    } else {
      ab.hidden = false;
      ab.textContent = '输入管理口令授权';
      ab.onclick = async () => {
        const pw = await askAdminAuth('输入管理口令后即可执行删除 / 恢复 / 永久删除。');
        if (pw === null) return;
        try { await adminLogin(pw); toast('管理授权成功', 2400); }
        catch (e) { toast('授权失败：' + e.message, 4200); }
        $('#vDetail').hidden = true;
      };
    }
    $('#vDetail').hidden = false;
  } catch (e) { toast('读取失败：' + e.message); }
};

/* Cmd/Ctrl+K 打开搜索 */
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault(); $('#btnSearch').click();
  }
});

/* =====================================================
   索引进度面板（轻量）
   ★ 服务端 total = null 表示「文件总数还没统计出来」——此阶段**不显示百分比**，
     只显示「正在扫描图库，统计文件数量……」，等拿到真实总数才展示进度条。
   ★ 前端**定时轮询** /api/index/progress，绝不挂着一个请求等索引结束。
   ===================================================== */
let idxTimer = null, idxBusy = false, idxLast = null, idxWasRunning = false;

function idxIsOpen() { return !$('#idxSheet').hidden; }
function idxOpen() { $('#idxSheet').hidden = false; idxPollStart(); }
function idxClose() {
  $('#idxSheet').hidden = true;
  if (idxLast && !idxLast.running) idxPollStop();
}
function idxPollStart() { idxTick(); if (!idxTimer) idxTimer = setInterval(idxTick, 1000); }
function idxPollStop() { if (idxTimer) { clearInterval(idxTimer); idxTimer = null; } }

async function idxTick() {
  if (idxBusy) return;
  idxBusy = true;
  try {
    const d = await api('/api/index/progress');
    idxLast = d;
    renderIdx(d);
    $('#idxDot').hidden = !d.running;
    if (idxWasRunning && !d.running) await afterIndexDone();
    idxWasRunning = !!d.running;
    if (!d.running && !idxIsOpen()) idxPollStop();
  } catch (e) { /* 轮询失败静默，不打扰浏览 */ }
  finally { idxBusy = false; }
}

const nfmt = n => (n || 0).toLocaleString();

function renderIdx(d) {
  const total = d.total;
  const counting = d.phase === 'counting';
  const active = d.running && (d.phase === 'counting' || d.phase === 'scanning'
    || d.phase === 'finalizing');

  $('#idxStage').textContent = d.stage || (d.running ? '索引进行中…' : '索引完成');
  $('#idxHint').textContent = d.running ? '进行中' : '已完成';

  // —— 进度条：只有拿到**真实总数**后才可能出现百分比 ——
  const wrap = $('#idxBarWrap');
  if (active) {
    wrap.hidden = false;
    if (total === null || total === undefined) {
      $('#idxBar').classList.add('indet');           // 不确定态，不伪造百分比
      $('#idxNums').textContent = counting
        ? `正在扫描图库，统计文件数量……（已发现 ${nfmt(d.found)} 个文件）`
        : '准备中…';
    } else {
      $('#idxBar').classList.remove('indet');
      const pct = total > 0
        ? Math.min(100, Math.round(100 * (d.processed || 0) / total)) : 0;
      $('#idxFill').style.width = pct + '%';
      $('#idxNums').textContent =
        `${nfmt(d.processed)} / ${nfmt(total)} 个文件（${pct}%）`;
    }
  } else wrap.hidden = true;

  // —— 增量：本次检查数量 + 新增 / 变更 / 删除 ——
  // 统计阶段（counting）还没开始比对，此时写「本次检查 0 个文件」等于拿没算出来的数当结论，
  // 所以这一行要等真正进入 scanning 之后再出现。
  const inc = $('#idxInc');
  const incReady = !counting &&
    (d.running || d.mode === 'recheck' || d.mode === 'incremental');
  if (incReady) {
    inc.hidden = false;
    inc.innerHTML = `本次检查 <b>${nfmt(d.libScanned)}</b> 个文件` +
      (d.libName ? `（${esc(d.libName)}）` : '') +
      ` ｜ 新增 <b>${nfmt(d.libAdded)}</b> ｜ 变更 <b>${nfmt(d.libChanged)}</b>` +
      ` ｜ 删除 <b>${nfmt(d.libRemoved)}</b>`;
  } else inc.hidden = true;

  // —— 异常：错误数 / 离线图库 ——
  const msgs = [];
  if (d.errors) {
    msgs.push(`⚠ 本次索引出现 ${d.errors} 个错误` +
      (d.lastError ? `：${d.lastError}` : ''));
  }
  const off = d.offline || [];
  if (off.length) {
    msgs.push(`⚠ 图库离线（已保留旧索引）：${off.map(o => o.name).join('、')}`);
  }
  const err = $('#idxErr');
  err.hidden = !msgs.length;
  if (msgs.length) err.innerHTML = msgs.map(esc).join('<br>');

  // —— 汇总 ——
  // ★ 索引进行中不许显示汇总：META 正在「清空 → 逐库重建」，此刻的汇总要么全 0、
  //   要么只统计了刚扫完的一两个库 —— 那都不是结论。一律「—」，
  //   等 running=false（本次收尾）后再填真实数字。
  const s = d.summary || {};
  const hasSum = !d.running && typeof s.total === 'number';
  const sv = v => (hasSum ? nfmt(v) : '—');
  const MODE = { startup: '首次启动索引', full: '全量重建',
    incremental: '手动增量扫描', recheck: '后台定期核对' };
  const rows = [];
  if (d.running && d.libName) {
    rows.push(['正在扫描', d.libName +
      (d.libTotal ? `（第 ${d.libIndex}/${d.libTotal} 个图库）` : '')]);
  }
  rows.push(['总照片数', sv(s.photos)]);
  rows.push(['视频数', sv(s.videos)]);
  rows.push(['Live Photo', hasSum ? `${nfmt(s.live)} 组` : '—']);
  rows.push(['条目合计', sv(s.total)]);
  rows.push(['本次模式', MODE[d.mode] || '—']);
  rows.push(['后台核对', d.recheckSeconds ? `每 ${d.recheckSeconds} 秒` : '已关闭']);
  (d.libraries || []).forEach(l => rows.push([
    l.name, `${nfmt(l.count)} 项${l.live ? ` / Live ${l.live}` : ''}` +
      `${l.stale ? ' ⚠ 离线' : ''} ｜ +${nfmt(l.added)} ~${nfmt(l.changed)} -${nfmt(l.removed)}`]));
  $('#idxKv').innerHTML = rows.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
}

/** 索引结束后：刷新图库与时间轴（首次索引完成即正常进入界面）。 */
async function afterIndexDone() {
  try {
    const d = await api('/api/libs');
    state.libs = d.libraries || [];
    if (d.trash && d.trash.retentionDays) trashDays = d.trash.retentionDays;
    const usable = state.libs.filter(l => l.count > 0);
    if (!usable.length) return;
    buildChips(usable);
    if (!state.lib) {
      await selectLib(usable[0].id, true);
      idxClose();
    } else if (!sel.active && !state.search.active) {
      await refreshAfterChange();
    }
  } catch (e) { /* 忽略 */ }
}

$('#btnIdx').onclick = () => idxOpen();
$('#idxSheet').addEventListener('click', e => {
  if (e.target.dataset.close !== undefined) idxClose();
});
$('#btnIdxClose').onclick = idxClose;
$('#btnIdxRescan').onclick = async () => {
  try {
    const r = await apiPost('/api/index/scan', { full: 0 });
    if (r.busy) { toast('索引正在进行中，请稍候', 2600); return; }
    idxWasRunning = true;
    idxPollStart();
    toast('已开始重新扫描（增量）', 2400);
  } catch (e) { toast('无法开始扫描：' + e.message, 3600); }
};

/* =====================================================
   源文件管理：选择模式 · 移入回收站 · 最近删除 · 永久删除
   ★ 破坏性操作一律 POST；GET 只做读取/预览/统计。
   ★ 服务端独立校验权限，前端置灰只是体验优化。
   ===================================================== */
let trashDays = 30;

/* =====================================================
   管理授权（独立于浏览）
   ★ 浏览永远免登录；只有破坏性操作才会要求授权。
   ★ 口令只存在于内存变量 → 一次性 POST 给服务端 → 服务端签发 HttpOnly 会话 Cookie。
   ★ 授权过期/被撤销时，服务端返回 401 needAuth，这里弹框并要求重新授权，
     随后**自动重试原操作一次**（用户无感）。
   ===================================================== */

/** 弹出「管理授权」输入框。返回输入的口令，取消则返回 null。 */
function askAdminAuth(reason) {
  $('#cfmTitle').textContent = '需要管理授权';
  $('#cfmBody').innerHTML =
    `<div class="cfm-warn">${esc(reason || '删除 / 恢复 / 永久删除等操作需要管理授权。')}</div>
     <label class="aa-lab">管理口令<input id="aaPw" type="password"
       autocomplete="current-password" placeholder="请输入管理口令"></label>
     <div class="aa-hint">授权在当前浏览器会话内有效，关闭浏览器后需重新验证。</div>`;
  const yes = $('#cfmYes');
  yes.textContent = '授权';
  yes.classList.toggle('danger', false);
  $('#confirm').hidden = false;
  const inp = $('#aaPw');
  if (inp) {
    inp.focus();
    inp.addEventListener('keydown', ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); closeConfirm(true); }
    });
  }
  const prev = cfmResolve;
  return new Promise(res => {
    cfmResolve = ok => {
      const val = inp ? inp.value : '';
      cfmResolve = prev;
      res(ok ? (val || null) : null);
    };
  });
}

/** 用口令换会话。成功返回 true。 */
async function adminLogin(pw) {
  const r = await fetch('/api/admin/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-SL-Admin': '1' },
    body: JSON.stringify({ token: pw })
  });
  if (r.ok) {
    const d = await r.json().catch(() => ({}));
    admin.required = true; admin.authed = true;
    admin.expiresAt = d.expiresAt || 0;
    return true;
  }
  const d = await r.json().catch(() => ({}));
  throw new Error(d.error || ('HTTP ' + r.status));
}

async function adminLogout() {
  try {
    await fetch('/api/admin/logout', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-SL-Admin': '1' }
    });
  } catch (e) { /* 忽略 */ }
  admin.authed = false; admin.expiresAt = 0;
  toast('已退出管理授权', 2200);
}

/**
 * 管理类 POST。
 * 服务端独立校验权限（401 needAuth / 403 / 409），这里只负责体验：
 * 遇 401 → 弹授权框 → 授权成功后**自动重试一次**。
 */
async function apiPost(path, body, _retried) {
  const r = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-SL-Admin': '1' },
    body: JSON.stringify(body || {})
  });
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (r.status === 401 && data && data.needAuth && !_retried) {
    if (!admin.required) {
      const err = new Error('未配置管理口令：源文件管理功能已禁用（浏览不受影响）');
      err.status = 401; err.noAuthConfig = true; throw err;
    }
    const pw = await askAdminAuth('该操作需要管理授权。');
    if (pw === null) { const e2 = new Error('已取消授权'); e2.canceled = true; throw e2; }
    try {
      await adminLogin(pw);
    } catch (e) {
      throw new Error('授权失败：' + e.message);
    }
    return apiPost(path, body, true);           // 自动重试一次
  }
  if (!r.ok) {
    const msg = (data && (data.error || data.msg)) || ('HTTP ' + r.status);
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return data || {};
}

function libMeta(id) {
  return state.libs.find(l => l.id === (id || state.lib));
}

/**
 * 带管理授权的 GET（维护类接口：/api/refresh、/api/cache/gc）。
 * 服务端已把这两条从匿名白名单移出 —— 它们会写盘/改状态，必须带会话 + X-SL-Admin 头。
 * 401 → 弹授权框 → 授权成功后自动重试一次（与 apiPost 同一套体验）。
 */
async function apiGetAdmin(path, params = {}, _retried) {
  const q = new URLSearchParams(params).toString();
  const r = await fetch(path + (q ? '?' + q : ''), {
    credentials: 'same-origin',
    headers: { 'X-SL-Admin': '1' }
  });
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (r.status === 401 && data && data.needAuth && !_retried) {
    if (!admin.required) {
      const err = new Error('未配置管理口令：该功能已禁用（浏览不受影响）');
      err.status = 401; err.noAuthConfig = true; throw err;
    }
    const pw = await askAdminAuth('该操作需要管理授权。');
    if (pw === null) { const e2 = new Error('已取消授权'); e2.canceled = true; throw e2; }
    try { await adminLogin(pw); }
    catch (e) { throw new Error('授权失败：' + e.message); }
    return apiGetAdmin(path, params, true);
  }
  if (!r.ok) {
    const msg = (data && (data.error || data.msg)) || ('HTTP ' + r.status);
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return data || {};
}

/* ---------------- 通用二次确认 ---------------- */
let cfmResolve = null;
function askConfirm(o) {
  $('#cfmTitle').textContent = o.title || '请确认';
  $('#cfmBody').innerHTML = o.html || '';
  const yes = $('#cfmYes');
  yes.textContent = o.okText || '确定';
  yes.classList.toggle('danger', !!o.danger);
  $('#confirm').hidden = false;
  return new Promise(res => { cfmResolve = res; });
}
function closeConfirm(v) {
  $('#confirm').hidden = true;
  const r = cfmResolve; cfmResolve = null;
  if (r) r(v);
}
$('#cfmYes').onclick = () => closeConfirm(true);
$('#cfmNo').onclick = () => closeConfirm(false);
$('#confirm').addEventListener('click', e => {
  if (e.target.dataset.cfmX !== undefined) closeConfirm(false);
});

/* ---------------- 回收站媒体预览 ---------------- */
function openPreview(item) {
  const url = `/api/trash/media?item=${enc(item.itemId)}`;
  const isV = /\.(mov|mp4|m4v|mkv|webm|3gp|mts|m2ts)$/i.test(item.p || '');
  $('#pvTitle').textContent = item.n || item.p || '预览';
  $('#pvBody').innerHTML = isV
    ? `<video controls playsinline src="${url}"></video>`
    : `<img alt="" src="${url}">`;
  $('#preview').hidden = false;
}
function closePreview() {
  $('#preview').hidden = true;
  $('#pvBody').innerHTML = '';
}
$('#pvClose').onclick = closePreview;
$('#preview').addEventListener('click', e => {
  if (e.target.dataset.pvX !== undefined) closePreview();
});

/* ---------------- 时间轴选择模式 ---------------- */
const sel = { active: false, map: new Map(), revision: 0 };     // rel -> item

function tileItemOf(a) {
  return a.item || null;
}
function toggleTileSel(a) {
  const it = tileItemOf(a);
  if (!it) return;
  if (sel.map.has(it.p)) sel.map.delete(it.p); else sel.map.set(it.p, it);
  a.classList.toggle('sel', sel.map.has(it.p));
  syncSelBar();
}
function syncSelBar() {
  const n = sel.map.size;
  $('#selCount').textContent = `已选 ${n} 项`;
  $('#selDelete').disabled = n === 0;
  const sh = $('#selShare');
  if (sh) sh.disabled = n === 0;
  syncDayPicks();            // 每天标题后的选择框跟着勾选态走（含半选）
}
function toggleSelectMode(on) {
  if (on && wallMode() === 'year') setDensity(3);
  ++sel.revision;
  sel.active = !!on;
  document.body.classList.toggle('selecting', sel.active);
  $('#btnSelect').classList.toggle('on', sel.active);
  $('#selBar').hidden = !sel.active;
  if (!sel.active) {
    sel.map.clear();
    $$('.tile.sel').forEach(t => t.classList.remove('sel'));
  }
  syncSelBar();
  syncDenUI();
}
$('#btnSelect').onclick = () => {
  if (state.search.active) exitSearch();
  toggleSelectMode(!sel.active);
};
$('#selCancel').onclick = () => toggleSelectMode(false);

/* ---------------- 「每一天」整日选择框 ----------------
   只在选择模式（body.selecting）下出现：点一下 = 选中 / 取消这一天**全部**照片，
   部分选中时显示为半选。与「全选本月」共用同一个 sel.map，两者可以叠加使用。
   ★ 单日也可能超过 600 张，所以同样按 600/页 分页拉全，避免漏选。 */
function dayKeyOfItem(it) {
  if (!it || !it.t) return '';
  const dt = new Date(it.t * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}|${p(dt.getDate())}`;
}
function selCountByDay() {
  const m = new Map();
  for (const it of sel.map.values()) {
    const k = dayKeyOfItem(it);
    if (k) m.set(k, (m.get(k) || 0) + 1);
  }
  return m;
}
function syncDayPicks(blocks) {
  const list = blocks || $$('.day-blk');
  if (!list.length) return;
  const byDay = selCountByDay();
  for (const el of list) {
    const cb = el.querySelector('.d-pick-cb');
    if (!cb) continue;
    const n = byDay.get(el.dataset.key) || 0;
    const total = +el.dataset.total || 0;
    cb.checked = n > 0 && total > 0 && n >= total;
    cb.indeterminate = n > 0 && !cb.checked;
  }
}
async function pickDay(el, on) {
  if (!el || !sel.active) return;
  const cb = el.querySelector('.d-pick-cb');
  const ym = el.dataset.ym, d = el.dataset.d;
  const lib = state.lib, epoch = state.viewEpoch, revision = sel.revision;
  const current = () => sel.active && sel.revision === revision && state.lib === lib && state.viewEpoch === epoch;
  if (cb) cb.disabled = true;
  try {
    const items = [];
    let off = 0, total = 1;
    while (off < total) {
      const r = await api('/api/items', { lib, ym, d, off, lim: 600 }, browseController.signal);
      if (!current()) return;
      const arr = r.items || [];
      total = r.total || 0;
      for (const it of arr) items.push(it);
      off += arr.length;
      if (!arr.length) break;
    }
    if (!items.length) { toast('这一天没有可选的条目', 2600); return; }
    if (on) { for (const it of items) if (!sel.map.has(it.p)) sel.map.set(it.p, it); }
    else { for (const it of items) sel.map.delete(it.p); }
    if (!el.dataset.total) el.dataset.total = items.length;   // 供半选判定
    // 已渲染的格子同步高亮（未渲染的仍在选择集里，创建分享时会一并带上）
    $$('.tile').forEach(a => {
      const it = tileItemOf(a);
      a.classList.toggle('sel', !!(it && sel.map.has(it.p)));
    });
    const label = fmtDayHead(ym, d);
    toast(on ? `已选中 ${label}，共 ${sel.map.size} 项` : `已取消 ${label}`, 3000);
    syncSelBar();
  } catch (e) {
    if (current() && e.name !== 'AbortError') toast('选取当天失败：' + e.message, 4200);
  } finally {
    if (cb) cb.disabled = false;
    syncDayPicks([el]);
  }
}
document.addEventListener('change', e => {
  const cb = e.target && e.target.closest ? e.target.closest('.d-pick-cb') : null;
  if (!cb) return;
  pickDay(cb.closest('.day-blk'), cb.checked);
});

/* =====================================================
   免登录分享（管理端创建 / 撤销；访客只读）
   ★ 集合在**创建时固化成快照**：之后相册里新增 / 删除 / 重新索引照片，
     都不会改变已发出分享的范围（服务端 share_items 保存那一刻的 rel/mov）。
   ===================================================== */

/* 「全选本月」：把当前正在浏览的那个月一次性全部勾上。
   月份取自视口顶部那个日期分组（与「保持浏览位置」同一套取法），
   然后按 600/页 分页把该月拉全 —— 这样「整月分享」不用逐张点。 */
function currentViewYm() {
  const anchor = captureViewAnchor();
  if (anchor && anchor.key) return String(anchor.key).split('|')[0];
  const last = state.days[state.renderedDayIdx - 1];
  return (last && last.ym) || '';
}

$('#selMonth').onclick = async () => {
  const ym = currentViewYm();
  if (!ym) { toast('先在时间轴上滚到某个月，再点「全选本月」', 3200); return; }
  if (!sel.active) toggleSelectMode(true);
  const lib = state.lib, epoch = state.viewEpoch, revision = sel.revision;
  const current = () => sel.active && sel.revision === revision && state.lib === lib && state.viewEpoch === epoch;
  const btn = $('#selMonth');
  btn.disabled = true; btn.textContent = '选取中…';
  try {
    let off = 0, total = 1;
    while (off < total) {
      const d = await api('/api/items', { lib, ym, off, lim: 600 }, browseController.signal);
      if (!current()) return;
      const items = d.items || [];
      total = d.total || 0;
      for (const it of items) if (!sel.map.has(it.p)) sel.map.set(it.p, it);
      off += items.length;
      if (!items.length) break;
    }
    // 已渲染的格子同步高亮（未渲染的部分仍留在选择集里，创建分享时会一并带上）
    $$('.tile').forEach(a => {
      const it = tileItemOf(a);
      a.classList.toggle('sel', !!(it && sel.map.has(it.p)));
    });
    syncSelBar();
    toast(`已选中 ${fmtMonthLabel(ym)}，共 ${sel.map.size} 项`, 3200);
  } catch (e) {
    if (current() && e.name !== 'AbortError') toast('选取本月失败：' + e.message, 4200);
  } finally {
    btn.disabled = false; btn.textContent = '全选本月';
  }
};

/* 生成分享链接 */
$('#selShare').onclick = async () => {
  if (!sel.map.size) return;
  const rels = [...sel.map.values()].map(x => x.p);
  const defTitle = (libMeta() ? libMeta().name : '相册') + ' 分享';
  const ok = await askConfirm({
    title: '生成分享链接',
    okText: '生成链接',
    html: `<div class="cfm-warn">把当前选中的 <b>${rels.length}</b> 张照片生成一个
             <b>只读</b>分享链接。拿到链接的人无需登录即可查看这些照片
             （不能删除、恢复或修改）。</div>
           <label class="aa-lab">分享标题<input id="shTitle" type="text" maxlength="40"
             value="${esc(defTitle)}" placeholder="例如：九月精选"></label>
           <label class="aa-lab">有效期
             <select id="shDays">
               <option value="0">长期有效</option>
               <option value="7">7 天</option>
               <option value="30" selected>30 天</option>
               <option value="90">90 天</option>
             </select></label>
           <div class="aa-hint">分享内容在创建的这一刻就固化了：之后你在相册里新增或删除照片，
             都不会改变这条分享的范围。</div>
           ${sess.publicUrl ? `<div class="aa-hint">链接地址将使用
             <b>${esc(sess.publicUrl)}</b>（admin.json → publicUrl）</div>` : ''}`,
  });
  if (!ok) return;
  const title = (($('#shTitle') || {}).value || '').trim();
  const days = parseInt((($('#shDays') || {}).value || '0'), 10) || 0;
  try {
    const r = await apiPost('/api/share/create',
      { lib: state.lib, rels, title, expireDays: days, kind: 'items' });
    toggleSelectMode(false);
    // ★ 配了 publicUrl 就用服务端给的绝对地址，否则退回当前页面地址
    showShareResult(r.fullUrl || (location.origin + r.url),
                    r.share, r.skipped, !!r.fullUrl);
  } catch (e) {
    toast('生成分享失败：' + e.message, 4600);
  }
};

function showShareResult(url, meta, skipped, usedPublic) {
  const skip = (skipped && skipped.length)
    ? `<div class="aa-hint">有 ${skipped.length} 张不在索引里，已自动跳过。</div>` : '';
  askConfirm({
    title: '分享链接已生成',
    okText: '完成',
    html: `<div class="cfm-warn"><b>${esc((meta && meta.title) || '分享')}</b> ·
             ${(meta && meta.count) || 0} 张 ·
             ${meta && meta.expiresAt ? esc(fmtExpire(meta.expiresAt)) : '长期有效'}</div>
           <div class="sh-link" id="shUrl">${esc(url)}</div>
           <button class="cfm-btn" id="shCopy" style="margin-top:10px">复制链接</button>
           <div class="aa-hint">把这条链接发给对方即可（对方无需登录）。
             随时可在「分享管理」里撤销，撤销后立即失效。${skip}</div>
           ${usedPublic ? '<div class="aa-hint">已使用 admin.json 里的 publicUrl 替换本机地址。</div>' : ''}`,
  });
  const b = $('#shCopy');
  if (b) b.onclick = async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(url);
        toast('链接已复制', 2200);
      } else {
        const el = $('#shUrl');
        const r = document.createRange(); r.selectNodeContents(el);
        const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        toast('已选中链接，请手动复制', 3000);
      }
    } catch (e) { toast('复制失败，请长按链接手动复制', 3000); }
  };
}

/* ---------------- 分享管理（列出 / 撤销） ---------------- */
async function openShareSheet() {
  if (state.search.active) exitSearch();
  if (sel.active) toggleSelectMode(false);
  $('#shareSheet').hidden = false;
  await loadShares();
}

async function loadShares() {
  const box = $('#shareList');
  box.innerHTML = '<div class="t-empty">读取中…</div>';
  let d;
  try { d = await apiPost('/api/share/list', { lib: state.lib }); }
  catch (e) {
    box.innerHTML = `<div class="t-empty">读取失败：${esc(e.message)}</div>`;
    return;
  }
  const list = d.shares || [];
  $('#shareHint').textContent = `${list.length} 条有效分享`;
  const note = $('#shareNote');
  note.hidden = !!d.enabled;
  if (!d.enabled) {
    note.textContent = '分享功能已在服务端配置中关闭'
      + '（libraries.json → access.share.enabled = false）。';
  }
  if (!list.length) {
    box.innerHTML = '<div class="t-empty">还没有分享。<br>退出后勾选照片，点「生成分享」即可创建。</div>';
    return;
  }
  box.innerHTML = list.map(s => `
    <div class="sh-item" data-id="${esc(s.id)}">
      <div class="sh-top">
        <span class="sh-title">${esc(s.title || '未命名分享')}</span>
        <span class="sh-badge">${s.count} 张</span>
      </div>
      <div class="sh-meta">${esc(s.libName || '')} ·
        ${s.expiresAt ? esc(fmtExpire(s.expiresAt)) : '长期有效'} ·
        创建于 ${esc(fmtAgo(s.createdAt))}</div>
      <div class="sh-actions">
        <button class="t-mini t-danger sh-revoke">撤销分享</button>
      </div>
    </div>`).join('');
  $$('.sh-revoke', box).forEach(btn => {
    btn.onclick = async () => {
      const item = btn.closest('.sh-item');
      const title = item.querySelector('.sh-title').textContent;
      const ok = await askConfirm({
        title: '撤销这条分享？',
        okText: '撤销', danger: true,
        html: `<div class="cfm-warn">撤销后，对方手里的链接与浏览器里的 Cookie
                 <b>立即失效</b>（浏览与图片请求都会被拒绝）。<br>不会删除任何照片。</div>
               <div class="sh-meta">${esc(title)}</div>`,
      });
      if (!ok) return;
      try {
        await apiPost('/api/share/revoke', { id: item.dataset.id });
        toast('已撤销，链接立即失效', 3000);
        await loadShares();
      } catch (e) { toast('撤销失败：' + e.message, 3800); }
    };
  });
}

$('#btnShareManage').onclick = () => { $('#sheet').hidden = true; openShareSheet(); };
$('#btnShareReload').onclick = () => loadShares();
$('#btnShareClose').onclick = () => { $('#shareSheet').hidden = true; };

/* 未授权访客（anon）的登录入口 */
$('#gateLogin').onclick = async () => {
  const pw = await askAdminAuth('输入管理口令以查看完整相册。');
  if (pw === null) return;
  try { await adminLogin(pw); }
  catch (e) { toast('授权失败：' + e.message, 4200); return; }
  toast('授权成功，正在载入…', 1800);
  setTimeout(() => location.reload(), 260);
};

/* ---------------- 保持浏览位置（删除/恢复后）----------------
   ★ 时间轴是「滚到哪渲染到哪」的懒加载（renderNextBatch 一批 20 个日期分组）。
     删除/恢复后整段重建，若不干预就会落到顶部 → 用户看到的是「最近的那几个月」，
     体感像是「跳到别的月份去了」。
     这里在重建前记住视口顶部那个日期分组（外加组内偏移），重建后：
       1) 继续 renderNextBatch 直到那个分组被渲染出来；
       2) 连续几帧把它对齐回原来位置（骨架→真图是异步的，高度会变，
          思路与 jumpToMonth 的 pinMonth 一致）。
     找不到原分组时（整组被删空）退到时间上紧邻的下一个分组，仍留在同一时期。 */
function captureViewAnchor() {
  const off = topbarOffset();
  if (wallMode() === 'year') {
    const card = $$('.month-card').find(el => el.getBoundingClientRect().bottom > off + 4);
    return card ? { key: card.dataset.ym, rel: card.getBoundingClientRect().top - off } : null;
  }
  for (const el of $$('.day-blk')) {
    const r = el.getBoundingClientRect();
    if (r.bottom > off + 4) {
      const anchor = { key: el.dataset.key, rel: Math.round(r.top - off) };
      const grid = el.querySelector('.grid'), top = grid.getBoundingClientRect().top;
      if (top < off && +el.dataset.total) {
        const g = gridGeometry(grid, +el.dataset.total);
        const row = Math.max(0, Math.floor((off - top) / (g.cell + g.gap)));
        anchor.index = Math.min(+el.dataset.total - 1, row * state.cols);
        anchor.tileRel = top + row * (g.cell + g.gap) - off;
      }
      return anchor;
    }
  }
  return null;
}

function restoreViewAnchor(anchor) {
  if (!anchor) return;
  let idx = state.days.findIndex(d => d.key === anchor.key || d.ym === anchor.key);
  let key = anchor.key, rel = anchor.rel;
  if (idx < 0) {                            // 该日期已不存在 → 取时间上更早的最近一天
    idx = state.days.findIndex(d => d.key < anchor.key);
    if (idx < 0) idx = state.days.length - 1;
    if (idx < 0) return;
    key = state.days[idx].key; rel = 0;
  }
  while (state.renderedDayIdx <= idx && !state.done) renderNextBatch();
  const epoch = state.viewEpoch, token = ++pinToken;
  let n = 0, last = -1, stable = 0, want = null;
  (function frame() {
    if (epoch !== state.viewEpoch || token !== pinToken) return;
    if (want !== null && Math.abs(window.scrollY - want) > 24) return;
    const el = $(`.day-blk[data-key="${key}"], .month-card[data-ym="${key}"]`);
    if (!el) { requestAnimationFrame(syncRail); return; }
    let top = el.getBoundingClientRect().top + window.scrollY - topbarOffset() - rel;
    if (wallMode() === 'year' && el.parentElement.firstElementChild === el) {
      const heading = el.closest('.year-collection').querySelector('.year-sep');
      top = heading.getBoundingClientRect().top + window.scrollY - topbarOffset();
    }
    if (wallMode() !== 'year' && key === anchor.key && Number.isInteger(anchor.index)) {
      const grid = el.querySelector('.grid'), g = gridGeometry(grid, +el.dataset.total);
      const index = Math.min(anchor.index, Math.max(0, +el.dataset.total - 1));
      top = grid.getBoundingClientRect().top + window.scrollY +
        Math.floor(index / state.cols) * (g.cell + g.gap) - topbarOffset() - anchor.tileRel;
    }
    if (Math.abs(top - last) < 2) stable++; else stable = 0;
    last = top;
    window.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
    want = window.scrollY;
    scheduleWindows();
    if (stable < 4 && ++n < 60) { requestAnimationFrame(frame); return; }
    requestAnimationFrame(syncRail);
  })();
}

/* ---------------- 刷新（删除/恢复后）---------------- */
async function refreshAfterChange() {
  // 搜索态没有 .day-blk，captureViewAnchor 会返回 null，行为同以前
  const anchor = captureViewAnchor();
  try {
    const d = await api('/api/libs');
    state.libs = d.libraries || [];
    if (d.trash && d.trash.retentionDays) trashDays = d.trash.retentionDays;
    buildChips(state.libs.filter(l => l.count > 0));
  } catch (e) { /* 刷新失败不阻塞主流程 */ }
  const cur = state.lib;
  state.lib = null;                       // 强制重建时间轴
  await selectLib(cur, true, anchor);     // ★ 重建后回到原日期，而不是回顶部
}

/* ---------------- 删除（= 移入回收站）---------------- */
async function deleteRels(rels) {
  const meta = libMeta() || {};
  if (!admin.required) {
    toast('未配置管理口令：删除功能已关闭（浏览不受影响）。如需开启，'
      + '请以 --admin-token 启动，或写 admin.json 的 adminToken。', 5600);
    return false;
  }
  if (!meta.canManage) {
    toast('当前图库不可管理源文件：' + (meta.manageReason || '未开启删除权限'), 4200);
    return false;
  }
  let dry;
  try {
    dry = await apiPost('/api/delete', { lib: state.lib, items: rels, dry: 1 });
  } catch (e) { toast('无法预览删除：' + e.message, 4200); return false; }
  if (!dry.files) {
    toast((dry.errors && dry.errors.length)
      ? ('无法删除：' + dry.errors[0].why) : '没有可删除的项目', 4200);
    return false;
  }
  const shown = (dry.items || []).slice(0, 12).map(x =>
    `<div>· ${esc(x.p)}${x.role === 'video' ? '（配对视频）' : ''}</div>`).join('');
  const more = (dry.items || []).length > 12
    ? `<div>… 其余 ${dry.items.length - 12} 个</div>` : '';
  const errs = (dry.errors || []).length
    ? `<div style="margin-top:8px;color:#ff9f0a">另有 ${dry.errors.length} 项将被跳过：` +
      `${esc(dry.errors[0].why)}</div>` : '';
  const ok = await askConfirm({
    title: '移入回收站？',
    okText: `移入回收站（${dry.files} 个文件）`,
    html:
      `<div>将把 <b>${dry.groups}</b> 个项目共 <b>${dry.files}</b> 个文件` +
      `${dry.videos ? `（含 <b>${dry.videos}</b> 个视频）` : ''}` +
      ` 移入回收站，${fmtSize(dry.bytes)}。</div>` +
      `<div style="margin-top:8px;max-height:150px;overflow:auto;font-size:12px;` +
      `color:var(--fg-3)">${shown}${more}</div>` +
      `<div style="margin-top:10px">源文件不会被永久删除，可在「最近删除」里恢复` +
      `（默认保留 ${trashDays} 天）。Live Photo 会连同配对视频一起移动。</div>` + errs
  });
  if (!ok) return false;
  try {
    const res = await apiPost('/api/delete', { lib: state.lib, items: rels, dry: 0 });
    toast(`已移入回收站：${res.moved} 个文件` +
          (res.failed ? `，${res.failed} 个失败` : '') +
          '（可在「最近删除」恢复）', 4600);
    return true;
  } catch (e) { toast('删除失败：' + e.message, 4200); return false; }
}
$('#selDelete').onclick = async () => {
  if (!sel.map.size) return;
  const rels = [...sel.map.values()].map(x => x.p);
  const done = await deleteRels(rels);
  if (done) { toggleSelectMode(false); await refreshAfterChange(); }
};
$('#vDel').onclick = async () => {
  const it = currentItem();
  if (!it) return;
  const done = await deleteRels([it.p]);
  if (done) { closeViewer(); await refreshAfterChange(); }
};

/* ---------------- 最近删除（回收站面板）---------------- */
let trashGroups = [];
let trashManage = {};
let trashStats = {};
const trashSel = new Set();

function trashThumbURL(itemId, s) {
  return `/api/trash/thumb?item=${enc(itemId)}&s=${s || THUMB_SIZE}`;
}
function typeLabel(g) {
  return g.type === 'video' ? '视频' : (g.type === 'live' ? '实况' : '照片');
}
function issueLabel(list) {
  const m = { failed: '操作失败', conflict: '恢复冲突', missing: '文件缺失' };
  return (list || []).map(x => m[x] || x).join('/');
}
function fmtAgo(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000), n = new Date();
  const p = x => String(x).padStart(2, '0');
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  if (d.toDateString() === n.toDateString()) return '今天 ' + hm;
  if (d.getFullYear() === n.getFullYear())
    return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`;
}

async function openTrash() {
  if (state.search.active) exitSearch();
  if (sel.active) toggleSelectMode(false);
  $('#trashSheet').hidden = false;
  await loadTrash();
}
async function loadTrash() {
  const box = $('#trashList');
  box.innerHTML = '<div class="t-empty">读取中…</div>';
  let d;
  try { d = await api('/api/trash', { lib: state.lib }); }
  catch (e) {
    box.innerHTML = `<div class="t-empty">读取失败：${esc(e.message)}</div>`;
    return;
  }
  trashGroups = d.groups || [];
  trashManage = d.manage || {};
  trashStats = d.stats || {};
  if (d.retentionDays) trashDays = d.retentionDays;
  trashSel.clear();
  renderTrash();
}
function renderTrash() {
  const box = $('#trashList');
  const m = trashManage[state.lib] || {};
  const allowed = !!m.allowed;

  const note = $('#trashNote');
  const canAct = allowed && admin.required;
  if (!admin.required) {
    note.hidden = false;
    note.textContent = '⚠ 未配置管理口令 → 恢复与永久删除已关闭，这里只做查看。'
      + '如需开启，请以 --admin-token 启动，或在 admin.json 里配置 adminToken（浏览不受影响）。';
  } else if (!allowed) {
    note.hidden = false;
    note.textContent = '⚠ ' + (m.reason || '当前图库不可管理源文件') +
      ' ｜ 这里只做查看：恢复与永久删除已被服务端禁用。';
  } else if (!admin.authed) {
    note.hidden = false;
    note.textContent = '需要管理授权：首次执行恢复/永久删除时会提示输入口令，'
      + '授权后在本浏览器会话内不再重复输入。';
  } else note.hidden = true;

  const nFiles = trashGroups.reduce((a, g) => a + g.files, 0);
  const bytes = trashGroups.reduce((a, g) => a + g.bytes, 0);
  const stuck = (trashStats && trashStats.stuck) || 0;
  $('#trashHint').textContent = trashGroups.length
    ? `${trashGroups.length} 个项目 / ${nFiles} 个文件 / ${fmtSize(bytes)}` +
      (stuck ? ` · ⚠ ${stuck} 项异常` : '')
    : '';
  $('#trashBar').hidden = !(canAct && trashGroups.length);
  $('#btnEmptyTrash').disabled = !(canAct && trashGroups.length);
  $('#emptyHint').textContent = trashGroups.length
    ? `${nFiles} 个 / ${fmtSize(bytes)}` : '';

  if (!trashGroups.length) {
    box.innerHTML = '<div class="t-empty">回收站是空的。<br>' +
      '在时间轴上点顶部「选择」，勾选照片后点「移入回收站」，' +
      '删除的项目会先来到这里。</div>';
    syncTrashBar();
    return;
  }

  box.innerHTML = '';
  trashGroups.forEach(g => {
    const first = g.items.find(x => x.k !== 'mov') || g.items[0];
    const el = document.createElement('div');
    el.className = 't-item' + (allowed ? '' : ' off');
    el.dataset.g = g.group;
    const days = (g.remainDays == null) ? ''
      : (g.expired ? ' ｜ <span class="t-days">已到期</span>'
                   : ` ｜ 剩余 <span class="t-days">${g.remainDays}</span> 天`);
    const issue = g.hasIssue
      ? ` ｜ <span class="t-issue">⚠ ${esc(issueLabel(g.issues))}</span>` : '';
    el.innerHTML =
      `<div class="t-thumb">` +
        (g.live ? '<span class="t-live">LIVE</span>' : '') +
        `<img loading="lazy" alt="" src="${trashThumbURL(first.itemId, THUMB_SIZE)}">` +
        `<span class="t-k">${typeLabel(g)}</span>` +
      `</div>` +
      `<div class="t-mid">` +
        `<div class="t-name">${esc(first.n || first.p)}</div>` +
        `<div class="t-meta">删除于 ${fmtAgo(g.deletedAt)} ｜ ${g.files} 个文件` +
          `${g.videos ? `（含 ${g.videos} 视频）` : ''}${days}${issue}</div>` +
      `</div>` +
      (allowed ? '<div class="t-pick"></div>' : '');
    const img = el.querySelector('img');
    img.addEventListener('error', () => {
      img.style.display = 'none';
      img.parentNode.insertAdjacentHTML('afterbegin',
        '<div style="position:absolute;inset:0;display:grid;place-items:center;' +
        'font-size:10px;color:var(--fg-3)">无缩略图</div>');
    }, { once: true });
    const thumb = el.querySelector('.t-thumb');
    thumb.style.cursor = 'zoom-in';
    thumb.onclick = ev => { ev.stopPropagation(); openPreview(first); };
    if (allowed) {
      el.onclick = () => {
        if (trashSel.has(g.group)) trashSel.delete(g.group);
        else trashSel.add(g.group);
        el.classList.toggle('sel', trashSel.has(g.group));
        syncTrashBar();
      };
    }
    box.appendChild(el);
  });
  syncTrashBar();
}
function selectedTrashGroups() {
  return trashGroups.filter(g => trashSel.has(g.group));
}
function selectedTrashIds() {
  const ids = [];
  selectedTrashGroups().forEach(g => g.items.forEach(x => ids.push(x.itemId)));
  return ids;
}
function syncTrashBar() {
  const n = selectedTrashGroups().length;
  $('#trashRestore').disabled = n === 0;
  $('#trashPurge').disabled = n === 0;
  $('#trashAll').textContent =
    (n && n === trashGroups.length) ? '取消全选' : '全选';
}
$('#trashAll').onclick = () => {
  const all = trashGroups.length > 0 && selectedTrashGroups().length === trashGroups.length;
  trashSel.clear();
  if (!all) trashGroups.forEach(g => trashSel.add(g.group));
  $$('#trashList .t-item').forEach(el =>
    el.classList.toggle('sel', trashSel.has(el.dataset.g)));
  syncTrashBar();
};
$('#trashRestore').onclick = async () => {
  const groups = selectedTrashGroups();
  if (!groups.length) return;
  const ids = selectedTrashIds();
  const policy = $('#trashPolicy').value;
  const btn = $('#trashRestore');
  btn.disabled = true;
  try {
    const res = await apiPost('/api/trash/restore',
      { lib: state.lib, items: ids, on_conflict: policy });
    const parts = [`恢复 ${res.restored} 个文件`];
    if (res.skipped) parts.push(`跳过 ${res.skipped} 个（目标已存在）`);
    if (res.failed) parts.push(`失败 ${res.failed} 个`);
    toast(parts.join('；'), 5200);
    await loadTrash();
    await refreshAfterChange();
  } catch (e) { toast('恢复失败：' + e.message, 4200); }
  finally { btn.disabled = false; }
};
$('#trashPurge').onclick = async () => {
  const groups = selectedTrashGroups();
  if (!groups.length) return;
  const ids = selectedTrashIds();
  const nFiles = groups.reduce((a, g) => a + g.files, 0);
  const bytes = groups.reduce((a, g) => a + g.bytes, 0);
  const ok = await askConfirm({
    title: '永久删除所选项目？',
    danger: true,
    okText: `永久删除（${nFiles} 个）`,
    html:
      `<div class="cfm-warn">永久删除不可恢复！</div>` +
      `<div style="margin-top:8px">将从回收站永久删除 <b>${groups.length}</b> 个项目 / ` +
      `<b>${nFiles}</b> 个文件（${fmtSize(bytes)}）。</div>` +
      `<div style="margin-top:8px;font-size:12px;color:var(--fg-3)">` +
      `此操作与「孤儿缓存清理」完全独立，不会触碰图库中的原始照片。</div>`
  });
  if (!ok) return;
  try {
    const res = await apiPost('/api/trash/purge',
      { lib: state.lib, items: ids, confirm: 1 });
    toast(`已永久删除 ${res.purged} 个文件` +
          (res.failed ? `，${res.failed} 个失败` : ''), 5200);
    await loadTrash();
  } catch (e) { toast('永久删除失败：' + e.message, 4200); }
};
$('#btnEmptyTrash').onclick = async () => {
  if (!trashGroups.length) return;
  const nFiles = trashGroups.reduce((a, g) => a + g.files, 0);
  const bytes = trashGroups.reduce((a, g) => a + g.bytes, 0);
  const ok = await askConfirm({
    title: '清空回收站？',
    danger: true,
    okText: '清空并永久删除',
    html:
      `<div class="cfm-warn">清空回收站不可恢复！</div>` +
      `<div style="margin-top:8px">将永久删除回收站中全部 <b>${trashGroups.length}</b> 个项目 / ` +
      `<b>${nFiles}</b> 个文件（${fmtSize(bytes)}）。</div>`
  });
  if (!ok) return;
  try {
    const res = await apiPost('/api/trash/empty', { lib: state.lib, confirm: 1 });
    toast(`已清空回收站：永久删除 ${res.purged} 个文件` +
          (res.failed ? `，${res.failed} 个失败` : ''), 5200);
    await loadTrash();
  } catch (e) { toast('清空失败：' + e.message, 4200); }
};
$('#btnTrashReload').onclick = () => loadTrash();
$('#btnTrash').onclick = () => openTrash();
$('#trashSheet').addEventListener('click', e => {
  if (e.target.dataset.close !== undefined) $('#trashSheet').hidden = true;
});

/* =====================================================
   窗口变化
   ===================================================== */
let rt = null;
window.addEventListener('resize', () => {
  clearTimeout(rt);
  rt = setTimeout(() => {
    const anchor = captureViewAnchor();
    computeCols(); syncTopbarHeight();
    sizeSkeletons($$('.day-blk'));            // 列数变了，重算骨架高度
    rearmObserver();                          // 预加载余量也跟着格子尺寸走
    updateWindows();
    if (anchor) restoreViewAnchor(anchor);
    syncRail();
  }, 120);
}, { passive: true });
if (window.ResizeObserver) {
  new ResizeObserver(syncTopbarHeight).observe($('#topbar'));
  new ResizeObserver(syncRailHeight).observe($('#rail'));
}

/* Confirmed collections share the timeline's bounded pages and media lane. */
function resetCollection() {
  state.collection = { tab: 'photos', context: null, albums: [] };
  syncCollectionNav();
}
function syncCollectionNav() {
  $$('#collectionNav button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.collection === state.collection.tab)));
}
function beginCollection(tab) {
  clearTimeout(searchTimer);
  ++state.viewEpoch;
  resetBrowseRequests();
  if (!viewer.hidden) closeViewer();
  if (sel.active) toggleSelectMode(false);
  state.search.request = null; state.search.active = true; state.search.done = true;
  state.collection.tab = tab; state.collection.context = null;
  state.switching = false;
  thumbReset();
  $('#searchBar').hidden = true; $('#searchInput').value = '';
  $('#endTip').hidden = true; $('#denCtl').hidden = true;
  rail.hidden = true; hideToTop(); syncRailHeight(); syncTopbarHeight(); syncCollectionNav();
  $('#timeline').innerHTML = '';
  window.scrollTo(0, 0);
  return state.viewEpoch;
}
function collectionCover(item) {
  return `<span class="cover-cell"><img alt="" decoding="async" data-thumb-url="${esc(thumbURL(item.p))}"></span>`;
}
function observeCollectionCovers() {
  $$('#timeline [data-thumb-url]').forEach(img => thumbObserver.observe(img));
}
async function openCollections(tab) {
  if (!sess.canManage || !state.lib) return;
  if (tab === 'photos') { exitSearch(); $('#denCtl').hidden = !state.days.length; return; }
  const epoch = beginCollection(tab), lib = state.lib;
  if (tab === 'memories') { renderMemories(); return; }
  $('#timeline').innerHTML = '<div class="collection-empty" role="status">正在整理相册…</div>';
  try {
    const result = await api('/api/vision/albums', { lib }, browseController.signal);
    if (epoch !== state.viewEpoch || lib !== state.lib) return;
    state.collection.albums = result.albums;
    const albums = result.albums.filter(e => tab === 'people' ? e.kind === 'person' : e.kind !== 'person');
    const words = { person: '人物', cat: '猫', dog: '狗', bird: '鸟' };
    $('#timeline').innerHTML = `<section class="collection-page"><div class="collection-heading"><div><p class="collection-eyebrow">${tab === 'people' ? '熟悉的面孔' : '陪伴的时光'}</p><h1>${tab === 'people' ? '人物相册' : '动物相册'}</h1><p>从一个名字，翻开一起走过的日子。</p></div><span>${albums.length} 个相册 · 当前图库</span></div><label class="collection-find">查找相册<input id="collectionFind" type="search" placeholder="输入名字" autocomplete="off"></label><div class="collection-cards">${albums.map(e => `<button type="button" class="collection-card" data-album="${esc(e.id)}" data-album-name="${esc(e.name)}">${collectionCover(e.cover)}<span class="collection-card-meta"><strong>${esc(e.name)}</strong><span>${e.count.toLocaleString()} 张照片${e.kind === 'person' ? '' : ' · '+words[e.kind]}</span></span></button>`).join('')}</div><p class="collection-empty" id="collectionNoMatch" ${albums.length ? 'hidden' : ''}>${albums.length ? '没有找到这个名字。' : '当前图库还没有已确认的相册。已关注并确认的照片会显示在这里。'}</p></section>`;
    $('#collectionFind').oninput = event => {
      const q = event.target.value.trim().toLocaleLowerCase(); let count = 0;
      $$('.collection-card').forEach(card => { card.hidden = !card.dataset.albumName.toLocaleLowerCase().includes(q); if (!card.hidden) count++; });
      $('#collectionNoMatch').hidden = count > 0;
    };
    observeCollectionCovers();
  } catch (error) {
    if (epoch !== state.viewEpoch || error.name === 'AbortError') return;
    $('#timeline').innerHTML = '<div class="collection-empty">相册暂时无法读取。<button id="collectionRetry">重试</button></div>';
    $('#collectionRetry').onclick = () => openCollections(tab);
  }
}
function renderMemories() {
  const months = state.tree?.months || [];
  const now = new Date(), current = now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0');
  const past = months.filter(m => m.ym < current);
  $('#timeline').innerHTML = `<section class="collection-page"><div class="collection-heading"><div><p class="collection-eyebrow">让过去，偶尔回来</p><h1>回忆</h1><p>不必记得日期，也能遇见值得重看的照片。</p></div></div><div id="memorySamples" aria-live="polite"></div><div class="memory-cards"><button class="memory-card memory-today" data-memory="today"><span>那年，今天</span><strong>往年今日</strong><small>看看过去的今天，留下了什么</small></button><button class="memory-card memory-random" data-memory="random" ${past.length ? '' : 'disabled'}><span>给自己一点惊喜</span><strong>随手翻一段过去</strong><small>随机打开一个过去的月份</small></button></div><h2 class="collection-subtitle">挑一段时光</h2><div class="memory-months">${months.map(m => `<button data-memory-month="${m.ym}"><strong>${m.ym.slice(0,4)} 年 ${+m.ym.slice(5)} 月</strong><span>${m.n.toLocaleString()} 张照片</span></button>`).join('')}</div></section>`;
  loadMemorySamples(state.viewEpoch, state.lib);
}
function randomMemory() {
  const now = new Date(), current = now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0');
  const months = (state.tree?.months || []).filter(m => m.ym < current && m.ym !== state.collection.context?.album.ym);
  const fallback = (state.tree?.months || []).filter(m => m.ym < current);
  const pool = months.length ? months : fallback;
  if (pool.length) openAlbum([], { memory: 'month', ym: pool[Math.floor(Math.random()*pool.length)].ym }, 'memories');
}
async function openAlbum(ids, options = {}, tab = state.collection.tab) {
  if (!sess.canManage) return;
  const epoch = beginCollection(tab), lib = state.lib;
  const album = { ids: ids.join(','), ym: options.ym || '', memory: options.memory || '' };
  const context = { lib, epoch, key: 'album:'+JSON.stringify(album), album };
  state.collection.context = context;
  $('#timeline').innerHTML = '<section class="collection-page"><div id="albumHeading" class="album-heading" role="status">正在打开相册…</div></section><div class="grid" id="albumGrid"></div>';
  try {
    const result = await fetchBrowsePage(context, 0);
    if (epoch !== state.viewEpoch || lib !== state.lib) return;
    const names = state.collection.albums;
    const primary = names.find(e => e.id === ids[0]);
    const range = result.months.length ? result.months.at(-1).ym+' — '+result.months[0].ym : '';
    $('#albumHeading').innerHTML = `<button class="album-back" data-collection="${tab}">← ${tab === 'memories' ? '回忆' : tab === 'animals' ? '动物相册' : '人物相册'}</button><div class="collection-heading"><div><p class="collection-eyebrow">${ids.length === 2 ? '共同出镜' : album.memory ? '时光回忆' : '确认过的每一次相遇'}</p><h1>${esc(result.title)}</h1><p>${result.total.toLocaleString()} 张照片${range && !album.memory ? ' · '+range : ''}</p></div></div>${ids.length ? `<div class="album-filters"><label>时间<select id="albumMonth"><option value="">全部月份</option>${result.months.map(m => `<option value="${m.ym}" ${m.ym === album.ym ? 'selected' : ''}>${m.ym} · ${m.count} 张</option>`).join('')}</select></label>${ids.length === 2 ? `<button data-album="${ids[0]}">只看${esc(primary?.name || '这个相册')}</button>` : ''}</div>${result.partners.length ? `<div class="album-together"><span>共同出镜</span><p class="album-together-hint">点一个名字，查看共同出镜的照片。</p><div>${result.partners.map(e => `<button data-together="${e.id}" data-anchor="${ids[0]}">${esc(e.name)}<small>${e.count} 张</small></button>`).join('')}</div></div>` : ''}` : '<button class="album-shuffle" data-memory="random">再翻一段过去 ↗</button>'}`;
    if (ids.length === 1 && !album.memory) $('#albumHeading').insertAdjacentHTML('beforeend', `<div class="growth-switch"><button aria-pressed="true">全部照片</button><button data-growth="${esc(ids[0])}">成长轨迹</button></div>`);
    if ($('#albumMonth')) $('#albumMonth').onchange = event => openAlbum(ids, { ym: event.target.value }, tab);
    const grid = $('#albumGrid');
    if (!result.total) {
      grid.outerHTML = `<div class="collection-empty">${album.memory === 'today' ? '往年今天还没有照片，试试随机翻一段过去。' : '这段时间没有符合条件的照片。'}</div>`;
    } else {
      $('#denCtl').hidden = false; syncDen();
      nearbyGrids.add(grid); groupObserver.observe(grid);
      await loadGridWindow(grid, context, result.total);
    }
  } catch (error) {
    if (epoch !== state.viewEpoch || error.name === 'AbortError') return;
    $('#albumHeading').innerHTML = '<p>相册加载失败，请重试。</p><button id="albumRetry">重新打开</button>';
    $('#albumRetry').onclick = () => openAlbum(ids, options, tab);
  }
}
document.addEventListener('click', event => {
  const nav = event.target.closest('[data-collection]');
  if (nav) { openCollections(nav.dataset.collection); return; }
  const together = event.target.closest('[data-together]');
  if (together) { openAlbum([together.dataset.anchor,together.dataset.together], { ym:state.collection.context?.album.ym || '' }); return; }
  const card = event.target.closest('[data-album]');
  if (card) { openAlbum([card.dataset.album]); return; }
  const month = event.target.closest('[data-memory-month]');
  if (month) { openAlbum([], { memory:'month', ym:month.dataset.memoryMonth }, 'memories'); return; }
  const memory = event.target.closest('[data-memory]');
  if (memory) { if (memory.dataset.memory === 'random') randomMemory(); else openAlbum([], { memory:'today' }, 'memories'); }
});

/* Real-event samples: a small local manifest, two images in flight, original BGM. */
let memorySession = null;
async function openStoryAlbum(id) {
  closeMemory();const epoch=beginCollection('memories'),lib=state.lib;
  const context={lib,epoch,key:'story:'+id,story:id};
  $('#timeline').innerHTML='<section class="collection-page"><div id="storyAlbumHeading">正在打开完整相册…</div></section><div class="grid" id="storyAlbumGrid"></div>';
  try{
    const page=await fetchBrowsePage(context,0);if(epoch!==state.viewEpoch||lib!==state.lib)return;
    $('#storyAlbumHeading').innerHTML=`<button class="album-back" data-collection="memories">← 回忆</button><div class="collection-heading"><div><p class="collection-eyebrow">这次经历的完整相册</p><h1>${esc(page.title)}</h1><p>${page.total} 张照片 · 按拍摄顺序</p></div></div><button class="story-play-button" data-story="${id}">▶ 精选播放</button><button class="story-play-button" data-story-full="${id}">▶ 完整播放</button>`;
    const grid=$('#storyAlbumGrid');if(page.total){$('#denCtl').hidden=false;syncDen();nearbyGrids.add(grid);groupObserver.observe(grid);await loadGridWindow(grid,context,page.total);}
  }catch(error){if(epoch===state.viewEpoch&&error.name!=='AbortError')$('#storyAlbumHeading').innerHTML='<p>这次经历暂时无法读取，请返回回忆后重试。</p><button class="album-back" data-collection="memories">← 回忆</button>';}
}
function openMemorySettings() {
  const s=memorySession;if(!s?.story)return;pauseMemory();
  $('#memorySettingsPanel').hidden=false;$('#memoryEditTitle').value=s.story.title;$('#memoryEditPlace').value=s.story.place;
  $('#memoryEditTheme').value=s.story.theme;$('#memoryEditLayout').value=s.layoutChoice;$('#memoryEditMode').value=s.mode;
  s.draftCover=s.story.cover_rel||s.story.cover.p;s.coverChanged=false;$('#memoryCoverPicker').hidden=true;$('#memorySettingsMessage').textContent='风格可即时预览，保存后会用于这段回忆。';$('#memoryEditTitle').focus();
  $('#memorySplitTime').value=s.story.photos[Math.floor(s.story.photos.length/2)]?.captured.slice(0,19)||'';
  $('#memoryMergeTarget').innerHTML='<option value="">正在读取其他回忆…</option>';
  api('/api/vision/stories',{lib:s.lib},s.controller.signal).then(result=>{
    if(memorySession!==s)return;s.organizeTargets=result.stories;
    $('#memoryMergeTarget').innerHTML='<option value="">选择另一段回忆</option>'+result.stories.filter(r=>r.id!==s.id).map(r=>`<option value="${r.id}">${esc(r.date+' · '+r.title+' · '+r.full_count+' 张')}</option>`).join('');
  }).catch(()=>{if(memorySession===s)$('#memoryMergeTarget').innerHTML='<option value="">其他回忆暂时无法读取</option>';});
}
async function organizeMemory(action) {
  const s=memorySession;if(!s?.story||s.organizing||s.saving)return;
  if($('#memoryEditTitle').value.trim()!==s.story.title||$('#memoryEditPlace').value.trim()!==s.story.place||$('#memoryEditTheme').value!==s.story.theme||s.draftCover!==s.story.cover_rel){$('#memorySettingsMessage').textContent='请先保存开场修改，再整理事件。';return;}
  const ids=action==='undo'?[]:[s.id],revisions=action==='undo'?{}:{[s.id]:s.story.revision};
  if(action==='merge'){
    const target=s.organizeTargets?.find(r=>r.id===$('#memoryMergeTarget').value);
    if(!target){$('#memorySettingsMessage').textContent='先选择另一段回忆。';return;}ids.push(target.id);revisions[target.id]=target.revision;
  }
  s.organizing=true;$('#memorySettingsMessage').textContent='正在整理经历…';
  try{
    const result=await apiPost('/api/vision/story/organize',{lib:s.lib,action,ids,revisions,split_at:$('#memorySplitTime').value});
    if(memorySession!==s)return;closeMemory();await openCollections('memories');if(result.created[0])openMemory(result.created[0]);
  }catch(error){if(memorySession===s)$('#memorySettingsMessage').textContent=error.message;}
  finally{s.organizing=false;}
}
async function loadMemoryCoverPage(offset=0) {
  const s=memorySession;if(!s?.story)return;const token=s.coverToken=(s.coverToken||0)+1;
  $$('#memoryCoverGrid img').forEach(img=>img.removeAttribute('src'));
  s.coverOffset=offset;$('#memoryCoverPicker').hidden=false;$('#memoryCoverGrid').textContent='正在读取封面照片…';
  try{
    const page=await api('/api/vision/story/items',{lib:s.lib,id:s.id,mode:'full',off:offset,lim:20},s.controller.signal);
    if(memorySession!==s||token!==s.coverToken)return;
    $('#memoryCoverGrid').innerHTML=page.items.map(p=>`<button type="button" data-memory-cover="${esc(p.p)}" aria-label="选择这张作为开场封面" aria-pressed="${s.draftCover===p.p}"><img src="${esc('/thumb?'+new URLSearchParams({lib:s.lib,p:p.p,s:640}))}" loading="lazy" alt="封面候选"></button>`).join('');
    $('#memoryCoverPage').textContent=(offset+1)+'–'+(offset+page.items.length)+' / '+page.total;$('#memoryCoverPrev').disabled=offset===0;$('#memoryCoverNext').disabled=offset+page.items.length>=page.total;
  }catch(error){if(memorySession===s&&error.name!=='AbortError')$('#memoryCoverGrid').textContent='封面照片暂时无法读取，请重试。';}
}
async function saveMemorySettings() {
  const s=memorySession;if(!s?.story||s.saving)return;
  const changes={title:$('#memoryEditTitle').value.trim(),place:$('#memoryEditPlace').value.trim(),theme:$('#memoryEditTheme').value};
  if(s.coverChanged)changes.cover_rel=s.draftCover;
  const options={layout:$('#memoryEditLayout').value,mode:$('#memoryEditMode').value};
  s.saving=true;$('#memorySave').disabled=true;$('#memorySettingsMessage').textContent='正在保存…';
  try{
    await apiPost('/api/vision/story/edit',{lib:s.lib,id:s.id,revision:s.story.revision,changes});
    if(memorySession!==s)return;loadMemorySamples(state.viewEpoch,state.lib);await openMemory(s.id,options);
  }catch(error){if(memorySession===s)$('#memorySettingsMessage').textContent=error.message;}
  finally{s.saving=false;$('#memorySave').disabled=false;}
}
async function loadMemorySamples(epoch, lib) {
  try {
    const result = await api('/api/vision/stories', {lib,layout:memoryLayout()}, browseController.signal);
    if (epoch !== state.viewEpoch || lib !== state.lib || state.collection.tab !== 'memories') return;
    const slot = $('#memorySamples'); if (!slot || !result.stories.length) return;
    slot.innerHTML = `<div class="story-section-heading"><div><h2>把日子，放成一部电影</h2><p>几段真实时光 · 精选照片与轻音乐</p></div><span>你的回忆</span></div><div class="story-cards">${result.stories.map(s => `<button type="button" class="story-card story-${s.theme}" data-story="${s.id}">${collectionCover(s.cover)}<span class="story-card-shade"></span><span class="story-card-copy"><small>${esc(s.date.replaceAll('-',' · '))}</small><strong>${esc(s.title)}</strong><span>${esc(s.place || '一段真实时光')}</span><em>▶ 精选播放 · 共 ${s.full_count} 张</em></span></button>`).join('')}</div><p class="story-place-note">附近地名由 <a href="https://www.geonames.org/" target="_blank" rel="noopener noreferrer">GeoNames</a> 离线估计，不代表具体景点。</p>`;
    observeCollectionCovers();
  } catch (error) {
    if (epoch !== state.viewEpoch || error.name === 'AbortError') return;
    const slot = $('#memorySamples'); if (slot) { slot.innerHTML = '<p class="collection-empty">回忆小样暂时无法读取。<button id="storiesRetry">重试</button></p>'; $('#storiesRetry').onclick = () => loadMemorySamples(epoch,lib); }
  }
}
function memoryCount(session) { return session.story.count ?? session.story.photos.length; }
function memoryLayout(choice='auto') { return choice==='auto' ? (window.matchMedia('(max-width:700px)').matches ? 'portrait' : 'landscape') : choice; }
async function openMemory(id, options={}) {
  if (!sess.canManage) return;
  closeMemory();
  if (!viewer.hidden) closeViewer();
  const session = {id,lib:state.lib,epoch:state.viewEpoch,controller:new AbortController(),index:0,elapsed:0,playing:false,ready:false,muted:false,returnFocus:document.activeElement,
    layoutChoice:options.layout||'auto',layout:memoryLayout(options.layout),mode:options.mode||'selected',pages:new Map(),pendingPages:new Map(),frameToken:0};
  memorySession = session; $('#memoryPlayer').hidden = false;
  document.body.style.overflow = 'hidden'; thumbHold(true);
  $('#memoryStage').innerHTML = '<p class="memory-loading" role="status">正在准备这段时光…</p>';
  $('#memoryProgress').innerHTML = ''; $('#memoryPlayerName').textContent = '回忆小样';
  $('#memorySettingsPanel').hidden=true;
  $('#memoryPlay').disabled = true; $('#memoryPlay').textContent = '正在准备'; $('#memoryClose').focus();
  try {
    const story = await api('/api/vision/story', {lib:session.lib,id,layout:session.layout,mode:session.mode}, session.controller.signal);
    if (memorySession !== session || session.epoch !== state.viewEpoch || session.lib !== state.lib) return;
    session.story = story;
    session.pages.set(0,story.photos);
    $('#memoryPlayerName').textContent = story.title;
    $('#memoryPlayer').className = 'memory-player story-'+story.theme+' memory-layout-'+session.layout;
    $('#memoryProgress').innerHTML = Array.from({length:Math.min(60,memoryCount(session)+2)},()=>'<span><i></i></span>').join('');
    $('#memoryAll').textContent='全部 '+story.full_count+' 张';
    $('#memoryPlay').disabled = false; $('#memoryPlay').textContent = '播放回忆';
    $('#memorySound').textContent = '♫ 配乐开'; $('#memorySound').setAttribute('aria-pressed','true');
    renderMemoryFrame(session);
  } catch (error) {
    if (memorySession !== session || error.name === 'AbortError') return;
    $('#memoryStage').innerHTML = '<div class="memory-loading">这段回忆暂时无法打开。<button id="memoryRetry">重试</button></div>';
    $('#memoryRetry').onclick = () => openMemory(id,options);
  }
}
function memoryPictureURL(session, item) {
  return '/photo?'+new URLSearchParams({lib:session.lib,p:item.p,fmt:'jpeg'});
}
async function memoryPhotoAt(session,index) {
  const off=Math.floor(index/120)*120;
  if(!session.pages.has(off)){
    if(!session.pendingPages.has(off)){
      const request=api('/api/vision/story/items',{lib:session.lib,id:session.id,mode:session.mode,layout:session.layout,off,lim:120},session.controller.signal).then(page=>{
        if(page.total!==memoryCount(session)||page.revision!==session.story.revision)throw Error('回忆已更新，请重新打开');
        session.pages.set(off,page.items.map(item=>({item,captured:item.captured})));
        while(session.pages.size>3)session.pages.delete(session.pages.keys().next().value);
      }).finally(()=>session.pendingPages.delete(off));
      session.pendingPages.set(off,request);
    }
    await session.pendingPages.get(off);
  }
  return session.pages.get(off)?.[index-off];
}
function memoryTitleMarkup(title) {return title.length<=8&&/^[\u3400-\u9fff]+$/.test(title) ? esc(title.slice(0,2))+'<br>'+esc(title.slice(2)) : esc(title);}
async function renderMemoryFrame(session) {
  if (session !== memorySession) return;
  const story = session.story, n = memoryCount(session), index = session.index,token=++session.frameToken;
  const intro = index === 0, outro = index === n+1;
  const stage = $('#memoryStage'); session.ready = false;
  let entry;
  try {if(!intro&&!outro)entry=await memoryPhotoAt(session,index-1);}
  catch(error){if(memorySession===session&&token===session.frameToken){pauseMemory();toast(error.message);};return;}
  if(memorySession!==session||token!==session.frameToken)return;
  const photo=intro||outro ? story.cover : entry?.item;if(!photo)return;
  // Keep the previous photo visible until the next one has decoded.
  stage.querySelectorAll('img').forEach(img => {img.onload = img.onerror = null;});
  while (stage.children.length > 1) stage.firstElementChild.remove();
  const wrapper = document.createElement('div');
  wrapper.innerHTML = `<div class="memory-frame ${intro || outro ? 'memory-opening' : 'memory-photo'}"><img class="memory-picture" alt="${intro ? '回忆封面' : outro ? '回忆尾声' : '回忆照片'}"><div class="memory-picture-shade"></div>${intro || outro ? `<div class="memory-title-layout"><p class="memory-kicker">${outro ? 'UNTIL WE MEET AGAIN' : 'A MOMENT TO KEEP'}</p><div class="memory-title-rule"></div><h1 class="${story.title.length>8?'memory-title-long':''}">${outro ? '愿此刻<br>常在' : memoryTitleMarkup(story.title)}</h1><p class="memory-location">${esc(story.place || '那一天的片段')}</p><p class="memory-date">${esc(story.date.replaceAll('-',' · '))}</p><p class="memory-people">${story.people.map(p=>esc(p.name)).join(' · ')}</p><span class="memory-seal">${outro ? '珍藏' : '记忆'}</span></div>` : `<div class="memory-photo-caption"><span>${esc(story.place || story.date)}</span><span>${esc(entry.captured.slice(11,16))}</span></div>`}<div class="memory-image-status" role="status">正在载入照片…</div></div>`;
  const frame = wrapper.firstElementChild, img = frame.querySelector('img');
  stage.appendChild(frame);session.frame = frame;
  const loaded = () => {
    if (memorySession !== session || session.frame !== frame || !frame.isConnected) return;
    session.ready = true; frame.classList.add('memory-frame-ready'); frame.querySelector('.memory-image-status')?.remove();
    if (session.playing) frame.classList.add('memory-frame-playing');
    [...stage.children].filter(old => old !== frame).forEach(old => {old.classList.add('memory-frame-leaving');setTimeout(() => old.remove(),900);});
  };
  img.onload = loaded;
  img.onerror = () => {
    if (memorySession !== session || !frame.isConnected) return;
    pauseMemory(); frame.querySelector('.memory-image-status').innerHTML = '照片暂时无法读取 <button id="memoryImageRetry">重试</button>';
    frame.querySelector('#memoryImageRetry').onclick = () => renderMemoryFrame(session);
  };
  img.src = memoryPictureURL(session,photo);
  if (img.complete && img.naturalWidth) loaded();
  // Preload exactly one following image; no whole-event download on opening.
  if (session.nextImage) {session.nextImage.removeAttribute('src');session.nextImage = null;}
  if (index < n) memoryPhotoAt(session,index).then(next=>{if(memorySession===session&&token===session.frameToken&&next){session.nextImage=new Image();session.nextImage.src=memoryPictureURL(session,next.item);}}).catch(()=>{});
  $('#memoryCounter').textContent = intro ? '序章' : outro ? '尾声' : index+' / '+n;
  $('#memoryPrev').disabled = index === 0; $('#memoryNext').disabled = outro;
  updateMemoryProgress(session);
}
function updateMemoryProgress(session) {
  const duration = session.index === 0 ? 6500 : session.index === memoryCount(session)+1 ? 4500 : 3800;
  const bars=$$('#memoryProgress i'),position=(session.index+Math.min(1,session.elapsed/duration))/(memoryCount(session)+2)*bars.length;
  bars.forEach((bar,index)=>{bar.style.transform=`scaleX(${Math.max(0,Math.min(1,position-index))})`;});
  return duration;
}
function moveMemory(delta) {
  const session = memorySession; if (!session?.story) return;
  session.index = Math.max(0,Math.min(memoryCount(session)+1,session.index+delta));session.elapsed = 0;session.ended=false;session.last = performance.now();
  renderMemoryFrame(session);
}
function memoryTick(now) {
  const session = memorySession; if (!session?.playing) return;
  if (session.ready) session.elapsed += Math.min(150,now-session.last);
  session.last = now;
  const duration = updateMemoryProgress(session);
  if (session.elapsed >= duration) {
    if (session.index === memoryCount(session)+1) {pauseMemory();$('#memoryPlay').textContent='再看一次';session.ended=true;return;}
    moveMemory(1);
  }
  session.animation = requestAnimationFrame(memoryTick);
}
function pauseMemory() {
  const session = memorySession; if (!session) return;
  session.playing = false; cancelAnimationFrame(session.animation);
  session.music?.context.suspend().catch(()=>{});
  $('#memoryStage .memory-frame')?.classList.remove('memory-frame-playing');
  $('#memoryPlay').textContent = session.story ? '继续播放' : '正在准备';
}
function playMemory() {
  const session = memorySession; if (!session?.story) return;
  if (session.playing) {pauseMemory();return;}
  if (session.ended) {session.ended=false;session.index=0;session.elapsed=0;renderMemoryFrame(session);}
  session.playing = true;session.last = performance.now();$('#memoryPlay').textContent='暂停';
  $('#memoryStage .memory-frame')?.classList.add('memory-frame-playing');
  try {
    if (!session.music) session.music = createMemoryMusic();
    session.music.context.resume().catch(()=>{});
    session.music.gain.gain.value = session.muted ? 0 : .38;
  } catch {session.muted=true;$('#memorySound').textContent='配乐不可用';}
  session.animation = requestAnimationFrame(memoryTick);
}
function createMemoryMusic() {
  // Original local motif: soft sine/triangle notes, no external tracks or API.
  const Audio = window.AudioContext || window.webkitAudioContext;
  const context = new Audio(), gain = context.createGain(), compressor = context.createDynamicsCompressor();
  gain.gain.value=.38;gain.connect(compressor);compressor.connect(context.destination);
  const chords=[[48,55,60,64],[45,52,57,60],[41,48,53,57],[43,50,55,59]];
  const melody=[72,76,79,76,74,72,67,71,69,72,76,72,67,64,62,67];
  let beat=0,next=context.currentTime+.08;
  function note(midi,start,duration,volume,type='sine') {
    const oscillator=context.createOscillator(),envelope=context.createGain();oscillator.type=type;
    oscillator.frequency.value=440*Math.pow(2,(midi-69)/12);
    envelope.gain.setValueAtTime(0,start);envelope.gain.linearRampToValueAtTime(volume,start+.045);
    envelope.gain.exponentialRampToValueAtTime(.0001,start+duration);oscillator.connect(envelope);envelope.connect(gain);
    oscillator.onended=()=>{oscillator.disconnect();envelope.disconnect();};oscillator.start(start);oscillator.stop(start+duration+.03);
  }
  function schedule() {
    if (context.state!=='running') return;
    while(next<context.currentTime+1.4) {
      const chord=chords[Math.floor(beat/8)%4];
      note(chord[beat%4],next,2.8,.13);
      if(beat%2===0)note(melody[Math.floor(beat/2)%melody.length],next+.035,2.1,.075,'triangle');
      if(beat%8===0)chord.forEach(midi=>note(midi-12,next,5.5,.035));
      beat++;next+=.75;
    }
  }
  const timer=setInterval(schedule,250);context.resume().then(schedule).catch(()=>{});
  return {context,gain,timer};
}
function closeMemory() {
  const session=memorySession; if (!session) return;
  memorySession=null;session.controller.abort();cancelAnimationFrame(session.animation);
  if(session.music){clearInterval(session.music.timer);session.music.context.close().catch(()=>{});}
  session.nextImage?.removeAttribute('src');
  $$('#memoryCoverGrid img').forEach(img=>img.removeAttribute('src'));$('#memoryCoverGrid').innerHTML='';
  $$('#memoryStage img').forEach(img=>{img.onload=img.onerror=null;img.removeAttribute('src');});
  $('#memoryStage').innerHTML='';$('#memoryPlayer').hidden=true;document.body.style.overflow='';thumbHold(false);
  if(session.returnFocus?.isConnected)session.returnFocus.focus();
}
document.addEventListener('click',event=>{
  const cover=event.target.closest('[data-memory-cover]');if(cover&&memorySession){memorySession.draftCover=cover.dataset.memoryCover;memorySession.coverChanged=true;$$('[data-memory-cover]').forEach(b=>b.setAttribute('aria-pressed',String(b===cover)));$('#memorySettingsMessage').textContent='已选择封面，点击“保存并应用”生效。';return;}
  const full=event.target.closest('[data-story-full]');if(full){openMemory(full.dataset.storyFull,{mode:'full'});return;}
  const button=event.target.closest('[data-story]');if(button)openMemory(button.dataset.story);
});
$('#memoryClose').onclick=closeMemory;$('#memoryPlay').onclick=playMemory;
$('#memoryAll').onclick=()=>{if(memorySession?.story)openStoryAlbum(memorySession.id);};
$('#memoryOptions').onclick=openMemorySettings;$('#memorySave').onclick=saveMemorySettings;
$('#memorySettingsClose').onclick=()=>{$('#memorySettingsPanel').hidden=true;const s=memorySession;if(s){s.coverToken=(s.coverToken||0)+1;if(s.story)$('#memoryPlayer').className='memory-player story-'+s.story.theme+' memory-layout-'+s.layout;}$$('#memoryCoverGrid img').forEach(img=>img.removeAttribute('src'));$('#memoryCoverGrid').innerHTML='';$('#memoryOptions').focus();};
$('#memoryEditTheme').onchange=event=>{const s=memorySession;if(s)$('#memoryPlayer').className='memory-player story-'+event.target.value+' memory-layout-'+s.layout;};
$('#memoryChooseCover').onclick=()=>loadMemoryCoverPage(0);$('#memoryCoverPrev').onclick=()=>loadMemoryCoverPage(Math.max(0,memorySession.coverOffset-20));$('#memoryCoverNext').onclick=()=>loadMemoryCoverPage(memorySession.coverOffset+20);
$('#memoryMerge').onclick=()=>organizeMemory('merge');$('#memorySplit').onclick=()=>organizeMemory('split');$('#memoryUndoOrganize').onclick=()=>organizeMemory('undo');
$('#memoryPrev').onclick=()=>moveMemory(-1);$('#memoryNext').onclick=()=>moveMemory(1);
$('#memorySound').onclick=()=>{const s=memorySession;if(!s)return;s.muted=!s.muted;if(s.music)s.music.gain.gain.value=s.muted?0:.38;$('#memorySound').textContent=s.muted?'♫ 配乐关':'♫ 配乐开';$('#memorySound').setAttribute('aria-pressed',String(!s.muted));};
document.addEventListener('keydown',event=>{
  if(!memorySession)return;
  if(!$('#memorySettingsPanel').hidden){
    if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();$('#memorySettingsClose').click();}
    if(event.key==='Tab'){const fields=$$('#memorySettingsPanel input,#memorySettingsPanel select,#memorySettingsPanel button').filter(b=>!b.disabled&&b.getClientRects().length);const first=fields[0],last=fields.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
    return;
  }
  if(event.key==='Tab'){
    const buttons=$$('#memoryPlayer button').filter(b=>!b.disabled&&b.getClientRects().length);
    const first=buttons[0],last=buttons.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    return;
  }
  if(['Escape',' ','ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();event.stopImmediatePropagation();if(event.key==='Escape')closeMemory();else if(event.key===' ')playMemory();else moveMemory(event.key==='ArrowLeft'?-1:1);}
},{capture:true});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&memorySession?.playing)pauseMemory();});

/* 启动 */
$$('.wall-levels button').forEach(button => {
  button.onclick = () => setDensity(button.dataset.wallMode === 'year' ? 4 :
    button.dataset.wallMode === 'month' ? 3 : (state.lastDayDensity ?? 1));
});
boot();

/* Native disclosure keeps maintenance tools out of the photo surface. */
const utilityMenu = $('#utilityMenu');
if (utilityMenu) {
  utilityMenu.addEventListener('click', e => {
    if (e.target.closest('button')) utilityMenu.open = false;
  });
  document.addEventListener('click', e => {
    if (!utilityMenu.contains(e.target)) utilityMenu.open = false;
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && utilityMenu.open) {
      utilityMenu.open = false; utilityMenu.querySelector('summary').focus();
    }
  });
}


/* A quiet identity journal, independent of the main gallery date rail. */
async function openGrowth(entity) {
  const tab=state.collection.tab,epoch=beginCollection(tab),lib=state.lib;
  $('#timeline').innerHTML='<section class="collection-page" id="growthPage"><p role="status">正在整理成长轨迹…</p></section>';
  try {
    const data=await api('/api/vision/album',{lib,ids:entity,growth:1},browseController.signal);
    if(epoch!==state.viewEpoch||lib!==state.lib)return;
    const years=[...new Set(data.stages.map(s=>s.period.slice(0,4)))];
    $('#growthPage').innerHTML=`<button class="album-back" data-collection="${tab}">← 返回相册</button><div class="collection-heading"><div><p class="collection-eyebrow">一起长大的日子</p><h1>${esc(data.title)} · 成长轨迹</h1><p>${data.total} 张已归档照片 · 在不同的时光里，看看熟悉的模样</p></div></div><div class="growth-switch"><button data-album="${esc(entity)}">全部照片</button><button aria-pressed="true">成长轨迹</button></div><nav class="growth-years" aria-label="年份导航">${years.map(y=>`<a href="#growth-${y}">${y==='unkn'?'日期未知':y}</a>`).join('')}</nav><div class="growth-journal">${data.stages.map((stage,i)=>{const year=stage.period.slice(0,4),label=stage.period==='unknown'?'日期未知':`${year} 年 ${+stage.period.slice(5)} 月`;return `<article class="growth-stage" id="growth-stage-${stage.period}">${i===0||data.stages[i-1].period.slice(0,4)!==year?`<h2 id="growth-${year}">${year==='unkn'?'日期未知':year}</h2>`:''}<div class="growth-stage-body"><div class="growth-caption"><p class="collection-eyebrow">${esc(label)}</p><h3>${stage.count===1?'留下的一个瞬间':'这一段陪伴'}</h3><p>${stage.count} 张照片</p><button data-growth-open="${stage.period}" data-growth-entity="${esc(entity)}">展开这个时期 →</button><button data-growth-picker="${stage.period}" data-growth-entity="${esc(entity)}">更换封面</button></div><div class="growth-pictures"><button class="growth-hero" data-growth-open="${stage.period}" data-growth-entity="${esc(entity)}" aria-label="展开${esc(label)}的照片">${collectionCover(stage.cover)}</button><div class="growth-previews">${stage.previews.filter(p=>p.p!==stage.cover.p).map(p=>collectionCover(p)).join('')}</div></div></div><div class="growth-picker" hidden></div></article>`}).join('')}</div>${data.stages.length?'':'<p class="collection-empty">确认照片后，成长轨迹会从这里开始。</p>'}`;
    observeCollectionCovers();
  } catch(error) {if(epoch===state.viewEpoch&&error.name!=='AbortError')$('#growthPage').innerHTML='<p>成长轨迹暂时无法读取，请返回相册重试。</p><button data-collection="'+tab+'">返回相册</button>';}
}
async function growthPicker(button,offset=0) {
  const stage=button.closest('.growth-stage'),picker=stage.querySelector('.growth-picker'),entity=button.dataset.growthEntity,period=button.dataset.growthPicker,epoch=state.viewEpoch;
  picker.hidden=false;if(!offset)picker.innerHTML='<p role="status">正在读取这个时期的照片…</p>';
  try {
    const page=await api('/api/vision/album',{lib:state.lib,ids:entity,ym:period,off:offset,lim:24},browseController.signal);
    if(epoch!==state.viewEpoch)return;
    if(!offset)picker.innerHTML='<p>选择一张作为这个时期的封面</p><div class="growth-cover-options"></div><p role="status" class="growth-save-status"></p><button class="growth-picker-close">收起</button>';
    picker.querySelector('.growth-cover-options').insertAdjacentHTML('beforeend',page.items.map(item=>`<button data-growth-save="${esc(item.p)}" data-growth-entity="${esc(entity)}" data-growth-period="${period}" aria-label="设为时期封面">${collectionCover(item)}</button>`).join(''));
    picker.querySelector('.growth-more')?.remove();
    if(offset+page.items.length<page.total){const more=document.createElement('button');more.className='growth-more';more.textContent='更多照片';more.onclick=()=>{more.disabled=true;growthPicker(button,offset+page.items.length)};picker.appendChild(more)}
    observeCollectionCovers();
  } catch(error){if(epoch===state.viewEpoch)picker.innerHTML='<p>照片暂时无法读取，请收起后重试。</p>';}
}
document.addEventListener('click',async event=>{
  const growth=event.target.closest('[data-growth]');if(growth){openGrowth(growth.dataset.growth);return}
  const open=event.target.closest('[data-growth-open]');if(open){openAlbum([open.dataset.growthEntity],{ym:open.dataset.growthOpen});return}
  const picker=event.target.closest('[data-growth-picker]');if(picker){growthPicker(picker);return}
  const close=event.target.closest('.growth-picker-close');if(close){close.closest('.growth-picker').hidden=true;return}
  const save=event.target.closest('[data-growth-save]');if(!save)return;
  const epoch=state.viewEpoch,lib=state.lib,status=save.closest('.growth-picker').querySelector('.growth-save-status');save.disabled=true;status.textContent='正在保存封面…';
  try{await apiPost('/api/vision/growth/cover',{lib,entity:save.dataset.growthEntity,period:save.dataset.growthPeriod,rel:save.dataset.growthSave});if(epoch!==state.viewEpoch)return;await openGrowth(save.dataset.growthEntity);document.getElementById('growth-stage-'+save.dataset.growthPeriod)?.scrollIntoView({block:'start'})}
  catch(error){if(epoch===state.viewEpoch){status.textContent='封面保存失败，请重试。';save.disabled=false}}
});
