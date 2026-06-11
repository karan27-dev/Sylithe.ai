"""Minimal operator dashboard served at / — zero build step.

Replace with the full React frontend when it lands; this consumes the same API.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sylithe AI — Operations</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background:#0b0e14;
         color:#d7dce2; margin:0; padding:2rem; }
  h1 { font-size:1.1rem; letter-spacing:.2em; color:#7aa2f7; }
  .card { background:#11151f; border:1px solid #1f2633; border-radius:10px;
          padding:1rem 1.25rem; margin-bottom:1rem; }
  textarea { width:100%; min-height:90px; background:#0b0e14; color:#d7dce2;
             border:1px solid #2a3344; border-radius:6px; padding:.6rem; box-sizing:border-box; }
  button { background:#7aa2f7; color:#0b0e14; border:0; border-radius:6px;
           padding:.55rem 1.2rem; font-weight:700; cursor:pointer; margin-top:.5rem; }
  button:disabled { opacity:.5; }
  table { width:100%; border-collapse:collapse; font-size:.85rem; }
  th, td { text-align:left; padding:.4rem .6rem; border-bottom:1px solid #1f2633;
           vertical-align:top; }
  .status-completed { color:#9ece6a; } .status-error { color:#f7768e; }
  .status-running { color:#e0af68; } .status-needs_confirmation { color:#bb9af7; }
  pre { white-space:pre-wrap; margin:0; max-height:8rem; overflow:auto; }
  small { color:#565f73; }
</style>
</head>
<body>
<h1>SYLITHE // OPS</h1>
<div class="card">
  <div id="health"><small>checking health…</small></div>
</div>
<div class="card">
  <textarea id="task" placeholder="Describe a DevOps task, e.g. 'Check the repo for merge conflicts, resolve what is safe, and run tests.'"></textarea>
  <br><button id="go" onclick="runTask()">Run agent</button>
  <div id="result"></div>
</div>
<div class="card">
  <table><thead><tr><th>#</th><th>Task</th><th>Status</th><th>Iter</th><th>Result</th></tr></thead>
  <tbody id="runs"></tbody></table>
</div>
<script>
async function refresh() {
  const h = await (await fetch('/healthz')).json();
  document.getElementById('health').innerHTML =
    `model: <b>${h.model}</b> · skills: ${h.skills.length} · audit chain: ` +
    (h.audit_chain_valid ? '<span class="status-completed">valid</span>'
                         : '<span class="status-error">BROKEN</span>');
  const runs = await (await fetch('/api/runs')).json();
  document.getElementById('runs').innerHTML = runs.map(r =>
    `<tr><td>${r.id}</td><td><pre>${esc(r.task)}</pre></td>` +
    `<td class="status-${r.status}">${r.status}</td><td>${r.iterations}</td>` +
    `<td><pre>${esc(r.result || '')}</pre></td></tr>`).join('');
}
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
async function runTask() {
  const btn = document.getElementById('go'); btn.disabled = true;
  document.getElementById('result').innerHTML = '<small>running…</small>';
  try {
    const res = await fetch('/api/runs', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({task: document.getElementById('task').value})});
    const data = await res.json();
    document.getElementById('result').innerHTML =
      `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
  } catch (e) {
    document.getElementById('result').innerHTML = `<pre>${esc(String(e))}</pre>`;
  }
  btn.disabled = false; refresh();
}
refresh(); setInterval(refresh, 10000);
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return _PAGE
