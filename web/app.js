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

const THUMB_VARIANTS = [160, 320, 480, 640];
const DENSITY_STEPS = [110, 150, 210];      // 单块目标宽度（px）
const TILE_BATCH = 20;                      // 每次追加的日期分组数
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

const state = {
  libs: [], lib: null, tree: null,
  days: [], renderedDayIdx: 0, done: false,
  density: 1, cols: 3,
  search: { active: false, q: '', off: 0, total: 0, done: false },
  items: new Map(),                          // dayKey -> items
  viewer: { list: [], i: 0, scale: 1, tx: 0, ty: 0 },
  years: [], railYear: null, railYm: null,   // 年份 / 月份导航
};

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

function api(path, params = {}) {
  const q = new URLSearchParams(params).toString();
  // 浏览类接口一律匿名访问（服务端也不再要求凭证），URL 保持干净。
  return fetch(path + (q ? '?' + q : ''), { credentials: 'same-origin' })
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
}
function mediaURL(ep, rel, extra = '') {
  return `${ep}?lib=${enc(state.lib)}&p=${enc(rel)}${extra}`;
}
function thumbSrcset(rel) {
  return THUMB_VARIANTS
    .map(s => `${mediaURL('/thumb', rel, '&s=' + s)} ${s}w`).join(', ');
}

let toastTimer = null;
function toast(msg, ms = 2000) {
  const el = $('#toast');
  el.textContent = msg; el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

/* ---------------- 布局 ---------------- */
function computeCols() {
  const w = Math.min(window.innerWidth, 2200);
  const target = DENSITY_STEPS[state.density];
  const cols = Math.max(2, Math.round((w - 16) / target));
  state.cols = cols;
  document.documentElement.style.setProperty('--cols', cols);
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
  syncTopbarHeight();
  let d = null;
  try {
    d = await api('/api/libs');
    state.libs = d.libraries || [];
    if (d.trash && d.trash.retentionDays) trashDays = d.trash.retentionDays;
  } catch (e) {
    splashFail('无法连接服务：' + e.message);
    return;
  }
  try { Object.assign(admin, await api('/api/admin/status')); } catch (e) { /* 浏览不受影响 */ }

  // 首次索引可能还在后台跑：不空等、不误报「没有照片」，而是显示进度面板。
  if (d && (d.ready === false || d.indexing)) {
    idxOpen();
    return;
  }
  const usable = state.libs.filter(l => l.count > 0);
  if (!usable.length) {
    const anyExist = state.libs.some(l => l.exists);
    splashFail(anyExist ? '图库里没有可显示的图片' : '没有找到可用的图库目录，请编辑 libraries.json');
    return;
  }
  buildChips(usable);
  await selectLib(usable[0].id, true);
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

async function selectLib(id, isBoot = false) {
  if (state.lib === id && !isBoot) return;
  state.lib = id;
  state.switching = true;                  // 阻止哨兵在切库期间用旧索引渲染
  $$('.chip').forEach(c => c.classList.toggle('on', c.dataset.lib === id));
  const meta = state.libs.find(l => l.id === id);
  $('#brandName').textContent = meta ? meta.name : '时光轴';
  document.title = (meta ? meta.name : '时光轴') + ' · 时光轴';
  if (meta && meta.stale && !isBoot) toast('该图库目录当前不可访问，显示的是上次索引', 3600);

  state.renderedDayIdx = 0; state.done = false;
  state.items.clear();
  state.search.active = false;
  $('#searchBar').hidden = true;
  $('#timeline').innerHTML = '';
  $('#endTip').hidden = true;
  rail.hidden = true; syncRailHeight();
  hideToTop();
  syncTopbarHeight();

  if (isBoot) $('#splash').classList.add('hide');

  try {
    state.tree = await api('/api/tree', { lib: id });
  } catch (e) {
    state.switching = false;
    renderEmpty('加载失败', '无法读取索引：' + e.message, true);
    return;
  }
  state.days = (state.tree.dayOrder || []).map(k => {
    const [ym, d] = k.split('|');
    return { key: k, ym, d };
  });
  buildMonthSheet();
  resetRail();
  state.switching = false;
  if (!state.days.length) { renderEmpty('暂无图片', '这个图库里还没有可显示的图片。'); return; }
  renderNextBatch();
  requestAnimationFrame(syncRail);
  window.scrollTo(0, 0);
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
  if (state.search.active || state.switching || state.done) return;
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
  const created = [];

  for (let i = from; i < to; i++) {
    const day = state.days[i];
    if (!day) break;
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

const groupObserver = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    groupObserver.unobserve(e.target);
    loadDay(e.target);
  });
}, { rootMargin: '900px 0px' });

function makeDayBlock(day) {
  const el = document.createElement('section');
  el.className = 'day-blk';
  el.dataset.key = day.key; el.dataset.ym = day.ym; el.dataset.d = day.d;
  const total = dayCount(day);
  if (total) el.dataset.total = total;
  // 骨架只放少量节点（省 DOM），真实高度由 sizeSkeletons() 撑出来
  const est = Math.max(2, Math.min(total || 6, 24));
  let sk = '';
  for (let i = 0; i < est; i++) sk += '<div class="tile sk"></div>';
  el.innerHTML =
    `<div class="day-hd"><span>${fmtDayHead(day.ym, day.d)}</span>` +
    `<span class="d-n">${total != null ? total + ' 张' : ''}</span>` +
    `<span class="d-w">${weekdayOf(day.ym, day.d)}</span></div>` +
    `<div class="grid">${sk}</div>`;
  groupObserver.observe(el);
  return el;
}

/* 把骨架网格撑到「真实张数」对应的高度。
   否则骨架（24 个）→ 真图（可能几百个）时分组突然长高，
   会把正在跳转/滚动的位置顶走上万像素。
   高度必须算准：tile 是 aspect-ratio:1，行高 = 单元格宽。 */
function sizeSkeletons(blocks) {
  const cols = state.cols;
  const rootCS = getComputedStyle(document.documentElement);
  const gap = parseFloat(rootCS.getPropertyValue('--gap')) || 4;
  blocks.forEach(el => {
    const g = el.querySelector('.grid');
    const total = +el.dataset.total || 0;
    if (!g || !total || g.dataset.painted === '1') return;
    const cs = getComputedStyle(g);
    const inner = g.clientWidth - (parseFloat(cs.paddingLeft) || 0)
                               - (parseFloat(cs.paddingRight) || 0);
    if (inner <= 0) return;
    const cell = (inner - (cols - 1) * gap) / cols;
    if (cell <= 0) return;
    const rows = Math.ceil(total / cols);
    g.style.minHeight = (rows * cell + (rows - 1) * gap) + 'px';
  });
}
function dayCount(day) {
  for (const m of (state.tree.months || [])) {
    if (m.ym === day.ym) {
      const x = (m.days || []).find(d => d.d === day.d);
      if (x) return x.n;
    }
  }
  return null;
}

async function loadDay(el) {
  const { ym, d, key } = { ym: el.dataset.ym, d: el.dataset.d, key: el.dataset.key };
  const grid = el.querySelector('.grid');
  let items = state.items.get(key);
  if (!items) {
    try {
      const r = await api('/api/items', { lib: state.lib, ym, d, lim: 600 });
      items = r.items || [];
      state.items.set(key, items);
    } catch (e) { grid.innerHTML = '<div class="e-s" style="grid-column:1/-1">加载失败</div>'; return; }
  }
  paintGrid(grid, items, key);
}

function paintGrid(grid, items, ctxKey) {
  if (grid.dataset.painted === '1') return;
  grid.dataset.painted = '1';
  grid.innerHTML = '';                        // 移除骨架
  grid.style.minHeight = '';                  // 真图自带高度，清掉预估
  const sizes = `${(100 / state.cols).toFixed(2)}vw`;
  const frag = document.createDocumentFragment();
  items.forEach((it, i) => {
    const a = document.createElement('a');
    a.className = 'tile';
    a.href = 'javascript:void 0';
    a.dataset.idx = i;
    a.dataset.ctx = ctxKey;
    let badge = '';
    if (it.live) badge = '<span class="badge live"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/></svg>LIVE</span>';
    else if (it.k === 'vid') badge = '<span class="badge"><svg viewBox="0 0 24 24"><path d="M8 6.5l9 5.5-9 5.5z" fill="currentColor" stroke="none"/></svg></span>';
    a.innerHTML = badge + '<span class="pick"></span>' +
      `<img alt="${esc(it.n)}" decoding="async" loading="lazy" ` +
      `srcset="${thumbSrcset(it.p)}" sizes="${sizes}">`;
    const img = a.querySelector('img');
    img.addEventListener('load', () => { a.classList.add('loaded'); fitTile(img); }, { once: true });
    img.addEventListener('error', () => {
      a.classList.add('loaded');
      a.style.background = 'var(--bg-3)';
    }, { once: true });
    frag.appendChild(a);
  });
  grid.appendChild(frag);
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
  const list = key === 'search-results' ? state.searchList : state.items.get(key);
  if (!list) return;
  openViewer(list, +a.dataset.idx);
});

/* 哨兵 + 滚动 */
function nearBottom(margin) {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - margin;
}
new IntersectionObserver(es => {
  if (es.some(x => x.isIntersecting)) renderNextBatch();
}, { rootMargin: '1400px 0px' }).observe($('#sentinel'));

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
  state.search.active = true;
  rail.hidden = true; syncRailHeight();
  state.search.q = q; state.search.off = 0; state.search.done = false;
  state.searchList = [];
  $('#endTip').hidden = true;
  const tl = $('#timeline');
  tl.innerHTML = '<div class="month-sep">搜索结果</div><div class="grid" id="searchGrid"></div>';
  await loadSearchPage();
}

async function loadSearchPage() {
  const grid = $('#searchGrid');
  if (!grid || state.search.done) return;
  try {
    const r = await api('/api/search', { lib: state.lib, q: state.search.q,
      off: state.search.off, lim: 120 });
    state.search.total = r.total;
    state.search.off += r.items.length;
    if (r.items.length < 120) state.search.done = true;
    state.searchList = (state.searchList || []).concat(r.items);
    // 以「当前已加载列表」作为点击上下文，故需重绘索引
    paintSearch(grid, r.items, state.searchList.length - r.items.length);
    $('.month-sep').innerHTML =
      `搜索结果 <span>${r.total.toLocaleString()} 张</span>`;
    if (state.search.done && !r.total) {
      grid.outerHTML = '<div class="empty"><div class="e-t">没有找到匹配的图片</div>' +
        '<div class="e-s">换个关键词试试</div></div>';
    }
  } catch (e) { toast('搜索失败：' + e.message, 2600); }
}

function paintSearch(grid, items, baseIdx) {
  const sizes = `${(100 / state.cols).toFixed(2)}vw`;
  const frag = document.createDocumentFragment();
  items.forEach((it, i) => {
    const a = document.createElement('a');
    a.className = 'tile'; a.href = 'javascript:void 0';
    a.dataset.idx = baseIdx + i;
    a.dataset.ctx = 'search-results';
    a.innerHTML = (it.live ? '<span class="badge live">LIVE</span>' : '') +
      `<img alt="${esc(it.n)}" decoding="async" loading="lazy" ` +
      `srcset="${thumbSrcset(it.p)}" sizes="${sizes}">`;
    const img = a.querySelector('img');
    img.addEventListener('load', () => { a.classList.add('loaded'); fitTile(img); }, { once: true });
    frag.appendChild(a);
  });
  grid.appendChild(frag);
}

function exitSearch() {
  state.search.active = false;
  $('#searchBar').hidden = true;
  $('#searchInput').value = '';
  syncTopbarHeight();
  const tl = $('#timeline');
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
  $('#vDetail').hidden = true;
  const meta = libMeta() || {};
  $('#vDel').hidden = !meta.canManage || !admin.required;   // 能力 + 已配置口令
  $('#vDel').title = !meta.canManage ? (meta.manageReason || '')
    : (!admin.required ? '未配置管理口令，删除功能已关闭' : '移入回收站');
  showSlide(idx, 0);
}
function closeViewer() {
  viewer.hidden = true;
  document.body.style.overflow = '';
  stopMedia();
  vMedia.innerHTML = '';
  state.viewer.scale = 1; state.viewer.tx = 0; state.viewer.ty = 0;
}

function stopMedia() {
  $$('video', vMedia).forEach(v => { try { v.pause(); } catch (e) {} });
  if (state.viewer.lpk) {                    // 清理上一张的 LivePhotosKit 实例
    try { state.viewer.lpk.destroy(); } catch (e) {}
    state.viewer.lpk = null;
  }
}
function currentItem() { return state.viewer.list[state.viewer.i]; }

function showSlide(idx, dir) {
  const list = state.viewer.list;
  if (!list.length) return;
  state.viewer.i = (idx + list.length) % list.length;
  state.viewer.scale = 1; state.viewer.tx = 0; state.viewer.ty = 0;
  applyTransform();
  const it = currentItem();
  stopMedia();
  vMedia.innerHTML = '';
  vMedia.classList.remove('motion');

  const n = state.viewer.i + 1, N = list.length;
  $('#vTitle').textContent = `${n} / ${N}`;
  $('#vCaption').innerHTML =
    `<div>${fmtFull(it.t)}</div><div class="c2">${esc(it.n)} · ${fmtSize(it.z)}</div>`;
  $('#vLive').hidden = !it.live;

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
    v.poster = mediaURL('/thumb', it.p, '&s=640');

    let useTrans = (it.c === 'hevc' || !it.c) ? !canPlayHevc() : false;
    const setSrc = () => { v.src = mediaURL('/video', it.p, useTrans ? '&t=1' : ''); };
    setSrc();
    v.onerror = () => {
      if (!useTrans) {                      // 直出失败 → 兜底转码一次
        useTrans = true; setSrc(); v.play().catch(() => {});
      } else if (!v.dataset.failed) {       // 转码也不行（或体积超限）→ 明确告知
        v.dataset.failed = '1';
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
    low.src = mediaURL('/thumb', it.p, '&s=480');
    const hi = document.createElement('img');
    hi.className = 'v-hi'; hi.alt = esc(it.n);
    hi.onload = () => { hi.classList.add('ready'); wrap.classList.add('done'); };
    hi.onerror = () => { low.style.filter = 'none'; };
    hi.src = mediaURL('/photo', it.p, '&fmt=jpeg');
    wrap.appendChild(low); wrap.appendChild(hi);
    vMedia.appendChild(wrap);

    if (it.live) attachLive(it);
    preloadNeighbors();
  }
  setChromeVisible(true);
}

/* ---- Live Photo 播放 ----
   iOS Safari → Apple 原生 LivePhotosKit（长按有"实况"动效 + 防抖对齐）
   其它浏览器 → 静图 + H.264 动图视频叠加（无 CDN 依赖，离线可用）
   注：photoSrc 统一用服务端转码的 JPEG —— 原始 HEIC 在非 Safari 上解不出，
       这是原型阶段已经踩过的坑。 */
function attachLive(it) {
  if (IS_IOS) {
    ensureLPK().then(ok => {
      if (!ok || currentItem() !== it) return fallbackMotion(it);
      try {
        const host = document.createElement('div');
        host.style.cssText =
          'position:absolute;inset:0;margin:auto;width:100%;height:100%';
        vMedia.appendChild(host);
        const canHevc = (() => {
          try { return !!document.createElement('video').canPlayType('video/mp4; codecs="hvc1"'); }
          catch (e) { return false; }
        })();
        const pl = window.LivePhotosKit.Player(host);
        pl.photoSrc = mediaURL('/photo', it.p, '&fmt=jpeg');
        pl.videoSrc = mediaURL('/video', it.mv || it.p, canHevc ? '' : '&t=1');
        pl.play();
        state.viewer.lpk = pl;
      } catch (e) { fallbackMotion(it); }
    });
  } else {
    fallbackMotion(it);
  }
}

function fallbackMotion(it) {
  const v = document.createElement('video');
  v.className = 'v-motion';
  v.muted = true; v.loop = true; v.playsInline = true;
  v.autoplay = true; v.preload = 'auto';
  v.setAttribute('playsinline', '');
  v.setAttribute('webkit-playsinline', '');
  // 桌面 / Android：浏览器普遍不认 HEVC → 走 H.264 转码
  v.src = mediaURL('/video', it.mv || it.p, '&t=1');
  vMedia.appendChild(v);
  v.play().then(() => {
    vMedia.classList.add('motion');
    $('#vLive').hidden = false;
  }).catch(() => {
    // 自动播放被拒时，保留 LIVE 徽标，用户点一下即可
    $('#vLive').hidden = false;
  });
}

$('#vLive').onclick = () => {
  const it = currentItem();
  if (!it || !it.live) return;
  const on = vMedia.classList.toggle('motion');
  const v = vMedia.querySelector('video.v-motion');
  if (v) { on ? v.play().catch(() => {}) : v.pause(); }
  $('#vLive').classList.toggle('offline', !on);
  toast(on ? '实况已播放' : '已暂停实况', 1200);
};

function preloadNeighbors() {
  const list = state.viewer.list;
  [1, -1].forEach(off => {
    const it = list[(state.viewer.i + off + list.length) % list.length];
    if (!it || it.k === 'vid') return;
    const im = new Image();
    im.src = mediaURL('/photo', it.p, '&fmt=jpeg');
  });
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
    ['路径', `<span class="mono">${esc(it.p)}</span>`],
  ];
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
  const seps = $$('#timeline .month-sep[data-ym]');
  if (!seps.length) return;
  const off = topbarOffset() + 8;
  let cur = seps[0].dataset.ym;
  for (const s of seps) {
    if (s.getBoundingClientRect().top <= off) cur = s.dataset.ym; else break;
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
    const el = $(`.month-sep[data-ym="${ym}"]`);
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
    const r = await api('/api/refresh', { lib: 'all' });
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
      const p = await api('/api/cache/gc');
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
    const r = await api('/api/cache/gc', { apply: 1 });
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

$('#btnDensity').onclick = () => {
  state.density = (state.density + 1) % DENSITY_STEPS.length;
  computeCols();
  // 重绘已加载网格的 sizes
  $$('.grid img').forEach(img => { img.sizes = `${(100 / state.cols).toFixed(2)}vw`; });
  toast(['大图', '标准', '紧凑'][2 - state.density] + '视图', 1200);
};

$('#btnInfo').onclick = async () => {
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
const sel = { active: false, map: new Map() };     // rel -> item

function tileItemOf(a) {
  const key = a.dataset.ctx;
  const list = key === 'search-results' ? state.searchList : state.items.get(key);
  if (!list) return null;
  return list[+a.dataset.idx] || null;
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
}
function toggleSelectMode(on) {
  sel.active = !!on;
  document.body.classList.toggle('selecting', sel.active);
  $('#btnSelect').classList.toggle('on', sel.active);
  $('#selBar').hidden = !sel.active;
  if (!sel.active) {
    sel.map.clear();
    $$('.tile.sel').forEach(t => t.classList.remove('sel'));
  }
  syncSelBar();
}
$('#btnSelect').onclick = () => {
  if (state.search.active) exitSearch();
  toggleSelectMode(!sel.active);
};
$('#selCancel').onclick = () => toggleSelectMode(false);

/* ---------------- 刷新（删除/恢复后）---------------- */
async function refreshAfterChange() {
  try {
    const d = await api('/api/libs');
    state.libs = d.libraries || [];
    if (d.trash && d.trash.retentionDays) trashDays = d.trash.retentionDays;
    buildChips(state.libs.filter(l => l.count > 0));
  } catch (e) { /* 刷新失败不阻塞主流程 */ }
  const cur = state.lib;
  state.lib = null;                       // 强制重建时间轴
  await selectLib(cur, true);
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
  return `/api/trash/thumb?item=${enc(itemId)}&s=${s || 320}`;
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
        `<img loading="lazy" alt="" src="${trashThumbURL(first.itemId, 240)}">` +
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
    computeCols(); syncTopbarHeight();
    sizeSkeletons($$('.day-blk'));            // 列数变了，重算骨架高度
    syncRail();
  }, 120);
}, { passive: true });
if (window.ResizeObserver) {
  new ResizeObserver(syncTopbarHeight).observe($('#topbar'));
  new ResizeObserver(syncRailHeight).observe($('#rail'));
}

/* 启动 */
boot();
