import { api } from '../api.js';

const STATE_LABEL = {
  '活': '活依赖',
  '旁路': '旁路凭证',
  '影子': '影子草稿',
  '悬挂': '悬挂死边',
  '非发布': '非发布',
};
const STATE_CLASS = {
  '活': 'state-live',
  '旁路': 'fate-bypass',
  '影子': 'fate-shadow',
  '悬挂': 'state-dead',
  '非发布': 'state-idle',
};

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export async function renderEdges(view, setMsg) {
  const [nodes, edges, shadow, bypass] = await Promise.all([
    api('/api/nodes'),
    api('/api/edges'),
    api('/api/edges/shadow'),
    api('/api/edges/bypass'),
  ]);
  const opts = (nodes.items || []).filter((n) => n.status !== '下线' && n.status !== '停用')
    .map((n) => `<option value="${n.id}">${esc(n.node_code)}</option>`).join('');
  const counts = {};
  (edges.items || []).forEach((e) => { counts[e.state] = (counts[e.state] || 0) + 1; });
  view.innerHTML = `
    <section class="card">
      <h2>边关系</h2>
      <div class="row">
        <label>边号<input id="code" value="E-NEW-1" /></label>
        <label>上游<select id="up">${opts}</select></label>
        <label>下游<select id="down">${opts}</select></label>
        <label>变换<input id="kind" value="直抽" /></label>
        <button class="primary" id="create" type="button">创建边</button>
      </div>
      <p class="muted" style="margin-top:.6rem">
        活依赖 ${counts['活'] || 0} · 旁路凭证 ${counts['旁路'] || 0} · 影子草稿 ${counts['影子'] || 0} · 悬挂死边 ${counts['悬挂'] || 0} · 非发布 ${counts['非发布'] || 0}
      </p>
    </section>
    ${(shadow.items?.length || bypass.items?.length) ? `<section class="card" style="margin-top:1rem">
      <h2>悬挂边命运账册</h2>
      <div class="wall">
        <article class="card">
          <h3>影子草稿 <span class="tag fate-shadow">${shadow.items?.length || 0}</span></h3>
          <p class="muted">收纳悬挂边，影响分析禁止读取</p>
          <ul>${(shadow.items || []).map((s) => `<li><code>${esc(s.edge_code)}</code> · 下线节点 ${esc(s.retired_node_code)}<br><span class="muted fp">${esc(s.fingerprint)}</span></li>`).join('') || '<li class="muted">无</li>'}</ul>
        </article>
        <article class="card">
          <h3>旁路账册 <span class="tag fate-bypass">${bypass.items?.length || 0}</span></h3>
          <p class="muted">指纹写入独立凭证行，影响分析仍可见</p>
          <ul>${(bypass.items || []).map((b) => `<li><code>${esc(b.edge_code)}</code> · ${esc(b.upstream_code)} → ${esc(b.downstream_code)}<br><span class="muted fp">${esc(b.fingerprint)}</span></li>`).join('') || '<li class="muted">无</li>'}</ul>
        </article>
      </div>
    </section>` : ''}
    <section class="wall" style="margin-top:1rem">
      ${(edges.items || []).map((e) => `<article class="card edge-${e.state === '悬挂' ? 'dead' : e.state === '影子' ? 'shadow' : 'live'}">
        <h3>${esc(e.edge_code)}</h3>
        <span class="tag ${STATE_CLASS[e.state] || ''}" title="边命运状态">${STATE_LABEL[e.state] || esc(e.state)}</span>
        <span class="tag">${esc(e.status)}</span>
        <p>${esc(e.upstream_code)} → ${esc(e.downstream_code)}</p>
        <p class="muted">${esc(e.transform_kind)} · ${esc(e.upstream_table)} → ${esc(e.downstream_table)}</p>
        ${e.state === '悬挂' ? '<p class="err">该边挂在已停用/下线节点上，覆盖与影响都将剔除</p>' : ''}
        ${e.state === '影子' ? '<p class="muted">影子草稿收纳，影响分析禁止读取</p>' : ''}
        ${e.state === '旁路' ? '<p class="muted">旁路凭证，影响分析仍可见</p>' : ''}
        <div class="row">
          <button data-pub="${e.id}" class="primary" type="button" ${e.status === '草稿' ? '' : 'disabled'}>发布</button>
          <button data-void="${e.id}" type="button" ${e.status === '已作废' ? 'disabled' : ''}>作废</button>
        </div>
      </article>`).join('') || '<p class="muted">暂无边</p>'}
    </section>`;
  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/edges', {
        method: 'POST',
        body: JSON.stringify({
          edge_code: document.getElementById('code').value,
          upstream_id: Number(document.getElementById('up').value),
          downstream_id: Number(document.getElementById('down').value),
          transform_kind: document.getElementById('kind').value,
        }),
      });
      setMsg('边已创建');
      await renderEdges(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-pub]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/edges/' + btn.dataset.pub + '/publish', { method: 'POST', body: '{}' });
        setMsg('边已发布');
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-void]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/edges/' + btn.dataset.void + '/void', { method: 'POST', body: '{}' });
        setMsg('边已作废');
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
