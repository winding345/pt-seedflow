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
let editingName = null;

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
    + '<button class="ghost" data-config="' + esc(s.name) + '">编辑配置</button>'
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
let hoverIndex = -1;
let chartVisible = { downloaded: true, uploaded: true };
let sortColumn = -1;      // -1 表示不排序
let sortDirection = 'none';   // none → asc → desc → none

// 恢复上次的显隐选择（两条都关掉则忽略）
try {
  const saved = JSON.parse(localStorage.getItem('ptsf.chartVisible') || 'null');
  if (saved && (saved.downloaded || saved.uploaded)) chartVisible = saved;
} catch (e) {}

function toggleSeries(key) {
  // 不允许两条都关掉，否则图是空的
  const next = { ...chartVisible, [key]: !chartVisible[key] };
  if (!next.downloaded && !next.uploaded) return;
  chartVisible = next;
  try { localStorage.setItem('ptsf.chartVisible', JSON.stringify(chartVisible)); } catch (e) {}
  drawChart();
  syncLegend();
}

function syncLegend() {
  document.querySelectorAll('.legend button[data-series]').forEach(function (btn) {
    const key = btn.getAttribute('data-series');
    btn.classList.toggle('on', !!chartVisible[key]);
    btn.classList.toggle('off', !chartVisible[key]);
  });
}

function seriesPoints(point) {
  const list = [];
  if (chartVisible.downloaded) list.push([point.downloaded, '#3498db']);
  if (chartVisible.uploaded) list.push([point.uploaded, '#27ae60']);
  return list;
}

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
    + '<div class="legend">'
    + '<button class="lg ' + (chartVisible.downloaded ? 'on' : 'off') + '" data-series="downloaded">'
    + '<span class="dot dl"></span>下载</button>'
    + '<button class="lg ' + (chartVisible.uploaded ? 'on' : 'off') + '" data-series="uploaded">'
    + '<span class="dot ul"></span>上传</button>'
    + '</div>'
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

  if (!points.length) {
    ctx.fillStyle = '#7f8c8d';
    ctx.textAlign = 'center';
    ctx.fillText('暂无流量数据（服务刚上线或还没有采样）', cssWidth / 2, cssHeight / 2);
    return;
  }

  const visible = points.map(function (p) {
    const vals = [];
    if (chartVisible.downloaded) vals.push(p.downloaded);
    if (chartVisible.uploaded) vals.push(p.uploaded);
    return vals.length ? Math.max.apply(null, vals) : 0;
  });
  const maxVal = niceMax(Math.max.apply(null, visible.concat([0])));
  const ticks = 4;
  ctx.textAlign = 'right';
  for (let i = 0; i <= ticks; i++) {
    const y = padTop + plotH - (plotH * i) / ticks;
    const value = (maxVal * i) / ticks;
    ctx.strokeStyle = '#eef1f4';
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(padLeft + plotW, y);
    ctx.stroke();
    ctx.fillStyle = '#7f8c8d';
    ctx.fillText(bytes(value), padLeft - 8, y + 4);
  }

  const slot = plotW / points.length;
  const xAt = i => padLeft + slot * (i + 0.5);
  const yAt = v => padTop + plotH - (v / maxVal) * plotH;

  function drawSeries(key, color, fill) {
    // 面积
    ctx.beginPath();
    ctx.moveTo(xAt(0), padTop + plotH);
    points.forEach((point, index) => ctx.lineTo(xAt(index), yAt(point[key])));
    ctx.lineTo(xAt(points.length - 1), padTop + plotH);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();

    // 折线
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = xAt(index);
      const y = yAt(point[key]);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.stroke();
  }

  if (chartVisible.downloaded) {
    drawSeries('downloaded', '#3498db', 'rgba(52, 152, 219, 0.12)');
  }
  if (chartVisible.uploaded) {
    drawSeries('uploaded', '#27ae60', 'rgba(39, 174, 96, 0.12)');
  }

  // 点：数据点少的时候画出来，多了会糊
  if (points.length <= 48) {
    points.forEach((point, index) => {
      seriesPoints(point).forEach(function (pair) {
        ctx.beginPath();
        ctx.arc(xAt(index), yAt(pair[0]), 2.5, 0, Math.PI * 2);
        ctx.fillStyle = pair[1];
        ctx.fill();
      });
    });
  }

  // 悬浮指示：竖线 + 高亮两个点
  if (hoverIndex >= 0 && hoverIndex < points.length) {
    const x = xAt(hoverIndex);
    ctx.strokeStyle = 'rgba(44, 62, 80, 0.25)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, padTop);
    ctx.lineTo(x, padTop + plotH);
    ctx.stroke();
    ctx.setLineDash([]);

    seriesPoints(points[hoverIndex]).forEach(function (pair) {
        ctx.beginPath();
        ctx.arc(x, yAt(pair[0]), 4, 0, Math.PI * 2);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = pair[1];
        ctx.stroke();
      });
  }

  // 横轴标签
  const every = Math.ceil(points.length / 8);
  const d = new Date();
  ctx.textAlign = 'center';
  points.forEach((point, index) => {
    if (index % every !== 0) return;
    d.setTime(point.ts * 1000);
    const label = chartBucket === 'day'
      ? (d.getMonth() + 1) + '-' + d.getDate()
      : String(d.getHours()).padStart(2, '0') + ':00';
    ctx.fillStyle = '#7f8c8d';
    ctx.fillText(label, xAt(index), cssHeight - 12);
  });

  bindChartHover(canvas);
}

function bindChartHover(canvas) {
  if (canvas.dataset.hoverReady === '1') return;
  canvas.dataset.hoverReady = '1';

  const tip = document.createElement('div');
  tip.className = 'chart-tip';
  canvas.parentElement.appendChild(tip);

  function indexFromX(x) {
    const points = (chartData && chartData.points) || [];
    if (!points.length) return -1;
    const padLeft = 64, padRight = 12;
    const plotW = Math.max(10, canvas.clientWidth - padLeft - padRight);
    const slot = plotW / points.length;
    const index = Math.round((x - padLeft) / slot - 0.5);
    return Math.max(0, Math.min(points.length - 1, index));
  }

  canvas.addEventListener('mousemove', function (event) {
    const points = (chartData && chartData.points) || [];
    if (!points.length) return;
    const index = indexFromX(event.offsetX);
    if (index !== hoverIndex) {
      hoverIndex = index;
      drawChart();
    }

    const point = points[index];
    const moment = new Date(point.ts * 1000);
    const label = chartBucket === 'day'
      ? (moment.getMonth() + 1) + '-' + moment.getDate()
      : String(moment.getHours()).padStart(2, '0') + ':00';
    tip.innerHTML = '<div class="t-time">' + label + '</div>'
      + (chartVisible.downloaded
          ? '<div><span class="dot dl"></span>下载 ' + bytes(point.downloaded) + '</div>' : '')
      + (chartVisible.uploaded
          ? '<div><span class="dot ul"></span>上传 ' + bytes(point.uploaded) + '</div>' : '');
    tip.style.display = 'block';

    const padLeft = 64, padRight = 12;
    const plotW = Math.max(10, canvas.clientWidth - padLeft - padRight);
    const x = padLeft + (plotW / points.length) * (index + 0.5);
    const boxWidth = canvas.parentElement.clientWidth;
    const tipWidth = 150;
    const left = canvas.offsetLeft + x + 14;
    tip.style.left = (left + tipWidth > boxWidth ? left - tipWidth - 28 : left) + 'px';
    tip.style.top = (canvas.offsetTop + 10) + 'px';
  });

  canvas.addEventListener('mouseleave', function () {
    hoverIndex = -1;
    tip.style.display = 'none';
    drawChart();
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
      + (row.state === 'removed' ? '已删除' : '运行中') + '</span>', 'left',
    row => row.state || ''],
  ['名称', row => esc(row.name), 'left', row => row.name || ''],
  ['预定大小', row => bytes(row.size), 'right', row => row.size || 0],
  ['已下载', row => bytes(row.downloaded), 'right', row => row.downloaded || 0],
  ['已上传', row => bytes(row.uploaded), 'right', row => row.uploaded || 0],
  ['近 1h 上传', row => bytes(row.last_hour_upload), 'right', row => row.last_hour_upload || 0],
  ['上传/下载比', row => (row.ratio || 0).toFixed(2), 'right', row => row.ratio || 0],
  ['已下载比例', row => pct(row.progress), 'right', row => row.progress || 0],
  ['做种', row => row.seeds, 'right', row => row.seeds || 0],
  ['下载', row => row.leechs, 'right', row => row.leechs || 0],
  ['下载速度', row => speed(row.dlspeed), 'right', row => row.dlspeed || 0],
  ['上传速度', row => speed(row.upspeed), 'right', row => row.upspeed || 0],
  ['发布时间', row => ts(row.published_at), 'right', row => row.published_at || 0],
  ['添加时间', row => ts(row.added_on), 'right', row => row.added_on || 0],
  ['最近活跃', row => ts(row.last_activity), 'right', row => row.last_activity || 0],
  ['促销结束', row => ts(row.discount_end), 'right', row => row.discount_end || 0],
];

function sortRows(rows) {
  if (sortColumn < 0 || sortDirection === 'none') return rows;
  const usable = COLUMNS[sortColumn];
  if (!usable || typeof usable[3] !== 'function') return rows;
  const factor = sortDirection === 'asc' ? 1 : -1;
  return rows.slice().sort(function (a, b) {
    const left = usable[3](a);
    const right = usable[3](b);
    if (typeof left === 'number' && typeof right === 'number') {
      return (left - right) * factor;
    }
    return String(left).localeCompare(String(right), 'zh-CN') * factor;
  });
}

function tableHtml(rows) {
  if (!rows.length) {
    return '<div class="table-container"><div class="empty">暂无任务</div></div>';
  }
  const head = COLUMNS.map(function (c, index) {
    let arrow = '';
    if (index === sortColumn && sortDirection !== 'none') {
      arrow = '<span class="arrow">' + (sortDirection === 'asc' ? '↑' : '↓') + '</span>';
    }
    const cls = index === sortColumn && sortDirection !== 'none' ? ' class="sorted"' : '';
    return '<th' + cls + ' data-sort="' + index + '">' + esc(c[0]) + arrow + '</th>';
  }).join('');
  const ordered = sortRows(rows);
  const body = ordered.map(row => '<tr>' + COLUMNS.map((c, i) =>
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

// ---------- 配置编辑 ----------
async function renderEditor(name) {
  let service;
  let site;
  let siteName = '';
  try {
    const resp = await fetchT('api/services/' + encodeURIComponent(name) + '/config');
    service = await resp.json();
    if (service.error) throw new Error(service.error);
    siteName = service.site || '';
    if (siteName) {
      const siteResp = await fetchT('api/sites/' + encodeURIComponent(siteName) + '/config');
      site = await siteResp.json();
      if (site.error) site = null;
    }
  } catch (err) {
    app.innerHTML = '<div class="top"><h1>配置</h1></div>'
      + '<div class="error-box">读取配置失败：' + esc(err) + '</div>';
    return;
  }

  app.innerHTML =
    '<div class="detail-top"><a class="back" href="#/service/' + encodeURIComponent(name) + '">← 返回</a>'
    + '<h1>' + esc(name) + ' 配置</h1></div>'
    + '<div class="hint" style="margin-bottom:10px">保存后立即生效并写入 MySQL；不校验字段，格式错误会提示。</div>'
    + '<h2 class="cfg-title">服务配置</h2>'
    + '<textarea id="cfgService" spellcheck="false">' + esc(JSON.stringify(service, null, 2)) + '</textarea>'
    + (site
        ? '<h2 class="cfg-title">站点配置 <span class="hint">（' + esc(siteName) + '：cookie / list_url 等）</span></h2>'
          + '<textarea id="cfgSite" spellcheck="false">' + esc(JSON.stringify(site, null, 2)) + '</textarea>'
        : '<div class="hint">该站点没有独立配置</div>')
    + '<div class="card-foot"><button id="saveCfg">保存</button>'
    + '<button class="ghost" id="cancelCfg">取消</button></div>';

  document.getElementById('saveCfg').addEventListener('click', async function () {
    let serviceData;
    try {
      serviceData = JSON.parse(document.getElementById('cfgService').value);
    } catch (err) {
      alert('服务配置 JSON 解析失败：' + err.message);
      return;
    }

    try {
      const resp = await fetchT('api/services/' + encodeURIComponent(name) + '/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(serviceData)
      });
      const result = await resp.json();
      if (result.error) { alert(result.error); return; }
    } catch (err) {
      alert('服务配置保存失败：' + err);
      return;
    }

    const siteBox = document.getElementById('cfgSite');
    if (siteBox && siteName) {
      let siteData;
      try {
        siteData = JSON.parse(siteBox.value);
      } catch (err) {
        alert('服务配置已保存；站点配置 JSON 解析失败：' + err.message);
        return;
      }
      try {
        const resp = await fetchT('api/sites/' + encodeURIComponent(siteName) + '/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(siteData)
        });
        const result = await resp.json();
        if (result.error) { alert(result.error); return; }
        if (result.warning) alert(result.warning);
      } catch (err) {
        alert('站点配置保存失败：' + err);
        return;
      }
    }

    alert('已保存并生效');
    location.hash = '#/service/' + encodeURIComponent(name);
  });

  document.getElementById('cancelCfg').addEventListener('click', function () {
    location.hash = '#/service/' + encodeURIComponent(name);
  });
}

// ---------- 数据加载 ----------
async function load() {
  if (editingName) return;      // 编辑页自己管理数据
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
  const edit = location.hash.match(/^#\/service\/([^/]+)\/config$/);
  if (edit) {
    editingName = decodeURIComponent(edit[1]);
    detailName = null;
    renderEditor(editingName);
    return;
  }
  editingName = null;
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
  const configBtn = e.target.closest('button[data-config]');
  if (configBtn) {
    e.stopPropagation();
    location.hash = '#/service/' + encodeURIComponent(configBtn.getAttribute('data-config')) + '/config';
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
  const sortHead = e.target.closest('th[data-sort]');
  if (sortHead) {
    const index = Number(sortHead.getAttribute('data-sort'));
    if (sortColumn !== index) {
      sortColumn = index;
      sortDirection = 'asc';
    } else if (sortDirection === 'asc') {
      sortDirection = 'desc';
    } else if (sortDirection === 'desc') {
      sortDirection = 'none';
      sortColumn = -1;
    } else {
      sortColumn = index;
      sortDirection = 'asc';
    }
    load();
    return;
  }
  const legendBtn = e.target.closest('.legend button[data-series]');
  if (legendBtn) {
    toggleSeries(legendBtn.getAttribute('data-series'));
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
