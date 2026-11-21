import { api } from './api.js';
import { renderNodes } from './pages/nodes.js';
import { renderEdges } from './pages/edges.js';
import { renderImpact } from './pages/impact.js';
import { renderNotify } from './pages/notify.js';
import { renderCoverage } from './pages/coverage.js';
import { renderSettings } from './pages/settings.js';

const view = document.getElementById('view');
const msg = document.getElementById('msg');
const chip = document.getElementById('chip');
const fateChip = document.getElementById('fate-chip');
const pages = {
  nodes: renderNodes,
  edges: renderEdges,
  impact: renderImpact,
  notify: renderNotify,
  coverage: renderCoverage,
  settings: renderSettings,
};

function setMsg(t, err = false) {
  msg.textContent = t || '';
  msg.className = err ? 'err' : 'muted';
}

async function refreshHeader() {
  const meta = await api('/api/meta');
  document.getElementById('lab').textContent = meta.lab || '';
  chip.textContent = meta.coverage_chip || '';
  chip.className = 'tag chip-' + (meta.coverage_chip === '已齐套' ? 'ok'
    : meta.coverage_chip === '未对账' ? 'idle' : 'warn');
  if (fateChip) {
    fateChip.textContent = meta.fate_chip || '无下线节点';
    fateChip.classList.toggle('warn', (meta.fate_summary?.total || 0) > 0);
  }
  return meta;
}

async function go(name) {
  document.querySelectorAll('#nav button').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  // Expose header refresh so action pages can update the top-bar fate chip.
  await pages[name](view, setMsg, refreshHeader);
  await refreshHeader();
}

document.querySelectorAll('#nav button').forEach((b) => {
  b.onclick = () => go(b.dataset.tab).catch((e) => setMsg(e.message, true));
});
document.getElementById('logout').onclick = async () => {
  await api('/api/logout', { method: 'POST', body: '{}' });
  location.href = '/login';
};

refreshHeader()
  .then(() => go('nodes'))
  .catch((e) => setMsg(e.message, true));
