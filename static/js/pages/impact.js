import { api } from '../api.js';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function exclusionChip(j) {
  if (j.dangling_excluded) {
    return `<span class="tag state-dead">已剔除悬挂边 ${j.excluded_count}</span>`;
  }
  return '<span class="tag state-live">未剔除悬挂边</span>';
}

export async function renderImpact(view, setMsg) {
  const [nodes, jobs, fateResp] = await Promise.all([
    api('/api/nodes'), api('/api/impact/jobs'), api('/api/fates'),
  ]);
  const fateRuns = fateResp.items || [];
  const opts = (nodes.items || []).filter((n) => n.status !== '下线' && n.status !== '停用')
    .map((n) => `<option value="${n.id}">${esc(n.node_code)}</option>`).join('');
  view.innerHTML = `
    <section class="card">
      <h2>影响分析</h2>
      <div class="row">
        <label>根节点<select id="root">${opts}</select></label>
        <button class="primary" id="go" type="button">启动影响分析</button>
      </div>
      <p class="muted">悬挂边命运决定影响页是否可见：影子草稿剔除，旁路账册保留。</p>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(jobs.items || []).map((j) => `<article class="card">
        <h3>${esc(j.job_code)}</h3>
        <span class="tag">${esc(j.status)}</span>
        ${exclusionChip(j)}
        <p>根 ${esc(j.node_code)} · 命中 ${j.affected_count}</p>
        <p class="muted">${esc(j.message)}</p>
        ${j.fate ? `<p class="muted">涉及命运：${esc(j.fate)}</p>` : ''}
        <button data-d="${j.id}" type="button">详情</button>
        <div id="j${j.id}"></div>
      </article>`).join('') || '<p class="muted">尚无影响作业</p>'}
    </section>
    <section class="card" style="margin-top:1rem">
      <h2>命运行台账 · 对账</h2>
      <p class="muted">核芯片与影响剔除结果，库内命运行可对：下方列出每个下线节点落定的命运与凭证指纹。</p>
      <div class="wall">
        ${fateRuns.map((f) => `<article class="card">
          <h3>${esc(f.node_code)}</h3>
          <span class="tag ${f.fate === '影子草稿' ? 'fate-shadow' : 'fate-bypass'}">命运 · ${esc(f.fate)}</span>
          <p class="muted">悬挂边 ${f.dangling_count} · 落定于 ${esc(f.created_at)}</p>
          <p class="muted">影响页：${f.fate === '影子草稿' ? '剔除这些悬挂边' : '仍可见这些悬挂边'}</p>
          <ul>${(f.edges || []).map((ed) => `<li><code>${esc(ed.edge_code)}</code> <span class="fp">${esc(String(ed.fingerprint).slice(0, 16))}…</span></li>`).join('') || '<li class="muted">无悬挂边</li>'}</ul>
        </article>`).join('') || '<p class="muted">尚无命运行</p>'}
      </div>
    </section>`;
  document.getElementById('go').onclick = async () => {
    try {
      const r = await api('/api/impact/start', {
        method: 'POST',
        body: JSON.stringify({ root_node_id: Number(document.getElementById('root').value) }),
      });
      setMsg('影响作业 ' + r.job.job_code + ' · ' + r.job.message);
      await renderImpact(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-d]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const r = await api('/api/impact/jobs/' + btn.dataset.d);
        const hits = (r.hits || []).map((h) =>
          `<li>+${h.hop} · ${esc(h.node_code)} · ${esc(h.table_name)} · ${esc(h.layer)}`
          + `${h.node_status === '下线' ? ' <span class="tag fate-bypass">已下线·旁路可见</span>' : ''}</li>`).join('');
        const ex = (r.excluded || []).map((x) =>
          `<li class="err"><code>${esc(x.edge_code)}</code> ${esc(x.upstream_code)} → ${esc(x.downstream_code)} · ${esc(x.fate)} · ${esc(x.reason)}</li>`).join('');
        document.getElementById('j' + btn.dataset.d).innerHTML = `
          <h4>命中节点</h4><ul>${hits || '<li class="muted">无</li>'}</ul>
          <h4>剔除的悬挂边</h4><ul>${ex || '<li class="muted">本次无剔除</li>'}</ul>`;
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
