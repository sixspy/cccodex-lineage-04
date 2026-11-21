import { api } from '../api.js';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function progressBar(r) {
  const pct = r.total ? Math.round((r.done / r.total) * 100) : (r.status === '完成' ? 100 : 0);
  return `<div class="progress"><div class="bar" style="width:${pct}%"></div><span>${pct}%</span></div>`;
}

async function pollSampling(runId, setMsg) {
  const card = document.getElementById('sampling-live');
  for (let i = 0; i < 200; i += 1) {
    const { item: s } = await api('/api/coverage/samplings/' + runId);
    if (card) {
      const pct = s.total ? Math.round((s.done / s.total) * 100) : 0;
      card.innerHTML = `
        <h3>抽检 #${s.id} <span class="tag ${s.status === '完成' ? 'chip-ok' : 'chip-warn'}">${s.status}</span></h3>
        ${progressBar(s)}
        <p class="muted">已抽 ${s.done} / ${s.total} · 活依赖边 ${s.live_edges} · 悬挂剔除 ${s.dangling_edges} · 剔除死边 ${s.excluded_dead}</p>`;
      if (s.status === '完成' || s.status === '失败') {
        setMsg(s.message || ('抽检' + s.status));
        return s;
      }
    } else if (s.status === '完成' || s.status === '失败') {
      return s;
    }
    await new Promise((res) => setTimeout(res, 200));
  }
  return null;
}

export async function renderCoverage(view, setMsg) {
  const [reports, samplings, fp, logs] = await Promise.all([
    api('/api/coverage/reports'),
    api('/api/coverage/samplings'),
    api('/api/ingest/fingerprint'),
    api('/api/ingest/logs'),
  ]);
  view.innerHTML = `
    <section class="card">
      <h2>覆盖对账</h2>
      <div class="row">
        <button class="primary" id="go" type="button">跑覆盖对账</button>
      </div>
    </section>
    <section class="card" style="margin-top:1rem">
      <h2>覆盖抽检进度</h2>
      <div class="row">
        <button class="primary" id="sample" type="button">跑覆盖抽检</button>
        <span class="muted">死边不计入活依赖；抽检逐条推进并记录进度</span>
      </div>
      <div id="sampling-live" class="card" style="margin-top:.6rem; display:none"></div>
      <div class="wall" style="margin-top:.8rem">
        ${(samplings.items || []).map((s) => `<article class="card">
          <h3>抽检 #${s.id} <span class="tag ${s.status === '完成' ? 'chip-ok' : 'chip-warn'}">${esc(s.status)}</span></h3>
          ${progressBar(s)}
          <p class="muted">${esc(s.message || '')}</p>
          <p class="muted">活依赖边 ${s.live_edges} · 悬挂剔除 ${s.dangling_edges} · 剔除死边 ${s.excluded_dead} · 孤儿 ${s.orphan_count}</p>
        </article>`).join('') || '<p class="muted">尚无抽检运行</p>'}
      </div>
    </section>
    <section class="card" style="margin-top:1rem">
      <h2>目录指纹 · 清洗入库</h2>
      <p class="muted">当前目录指纹 <code class="fp">${esc(fp.fingerprint)}</code></p>
      <p class="muted">节点 ${fp.node_count} · 已发布边 ${fp.edge_count} · 影子 ${fp.shadow_count} · 旁路 ${fp.bypass_count} · 命运行 ${fp.fate_count}</p>
      <div class="row">
        <button id="use-current" type="button">取当前指纹</button>
        <button class="primary" id="ingest" type="button">按指纹入库</button>
      </div>
      <p class="muted" id="ingest-note"></p>
      <ul class="logs">
        ${(logs.items || []).slice(0, 6).map((l) => `<li class="${l.blocked ? 'err' : 'muted'}">${esc(l.message)}</li>`).join('') || '<li class="muted">暂无入库记录</li>'}
      </ul>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(reports.items || []).map((r) => `<article class="card">
        <h3>报告 #${r.id}</h3>
        <p>${esc(r.summary)}</p>
        <p class="muted">活节点 ${r.node_count} · 活边 ${r.edge_count} · 孤儿 ${r.orphan_count} · 草稿边 ${r.uncovered_edges} · 悬挂 ${r.dangling_count ?? 0} · 剔除死边 ${r.excluded_edges ?? 0}</p>
      </article>`).join('') || '<p class="muted">暂无对账报告</p>'}
    </section>`;
  document.getElementById('go').onclick = async () => {
    try {
      const r = await api('/api/coverage/reports', { method: 'POST', body: '{}' });
      setMsg(r.report.summary);
      await renderCoverage(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  document.getElementById('sample').onclick = async () => {
    try {
      const r = await api('/api/coverage/samplings', { method: 'POST', body: '{}' });
      const live = document.getElementById('sampling-live');
      if (live) live.style.display = 'block';
      setMsg('抽检已启动，进度如下');
      const done = await pollSampling(r.run.id, setMsg);
      if (done) await renderCoverage(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  const fpInput = fp.fingerprint;
  document.getElementById('use-current').onclick = () => {
    document.getElementById('ingest-note').textContent = '已取当前指纹，可入库';
    document.getElementById('ingest').dataset.fp = fpInput;
  };
  document.getElementById('ingest').dataset.fp = fpInput;
  document.getElementById('ingest').onclick = async () => {
    try {
      const btn = document.getElementById('ingest');
      const r = await api('/api/ingest', {
        method: 'POST',
        body: JSON.stringify({ fingerprint: btn.dataset.fp || fpInput }),
      });
      setMsg(r.item.message);
      await renderCoverage(view, setMsg);
    } catch (e) {
      setMsg(e.message, true);
      await renderCoverage(view, setMsg);
    }
  };
}
