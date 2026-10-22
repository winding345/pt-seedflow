"""Web 面板：一页展示各服务状态。"""
from __future__ import annotations

import time
from typing import List

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .scheduler import ServiceRunner

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>pt-seedflow</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { margin: 0; padding: 24px; background: #16181d; color: #e6e6e6;
         font: 14px/1.6 -apple-system, "Segoe UI", "PingFang SC", sans-serif; }
  h1 { font-size: 18px; margin: 0 0 4px; font-weight: 600; }
  .sub { color: #8b8f98; font-size: 12px; margin-bottom: 20px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); }
  .card { background: #1e2127; border: 1px solid #2b2f38; border-radius: 10px; padding: 16px; }
  .card h2 { font-size: 15px; margin: 0 0 10px; display: flex; justify-content: space-between; align-items: center; }
  .tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #2b2f38; color: #9aa0aa; }
  .tag.live { background: #1c3b2a; color: #6ee7a8; }
  .tag.dry { background: #3b3520; color: #e7c96e; }
  .row { display: flex; justify-content: space-between; color: #b9bec7; font-size: 13px; padding: 3px 0; }
  .row b { color: #e6e6e6; font-weight: 500; }
  .bar { height: 6px; background: #2b2f38; border-radius: 3px; overflow: hidden; margin: 8px 0 12px; }
  .bar > i { display: block; height: 100%; background: #4c8dff; }
  .acts { margin-top: 12px; border-top: 1px solid #2b2f38; padding-top: 10px; }
  .act { font-size: 12px; display: flex; gap: 8px; padding: 2px 0; color: #9aa0aa; }
  .act .k { width: 74px; color: #6b7280; flex: none; }
  .act.add .k { color: #6ee7a8; }
  .act.delete .k, .act.would-delete .k { color: #f87171; }
  .act.would-add .k { color: #e7c96e; }
  .act.error .k { color: #f87171; }
  .err { color: #f87171; font-size: 12px; margin-top: 6px; }
  button { background:#2b2f38; color:#d7dae0; border:1px solid #3a3f4a; border-radius:6px;
           padding:5px 10px; font-size:12px; cursor:pointer; margin-right:6px; }
  button:hover { background:#343a45; }
</style>
</head>
<body>
<h1>pt-seedflow</h1>
<div class="sub" id="meta">加载中…</div>
<div class="grid" id="grid"></div>
<script>
async function fire(name, action){
  if(!confirm('确认对 ' + name + ' 执行手动' + (action==='add'?'加种':'删种') + '？')) return;
  let r;
  try {
    r = await fetchT('api/trigger', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({service:name, action:action})
    });
  } catch(e) {
    alert('请求超时或失败：' + e);
    return;
  }
  const d = await r.json().catch(()=>({error:'响应解析失败'}));
  if(d.error) alert(d.error);
  refresh();
}
function fmt(sec){ if(sec<=0) return "即将"; var m=Math.floor(sec/60); return m+" 分钟后"; }
function esc(s){ return String(s).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
async function fetchT(url, opts){
  // 5 秒超时：容器重启时旧请求会被切断，没有超时就会永远停在"加载中"
  opts = opts || {};
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 5000);
  try {
    return await fetch(url, Object.assign({cache:'no-store', signal: ctrl.signal}, opts));
  } finally {
    clearTimeout(timer);
  }
}
let fails = 0;
async function refresh(){
  try {
    const r = await fetchT('api/status');
    const d = await r.json();
    document.getElementById('meta').textContent =
      '运行 ' + d.uptime_human + ' · 共 ' + d.services.length + ' 个服务 · ' + new Date().toLocaleTimeString();
    document.getElementById('grid').innerHTML = d.services.map(s => {
      const pct = Math.min(100, s.used_gb / s.disk_gb * 100).toFixed(1);
      const dlPct = Math.min(100, s.quota.dl_used / s.quota.dl_cap * 100).toFixed(0);
      const acts = s.actions.slice(0, 6).map(a =>
        '<div class="act ' + a.kind + '"><span class="k">' + a.kind + '</span><span>'
        + esc(a.name).slice(0, 46) + ' <span style="color:#6b7280">' + esc(a.detail) + '</span></span></div>'
      ).join('') || '<div class="act"><span class="k">-</span><span>暂无动作</span></div>';
      return '<div class="card">'
        + '<h2>' + esc(s.name)
        + '<span class="tag ' + (s.dry_run ? 'dry' : 'live') + '">' + (s.dry_run ? '演练' : '运行') + '</span></h2>'
        + '<div class="row"><span>磁盘</span><b>' + s.used_gb + ' / ' + s.disk_gb + ' GB</b></div>'
        + '<div class="bar"><i style="width:' + pct + '%"></i></div>'
        + '<div class="row"><span>任务</span><b>' + s.torrents + ' / ' + s.max_torrents + '</b></div>'
        + '<div class="row"><span>候选池</span><b>' + s.candidates + '</b></div>'
        + '<div class="row"><span>下载配额</span><b>' + s.quota.dl_used + ' / ' + s.quota.dl_cap + '</b></div>'
        + '<div class="bar"><i style="width:' + dlPct + '%;background:#8b5cf6"></i></div>'
        + '<div class="row"><span>下次 search</span><b>' + fmt(s.next_search_seconds) + '</b></div>'
        + '<div class="row"><span>累计 加/删</span><b>' + s.added_total + ' / ' + s.deleted_total + '</b></div>'
        + (s.last_error ? '<div class="err">' + esc(s.last_error) + '</div>' : '')
        + '<div class="acts">' + acts + '</div>'
        + '<div class="acts"><button onclick="fire(\'' + esc(s.name) + '\',\'add\')">手动加种</button>'
        + '<button onclick="fire(\'' + esc(s.name) + '\',\'delete\')">手动删种</button></div>'
        + '</div>';
    }).join('');
    fails = 0;
  } catch (e) {
    fails++;
    document.getElementById('meta').textContent =
      '无法获取状态（' + (e && e.name === 'AbortError' ? '请求超时' : e) + '），第 ' +
      fails + ' 次，' + (fails >= 3 ? '正在自动刷新…' : '稍后自动重试…');
    // 连续 3 次失败就整页重载，避免永久卡死
    if (fails >= 3) { setTimeout(() => location.reload(), 1500); }
  }
}
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""


def create_app(runners: List[ServiceRunner], started_at: float) -> FastAPI:
    app = FastAPI(title="pt-seedflow", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        # 禁止缓存：否则部署新前端后，浏览器可能仍用旧 JS
        return HTMLResponse(PAGE, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        })

    @app.post("/api/trigger")
    def trigger(payload: dict = Body(...)) -> JSONResponse:
        name = payload.get("service")
        action = payload.get("action", "both")
        runner = next((r for r in runners if r.cfg.name == name), None)
        if runner is None:
            return JSONResponse({"error": "未知服务: %s" % name}, status_code=404)
        try:
            return JSONResponse(runner.manual(action))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/status")
    def status() -> JSONResponse:
        uptime = int(time.time() - started_at)
        hours, rest = divmod(uptime, 3600)
        minutes, seconds = divmod(rest, 60)
        return JSONResponse({
            "uptime_seconds": uptime,
            "uptime_human": "%d 小时 %d 分" % (hours, minutes) if hours else "%d 分 %d 秒" % (minutes, seconds),
            "services": [r.snapshot() for r in runners],
        })

    return app
