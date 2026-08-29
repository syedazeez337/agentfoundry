"""Thin HTTP layer + read-mostly UI. Exists so the UI is not special-cased."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from af import ANALYSIS_VERSION
from af.evidence import iter_bundles, load_bundle
from af.store import Store
from af.util import Paths


def _routes(root: Path):
    paths = Paths(root)
    store = Store(paths.db)

    def experiments(_q):
        out = []
        for e in store.list_experiments():
            v = store.verdicts_for(e["hash"])
            out.append({"hash": e["hash"], "name": e["name"], "state": e["state"],
                        "verdict": v[0]["result"] if v else None,
                        "counts": store.counts_by_state(e["hash"])})
        return out

    def verdict(q):
        e = store.get_experiment(q.get("id", [""])[0])
        if not e:
            return {"error": "not found"}
        v = store.verdicts_for(e["hash"])
        return v[0] if v else {"error": "no verdict yet"}

    def trials(_q):
        return [b.summary() for b in iter_bundles(paths.bundles)][:200]

    def trial(q):
        b = load_bundle(paths.bundles, q.get("id", [""])[0])
        s = store.get_scores([b.trial_key], ANALYSIS_VERSION).get(b.trial_key)
        return {"manifest": b.manifest, "usage": b.usage,
                "integrity": b.integrity, "score": s,
                "events": [e.to_dict() for e in b.events()],
                "patch": b.patch}

    def clusters(_q):
        from af.analyze import cluster_failures

        bundles = [b for b in iter_bundles(paths.bundles) if b.ok]
        scores = store.get_scores([b.trial_key for b in bundles], ANALYSIS_VERSION)
        return cluster_failures(bundles, scores)

    def landscape(_q):
        from af.analyze import build_landscape

        bundles = [b for b in iter_bundles(paths.bundles) if b.ok]
        scores = store.get_scores([b.trial_key for b in bundles], ANALYSIS_VERSION)
        return build_landscape(bundles, scores)

    def architectures(_q):
        return [{"hash": a["hash"], "name": a["name"],
                 "manifest": json.loads(a["manifest"])}
                for a in store.list_architectures()]

    def archive(_q):
        return store.list_archive()

    return {
        "/api/experiments": experiments,
        "/api/verdict": verdict,
        "/api/trials": trials,
        "/api/trial": trial,
        "/api/clusters": clusters,
        "/api/landscape": landscape,
        "/api/architectures": architectures,
        "/api/archive": archive,
    }


INDEX = """<!doctype html>
<meta charset="utf-8"><title>AgentFoundry</title>
<style>
 body{font:14px ui-monospace,SFMono-Regular,Consolas,monospace;margin:0;
      background:#0d1117;color:#c9d1d9}
 header{padding:14px 20px;border-bottom:1px solid #21262d;font-weight:600}
 main{display:grid;grid-template-columns:320px 1fr;height:calc(100vh - 50px)}
 aside{border-right:1px solid #21262d;overflow:auto;padding:12px}
 section{overflow:auto;padding:18px}
 h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#8b949e;
    margin:18px 0 8px}
 .row{padding:7px 9px;border-radius:6px;cursor:pointer}
 .row:hover{background:#161b22}
 .v{padding:2px 7px;border-radius:10px;font-size:11px}
 .BETTER{background:#1a7f37;color:#fff}.WORSE{background:#a40e26;color:#fff}
 .EQUIVALENT{background:#1f6feb;color:#fff}.INCONCLUSIVE{background:#9e6a03;color:#fff}
 .UNINTERPRETABLE{background:#6e2b2b;color:#fff}
 table{border-collapse:collapse;width:100%;margin:10px 0}
 td,th{padding:6px 10px;border-bottom:1px solid #21262d;text-align:left}
 th{color:#8b949e;font-weight:500}
 pre{background:#161b22;padding:12px;border-radius:8px;overflow:auto;max-height:50vh}
 .dim{color:#8b949e}
</style>
<header>AgentFoundry <span class=dim>&mdash; experiment platform</span></header>
<main>
<aside id=side></aside>
<section id=view><p class=dim>select an experiment</p></section>
</main>
<script>
const $=(s)=>document.querySelector(s);
const get=(u)=>fetch(u).then(r=>r.json());
async function boot(){
  const exps=await get('/api/experiments');
  const arch=await get('/api/architectures');
  $('#side').innerHTML='<h2>experiments</h2>'+exps.map(e=>
    `<div class=row onclick="showVerdict('${e.hash}')">${e.name}<br>
     <span class="v ${e.verdict||''}">${e.verdict||e.state}</span></div>`).join('')
    +'<h2>architectures</h2>'+arch.map(a=>
    `<div class=row>${a.name}<br><span class=dim>${a.hash.slice(0,10)}</span></div>`).join('')
    +'<h2>analysis</h2><div class=row onclick="showClusters()">failure clusters</div>'
    +'<div class=row onclick="showLandscape()">landscape</div>'
    +'<div class=row onclick="showTrials()">trials</div>';
}
async function showVerdict(h){
  const v=await get('/api/verdict?id='+h);
  if(v.error){$('#view').innerHTML='<p class=dim>'+v.error+'</p>';return}
  const p=v.payload;
  $('#view').innerHTML=`<h1>${p.experiment}</h1>
   <p><span class="v ${v.result}">${v.result}</span> ${p.reason}</p>
   <p class=dim>${p.hypothesis||''}</p>
   <table><tr><th>arm<th>resolve<th>95% CI<th>cost<th>flags</tr>
   ${Object.entries(p.arms).map(([k,a])=>`<tr><td>${k}<td>${a.rate.toFixed(3)}
     <td>[${a.ci[0].toFixed(2)}, ${a.ci[1].toFixed(2)}]<td>$${a.cost_usd_mean}
     <td class=dim>${JSON.stringify(a.flags)}</tr>`).join('')}</table>
   <table><tr><th>comparison<th>delta<th>CI<th>p<th>FDR<th>equiv<th>cost</tr>
   ${p.comparisons.map(c=>`<tr><td>${c.baseline}→${c.candidate}
     <td>${c.delta.toFixed(3)}<td>[${c.ci[0].toFixed(3)}, ${c.ci[1].toFixed(3)}]
     <td>${c.p_value.toFixed(3)}<td>${c.significant_after_fdr}
     <td>${c.equivalence.equivalent}<td>${c.cost_delta_usd}</tr>`).join('')}</table>
   <p class=dim>n_tasks=${p.n_tasks} &middot; MDE=${(p.minimum_detectable_effect*100).toFixed(1)}%
    &middot; credibility ${JSON.stringify(p.credibility)}</p>`;
}
async function showClusters(){
  const c=await get('/api/clusters');
  $('#view').innerHTML=`<h1>failure clusters</h1><p class=dim>${c.n_failures} failures</p>
   <table><tr><th>code<th>n<th>share<th>description</tr>
   ${c.clusters.map(x=>`<tr><td>${x.code}<td>${x.n}<td>${(x.share*100).toFixed(1)}%
     <td class=dim>${x.description}</tr>`).join('')}</table>
   <p class=dim>${c.calibration.note}</p>`;
}
// Everything rendered here originates from a trial: a task instruction, a
// model's output, a shell command an agent chose. Interpolating any of it
// into innerHTML unescaped makes an evidence viewer execute the evidence.
function esc(v){
  return String(v==null?'':v).replace(/[&<>"']/g, c=>(
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
async function showLandscape(){
  const l=await get('/api/landscape');
  $('#view').innerHTML=`<h1>decision landscape</h1>
   <h2>traps</h2><table><tr><th>state<th>visits<th>success</tr>
   ${l.traps.map(n=>`<tr><td>${esc(n.state)}<td>${esc(n.visits)}<td>${esc(n.success_rate)}</tr>`).join('')}</table>
   <h2>productive</h2><table><tr><th>state<th>visits<th>success</tr>
   ${l.productive.map(n=>`<tr><td>${esc(n.state)}<td>${esc(n.visits)}<td>${esc(n.success_rate)}</tr>`).join('')}</table>`;
}
async function showTrials(){
  const t=await get('/api/trials');
  $('#view').innerHTML=`<h1>trials</h1><table>
   <tr><th>key<th>task<th>arch<th>status<th>wall<th>cost</tr>
   ${t.map(x=>`<tr onclick="showTrial('${esc(x.trial_key)}')"><td>${esc(x.trial_key.slice(0,12))}
     <td>${esc(x.task)}<td>${esc(x.arch)}<td>${esc(x.status)}<td>${esc(x.wall_s)}<td>$${esc(x.cost_usd)}</tr>`).join('')}
   </table>`;
}
async function showTrial(k){
  const t=await get('/api/trial?id='+k);
  $('#view').innerHTML=`<h1>${esc(k.slice(0,12))}</h1>
   <p class=dim>${esc(t.manifest.task.instruction)}</p>
   <h2>score</h2><pre>${esc(JSON.stringify(t.score&&t.score.outcomes,null,1))}</pre>
   <h2>integrity</h2><pre>${esc(JSON.stringify(t.integrity,null,1))}</pre>
   <h2>events (${esc(t.events.length)})</h2>
   <pre>${t.events.map(e=>esc(e.seq+' '+e.type+' '+JSON.stringify(e.attrs))).join('\\n')}</pre>
   <h2>patch</h2><pre>${esc(t.patch||'(none)')}</pre>`;
}
boot();
</script>
"""


def serve(root: Path, port: int = 8787) -> None:
    routes = _routes(root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                body = INDEX.encode()
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            fn = routes.get(u.path)
            if fn is None:
                self.send_error(404)
                return
            try:
                data = fn(parse_qs(u.query))
                body = json.dumps(data, default=str).encode()
                self.send_response(200)
            except Exception as exc:  # noqa: BLE001
                body = json.dumps({"error": str(exc)}).encode()
                self.send_response(500)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", port), Handler)
    print(f"  AgentFoundry UI  http://127.0.0.1:{port}")
    print("  ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
