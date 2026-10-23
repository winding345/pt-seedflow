// 纯前端：拉 JSON → 渲染。没有数据就说明服务有问题，不做兜底。
// 本地调试：index.html?mock=../tests/fixtures/status.sample.json
const MOCK = new URLSearchParams(location.search).get('mock');
const ENDPOINT = MOCK || 'api/status';
const REFRESH_MS = 10000;

const app = document.getElementById('app');
let services = [];
let lastError = null;
let filter = 'all';
let detailName = null;

// ---------- 工具 ----------
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function bytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return n.toFixed(n >= 100 ? 0 : n >= 10 ? 1 : 2) + ' ' + units[i];
}

function speed(n) {
  return bytes(n) + '/s';
}

function ts(sec) {
  if (!sec) return '-';
  const d = new Date(sec * 1000);
  const p = x => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
    + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function mins(sec) {
  if (sec <= 0) return '即将';
  return Math.floor(sec / 60) + ' 分钟后';
}

function pct(v) { return (Number(v) || 0).toFixed(1) + '%'; }

function fetchT(url, opts) {
  opts = opts || {};
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 5000);
  return fetch(url, Object.assign({ cache: 'no-store', signal: ctrl.signal }, opts))
    .finally(() => clearTimeout(timer));
}

// ---------- 列表页 ----------
function kvItem(k, v) {
  return '<div class="item"><span>' + esc(k) + '</span><b>' + esc(v) + '</b></div>';
}

function cardHtml(s) {
  const c = s.config || {};
  const st = s.stats || {};
  const diskPct = Math.min(100, (st.used_gb / c.disk_gb) * 100).toFixed(1);
  const dlPct = Math.min(100, (st.dl_used / st.dl_cap) * 100).toFixed(0);

  const kv = [
    ['磁盘配额', c.disk_gb + ' GB'],
    ['刷流分类', (c.categories || []).length ? c.categories.join(', ') : '不限'],
    ['促销类型', (c.discounts || []).length ? c.discounts.join(', ') : '全部非 NORMAL'],
    ['体积范围', c.size_mb[0] + ' ~ ' + c.size_mb[1] + ' MB'],
    ['删除阈值', c.delete_threshold_mb + ' MB / ' + c.window_minutes + ' 分钟'],
    ['豁免时间', c.exempt_minutes + ' 分钟'],
    ['最短保留', c.min_keep_minutes + ' 分钟'],
    ['并发上限', c.max_torrents + ' 个'],
    ['单轮上限', c.add_per_tick + ' 个'],
    ['qB 分类', c.category],
    ['保存路径', c.save_path || 'qB 默认'],
    ['站点配额', 'search ' + c.limits.search_per_day + ' / dl ' + c.limits.dl_per_day
      + '（安全系数 ' + c.limits.safety + '）'],
  ].map(pair => kvItem(pair[0], pair[1])).join('');

  const acts = (s.actions || []).slice(0, 3).map(a =>
    '<div class="act ' + esc(a.kind) + '"><span class="k">' + esc(a.kind) + '</span><span>'
    + esc(a.name).slice(0, 34) + ' <span style="color:#95a5a6">' + esc(a.detail) + '</span></span></div>'
  ).join('') || '<div class="act"><span class="k">-</span><span>暂无动作</span></div>';

  return '<div class="card" data-svc="' + esc(s.name) + '">'
    + '<div class="card-head"><h2>' + esc(s.name) + '</h2><div class="tags">'
    + '<span class="tag">' + esc(s.site) + '</span>'
    + '<span class="tag ' + (c.dry_run ? 'dry' : 'live') + '">' + (c.dry_run ? '演练' : '运行') + '</span>'
    + '</div></div>'

    + '<div class="section-title">容量</div>'
    + '<div class="bar"><i style="width:' + diskPct + '%"></i></div>'
    + '<div class="bar-note"><span>' + st.used_gb + ' / ' + c.disk_gb + ' GB</span>'
    + '<span>' + st.torrents + ' 个任务 · 候选 ' + st.candidates + '</span></div>'

    + '<div class="section-title">配额</div>'
    + '<div class="bar purple"><i style="width:' + dlPct + '%"></i></div>'
    + '<div class="bar-note"><span>下载 ' + st.dl_used + ' / ' + st.dl_cap + '</span>'
    + '<span>搜索 ' + st.search_used + ' / ' + st.search_cap
    + ' · 下次 ' + mins(st.next_search_seconds) + '</span></div>'

    + '<div class="section-title">流量</div>'
    + '<div class="stats">'
    + '<div class="stat"><div class="v">' + bytes(st.current_downloaded) + '</div><div class="k">当前已下载</div></div>'
    + '<div class="stat"><div class="v">' + bytes(st.current_uploaded) + '</div><div class="k">当前已上传</div></div>'
    + '<div class="stat"><div class="v">' + bytes(st.since_launch_downloaded) + '</div><div class="k">上线以来下载</div></div>'
    + '<div class="stat"><div class="v">' + bytes(st.since_launch_uploaded) + '</div><div class="k">上线以来上传</div></div>'
    + '<div class="stat"><div class="v">' + st.added_total + ' / ' + st.removed_total + '</div><div class="k">累计新增 / 移除</div></div>'
    + '<div class="stat"><div class="v">' + (st.quota_exhausted ? '已耗尽' : '正常') + '</div><div class="k">配额状态</div></div>'
    + '</div>'

    + '<div class="section-title">配置</div>'
    + '<div class="kv">' + kv + '</div>'

    + '<div class="actions-log">' + acts + '</div>'
    + (s.last_error ? '<div class="error-box" style="margin-top:12px">' + esc(s.last_error) + '</div>' : '')
    + '<div class="card-foot">'
    + '<button data-detail="' + esc(s.name) + '">查看任务</button>'
    + '<button class="ghost" data-search="' + esc(s.name) + '">手动 search</button>'
    + '<button class="ghost" data-act="add" data-svc="' + esc(s.name) + '">手动加种</button>'
    + '<button class="ghost" data-act="delete" data-svc="' + esc(s.name) + '">手动删种</button>'
    + '</div></div>';
}

function renderList(data) {
  document.querySelector('.wrap').classList.remove('wide');
  const total = data.services.length;
  app.innerHTML =
    '<div class="top"><h1>pt-seedflow</h1>'
    + '<span class="meta">运行 ' + esc(data.uptime_human) + ' · ' + total + ' 个服务 · '
    + new Date().toLocaleTimeString() + '</span></div>'
    + '<div class="sub">点击卡片查看任务明细</div>'
    + (lastError ? '<div class="error-box">刷新失败：' + esc(lastError)
        + '<button id="retry">重试</button></div>' : '')
    + '<div class="grid">' + data.services.map(cardHtml).join('') + '</div>';
}

// ---------- 详情页 ----------
let chartDays = 1;
let chartBucket = 'hour';
let chartData = null;

function chartHtml() {
  const rangeBtn = d =>
    '<button data-days="' + d + '" class="' + (chartDays === d ? 'active' : '') + '">' + d + ' 天</button>';
  const bucketBtn = (b, label, disabled) =>
    '<button data-bucket="' + b + '" class="' + (chartBucket === b ? 'active' : '') + '"'
    + (disabled ? ' disabled' : '') + '>' + label + '</button>';
  return '<div class="chart-box">'
    + '<div class="chart-head"><span class="chart-title">流量趋势</span>'
    + '<div class="filters">' + [1, 3, 7, 30].map(rangeBtn).join('') + '</div>'
    + '<div class="filters">' + bucketBtn('hour', '按小时', chartDays > 3)
    + bucketBtn('day', '按天', false) + '</div></div>'
    + '<canvas id="traffic" height="240"></canvas>'
    + '<div class="legend"><span class="dot dl"></span>下载 <span class="dot ul"></span>上传</div>'
    + '</div>';
}

function niceMax(value) {
  if (value <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  const norm = value / exp;
  const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return step * exp;
}

function drawChart() {
  const canvas = document.getElementById('traffic');
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.parentElement.clientWidth - 32;
  const cssHeight = 240;
  canvas.width = cssWidth * dpr;
  canvas.height = cssHeight * dpr;
  canvas.style.width = cssWidth + 'px';
  canvas.style.height = cssHeight + 'px';

  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const points = (chartData && chartData.points) || [];
  const padLeft = 64, padRight = 12, padTop = 12, padBottom = 34;
  const plotW = Math.max(10, cssWidth - padLeft - padRight);
  const plotH = cssHeight - padTop - padBottom;

  ctx.font = '11px -apple-system, "Segoe UI", sans-serif';
  ctx.strokeStyle = '#e9ecef';
  ctx.fillStyle = '#7f8c8d';

  if (!points.length) {
    ctx.textAlign = 'center';
    ctx.fillText('暂无流量数据（服务刚上线或还没有采样）', cssWidth / 2, cssHeight / 2);
    return;
  }

  const maxVal = niceMax(Math.max(...points.map(p => Math.max(p.downloaded, p.uploaded))));
  const ticks = 4;
  ctx.textAlign = 'right';
  for (let i = 0; i <= ticks; i++) {
    const y = padTop + plotH - (plotH * i) / ticks;
    const value = (maxVal * i) / ticks;
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(padLeft + plotW, y);
    ctx.stroke();
    ctx.fillText(bytes(value), padLeft - 8, y + 4);
  }

  const slot = plotW / points.length;
  const barW = Math.max(2, Math.min(18, slot / 2.6));
  const d = new Date();

  points.forEach((point, index) => {
    const cx = padLeft + slot * (index + 0.5);
    const hDl = (point.downloaded / maxVal) * plotH;
    const hUl = (point.uploaded / maxVal) * plotH;
    ctx.fillStyle = '#3498db';
    ctx.fillRect(cx - barW - 1, padTop + plotH - hDl, barW, hDl);
    ctx.fillStyle = '#27ae60';
    ctx.fillRect(cx + 1, padTop + plotH - hUl, barW, hUl);

    const every = Math.ceil(points.length / 8);
    if (index % every === 0) {
      d.setTime(point.ts * 1000);
      const label = chartBucket === 'day'
        ? (d.getMonth() + 1) + '-' + d.getDate()
        : String(d.getHours()).padStart(2, '0') + ':00';
      ctx.fillStyle = '#7f8c8d';
      ctx.textAlign = 'center';
      ctx.fillText(label, cx, cssHeight - 12);
    }
  });
}

async function loadChart(name) {
  try {
    const resp = await fetchT('api/services/' + encodeURIComponent(name)
      + '/traffic?days=' + chartDays + '&bucket=' + chartBucket);
    if (resp.ok) chartData = await resp.json();
  } catch (err) {
    chartData = null;
  }
  drawChart();
}

const COLUMNS = [
  ['状态', row => '<span class="pill ' + (row.state === 'removed' ? 'off' : 'on') + '">'
      + (row.state === 'removed' ? '已删除' : '运行中') + '</span>', 'left'],
  ['名称', row => esc(row.name), 'left'],
  ['预定大小', row => bytes(row.size)],
  ['已下载', row => bytes(row.downloaded)],
  ['已上传', row => bytes(row.uploaded)],
  ['近 1h 上传', row => bytes(row.last_hour_upload)],
  ['上传/下载比', row => (row.ratio || 0).toFixed(2)],
  ['已下载比例', row => pct(row.progress)],
  ['做种', row => row.seeds],
  ['下载', row => row.leechs],
  ['下载速度', row => speed(row.dlspeed)],
  ['上传速度', row => speed(row.upspeed)],
  ['发布时间', row => ts(row.published_at)],
  ['添加时间', row => ts(row.added_on)],
  ['最近活跃', row => ts(row.last_activity)],
  ['促销结束', row => ts(row.discount_end)],
];

function tableHtml(rows) {
  if (!rows.length) {
    return '<div class="table-container"><div class="empty">暂无任务</div></div>';
  }
  const head = COLUMNS.map(c => '<th>' + esc(c[0]) + '</th>').join('');
  const body = rows.map(row => '<tr>' + COLUMNS.map((c, i) =>
    '<td' + (i === 1 ? ' class="name" title="' + esc(row.name) + '"' : '') + '>'
    + c[1](row) + '</td>').join('') + '</tr>').join('');
  return '<div class="table-container"><div class="table-wrapper">'
    + '<table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>'
    + '</div></div>';
}

function renderDetail(data, rows) {
  document.querySelector('.wrap').classList.add('wide');
  const svc = data.services.find(s => s.name === detailName);
  if (!svc) {
    location.hash = '';
    return;
  }
  const st = svc.stats || {};
  const all = (rows.active || []).concat(rows.removed || []);
  const shown = filter === 'all' ? all
    : filter === 'active' ? (rows.active || [])
    : (rows.removed || []);

  const summary = [
    ['当前任务', st.torrents + ' / ' + svc.config.max_torrents],
    ['占用空间', st.used_gb + ' / ' + svc.config.disk_gb + ' GB'],
    ['当前已下载', bytes(st.current_downloaded)],
    ['当前已上传', bytes(st.current_uploaded)],
    ['上线以来下载', bytes(st.since_launch_downloaded)],
    ['上线以来上传', bytes(st.since_launch_uploaded)],
    ['累计新增 / 移除', st.added_total + ' / ' + st.removed_total],
  ].map(p => '<div class="stat"><div class="v">' + esc(p[1]) + '</div><div class="k">'
    + esc(p[0]) + '</div></div>').join('');

  app.innerHTML =
    '<div class="detail-top"><a class="back" href="#">← 返回</a>'
    + '<h1>' + esc(svc.name) + '</h1>'
    + '<span class="tag">' + esc(svc.site) + '</span>'
    + '<span class="tag ' + (svc.config.dry_run ? 'dry' : 'live') + '">'
    + (svc.config.dry_run ? '演练' : '运行') + '</span></div>'
    + (lastError ? '<div class="error-box">刷新失败：' + esc(lastError)
        + '<button id="retry">重试</button></div>' : '')
    + '<div class="summary">' + summary + '</div>'
    + chartHtml()
    + '<div class="toolbar"><div class="filters">'
    + '<button data-filter="all" class="' + (filter === 'all' ? 'active' : '') + '">全部 ' + all.length + '</button>'
    + '<button data-filter="active" class="' + (filter === 'active' ? 'active' : '') + '">运行中 ' + (rows.active || []).length + '</button>'
    + '<button data-filter="removed" class="' + (filter === 'removed' ? 'active' : '') + '">已删除 ' + (rows.removed || []).length + '</button>'
    + '</div><span class="hint">' + new Date().toLocaleTimeString() + ' 更新'
    + ' <button class="ghost" id="refreshDetail">刷新</button></span></div>'
    + tableHtml(shown);
  loadChart(svc.name);
}

// ---------- 数据加载 ----------
async function load() {
  let data;
  try {
    const resp = await fetchT(ENDPOINT);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    data = await resp.json();
    lastError = null;
  } catch (err) {
    lastError = err && err.name === 'AbortError' ? '请求超时' : String(err);
    if (!services.length) {
      app.innerHTML = '<div class="top"><h1>pt-seedflow</h1></div>'
        + '<div class="error-box">无法获取服务状态：' + esc(lastError) + '（服务可能未启动）</div>';
      return;
    }
    data = { services };
  }
  services = data.services;

  if (detailName) {
    let rows = { active: [], removed: [] };
    try {
      const resp = await fetchT('api/services/' + encodeURIComponent(detailName) + '/torrents');
      if (resp.ok) rows = await resp.json();
    } catch (err) {
      lastError = String(err);
    }
    renderDetail(data, rows);
  } else {
    renderList(data);
  }
}

async function search(name) {
  if (!confirm('立即刷新候选池？会消耗一次 search 配额。')) return;
  try {
    const resp = await fetchT('api/services/' + encodeURIComponent(name) + '/search', {method: 'POST'});
    const d = await resp.json();
    if (d.error) alert(d.error);
    else if (!d.ok) alert('本轮未刷新，详情见卡片日志');
  } catch (err) {
    alert('请求失败：' + err);
  }
  load();
}

async function fire(name, action) {
  if (!confirm('确认对 ' + name + ' 执行手动' + (action === 'add' ? '加种' : '删种') + '？')) return;
  try {
    const resp = await fetchT('api/trigger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ service: name, action: action })
    });
    const d = await resp.json();
    if (d.error) alert(d.error);
  } catch (err) {
    alert('请求失败：' + err);
  }
  load();
}

// ---------- 路由与事件 ----------
function route() {
  const m = location.hash.match(/^#\/service\/(.+)$/);
  detailName = m ? decodeURIComponent(m[1]) : null;
  filter = 'all';
  load();
}

document.addEventListener('click', function (e) {
  const detailBtn = e.target.closest('[data-detail]');
  if (detailBtn) {
    location.hash = '#/service/' + encodeURIComponent(detailBtn.getAttribute('data-detail'));
    return;
  }
  const searchBtn = e.target.closest('button[data-search]');
  if (searchBtn) {
    e.stopPropagation();
    search(searchBtn.getAttribute('data-search'));
    return;
  }
  const actBtn = e.target.closest('button[data-act]');
  if (actBtn) {
    e.stopPropagation();
    fire(actBtn.getAttribute('data-svc'), actBtn.getAttribute('data-act'));
    return;
  }
  const daysBtn = e.target.closest('[data-days]');
  if (daysBtn) {
    chartDays = Number(daysBtn.getAttribute('data-days'));
    if (chartDays > 3) chartBucket = 'day';
    load();
    return;
  }
  const bucketBtn = e.target.closest('[data-bucket]');
  if (bucketBtn && !bucketBtn.disabled) {
    chartBucket = bucketBtn.getAttribute('data-bucket');
    load();
    return;
  }
  const filterBtn = e.target.closest('[data-filter]');
  if (filterBtn) {
    filter = filterBtn.getAttribute('data-filter');
    load();
    return;
  }
  if (e.target.id === 'retry' || e.target.id === 'refreshDetail') {
    load();
    return;
  }
  const card = e.target.closest('.card');
  if (card) {
    location.hash = '#/service/' + encodeURIComponent(card.getAttribute('data-svc'));
  }
});

window.addEventListener('hashchange', route);
route();
setInterval(function () {
  if (!detailName) load();      // 详情页不自动刷新，避免打断阅读
}, REFRESH_MS);
