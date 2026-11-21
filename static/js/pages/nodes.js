import { api } from '../api.js';

const FATE_DESC = {
  '影子草稿': '悬挂边收纳进影子草稿表，影响分析禁止读取（影响页将剔除）',
  '旁路账册': '悬挂边指纹写入独立凭证行，影响分析仍可读取（影响页仍可见）',
};

function fateChip(n) {
  if (!n.fate) return '';
  const cls = n.fate === '影子草稿' ? 'fate-shadow' : 'fate-bypass';
  return `<span class="tag ${cls}" title="下线命运：${n.fate}">命运 · ${n.fate}</span>`;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function openRetire(n, onDone, setMsg, refreshHeader) {
  const wrap = document.createElement('div');
  wrap.className = 'modal-back';
  wrap.innerHTML = `
    <div class="modal card">
      <h2>下线节点 ${escapeHtml(n.node_code)}</h2>
      <p class="muted">下线后悬挂边只许落一种命运，且不可改写。请选择：</p>
      <label class="fate-opt">
        <input type="radio" name="fate" value="影子草稿" checked />
        <span><b>影子草稿</b><br><span class="muted">${FATE_DESC['影子草稿']}</span></span>
      </label>
      <label class="fate-opt">
        <input type="radio" name="fate" value="旁路账册" />
        <span><b>旁路账册</b><br><span class="muted">${FATE_DESC['旁路账册']}</span></span>
      </label>
      <p class="muted" id="fate-hint"></p>
      <div class="row modal-actions">
        <button type="button" class="primary" id="do-retire">确认下线</button>
        <button type="button" id="cancel-retire">取消</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const hint = wrap.querySelector('#fate-hint');
  const sync = () => {
    const v = wrap.querySelector('input[name=fate]:checked').value;
    hint.textContent = '影响页将' + (v === '影子草稿' ? '剔除' : '仍可见') + '这些悬挂边';
  };
  wrap.querySelectorAll('input[name=fate]').forEach((r) => (r.onchange = sync));
  sync();
  const close = () => wrap.remove();
  wrap.querySelector('#cancel-retire').onclick = close;
  wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });
  wrap.querySelector('#do-retire').onclick = async () => {
    const fate = wrap.querySelector('input[name=fate]:checked').value;
    const btn = wrap.querySelector('#do-retire');
    btn.disabled = true;
    try {
      const r = await api('/api/nodes/' + n.id + '/retire', {
        method: 'POST',
        body: JSON.stringify({ fate }),
      });
      close();
      setMsg(`${r.item.node_code} 已下线 · 命运「${r.item.fate}」 · ${r.item.message || ''}`);
      if (refreshHeader) await refreshHeader();
      await onDone();
    } catch (e) {
      // 白话中文说明失败原因（含并发被抢先）
      setMsg(e.message, true);
      btn.disabled = false;
    }
  };
}

export async function renderNodes(view, setMsg, refreshHeader) {
  const [j, shadow, bypass] = await Promise.all([
    api('/api/nodes'),
    api('/api/edges/shadow'),
    api('/api/edges/bypass'),
  ]);
  const retired = (j.items || []).filter((n) => n.fate);
  view.innerHTML = `
    <section class="card">
      <h2>节点目录</h2>
      <div class="row">
        <label>编号<input id="code" value="N-NEW-1" /></label>
        <label>表名<input id="table" value="dwd_sample" /></label>
        <label>层级<select id="layer"><option>ODS</option><option selected>DWD</option><option>DWS</option><option>ADS</option></select></label>
        <label>责任组<input id="team" value="仓建组" /></label>
        <button class="primary" id="create" type="button">登记节点</button>
      </div>
    </section>
    <section class="card" style="margin-top:1rem">
      <h2>下线命运汇总</h2>
      <p class="muted">下线节点 ${retired.length} · 命运行落定 ${retired.filter((n) => n.fate).length} 条</p>
      <div class="fate-ledger">
        ${shadow.items?.length ? `<div><h3>影子草稿 <span class="tag fate-shadow">${shadow.items.length}</span></h3>
          <ul>${shadow.items.map((s) => `<li><code>${escapeHtml(s.edge_code)}</code> 收纳自 ${escapeHtml(s.retired_node_code)} <span class="muted">fp ${String(s.fingerprint).slice(0, 10)}…</span></li>`).join('')}</ul></div>` : ''}
        ${bypass.items?.length ? `<div><h3>旁路账册 <span class="tag fate-bypass">${bypass.items.length}</span></h3>
          <ul>${bypass.items.map((b) => `<li><code>${escapeHtml(b.edge_code)}</code> ${escapeHtml(b.upstream_code)} → ${escapeHtml(b.downstream_code)} <span class="muted">fp ${String(b.fingerprint).slice(0, 10)}…</span></li>`).join('')}</ul></div>` : ''}
        ${!shadow.items?.length && !bypass.items?.length ? '<p class="muted">尚无节点落定下线命运</p>' : ''}
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(j.items || []).map((n) => `<article class="card node-${n.status === '下线' ? 'retired' : 'live'}">
        <h3>${escapeHtml(n.node_code)}</h3>
        <span class="tag ${n.status === '下线' ? 'tag-retired' : ''}">${escapeHtml(n.status)}</span>
        <span class="tag">${escapeHtml(n.layer)}</span>
        ${fateChip(n)}
        <p>${escapeHtml(n.table_name)}</p>
        <p class="muted">上游 ${n.upstream_count} · 下游 ${n.downstream_count} · ${escapeHtml(n.owner_team)}</p>
        ${n.fate ? `<p class="muted">悬挂边 ${n.fate_dangling_count} · 影子 ${n.shadow_count ?? 0} · 旁路 ${n.bypass_count ?? 0}</p>` : ''}
        <div class="row">
          <button data-act="${n.id}" type="button" ${n.status === '下线' ? 'disabled' : ''}>启用</button>
          <button data-ret="${n.id}" type="button" ${n.status === '下线' ? 'disabled' : ''}>下线</button>
        </div>
      </article>`).join('') || '<p class="muted">暂无节点</p>'}
    </section>`;
  const reload = () => renderNodes(view, setMsg, refreshHeader);
  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/nodes', {
        method: 'POST',
        body: JSON.stringify({
          node_code: document.getElementById('code').value,
          table_name: document.getElementById('table').value,
          layer: document.getElementById('layer').value,
          owner_team: document.getElementById('team').value,
        }),
      });
      setMsg('节点已登记');
      if (refreshHeader) await refreshHeader();
      await reload();
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-act]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/nodes/' + btn.dataset.act + '/activate', { method: 'POST', body: '{}' });
        setMsg('已启用');
        if (refreshHeader) await refreshHeader();
        await reload();
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-ret]').forEach((btn) => {
    btn.onclick = () => {
      const n = (j.items || []).find((x) => String(x.id) === btn.dataset.ret);
      if (n) openRetire(n, reload, setMsg, refreshHeader);
    };
  });
}
