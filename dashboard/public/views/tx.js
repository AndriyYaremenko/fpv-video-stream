// dashboard/public/views/tx.js — «Передавач»: dashboard-controlled TX generator, one card per
// TX-capable node (store[id].txstate != null). RECONCILE-BASED (build-once skeleton + in-place live
// updates), like views/nodes.js — route is live:true so a full innerHTML rebuild each tick would wipe
// the operator's typed freq/gain and close the file <select>. Start requires a confirm (RF safety).
import { el, pip, escapeHtml } from '/views/components.js';

const STANDARDS = ['PAL', 'NTSC'];

function fmtCountdown(untilTs, nowS) {
  if (untilTs == null) return '';
  const left = Math.max(0, untilTs - nowS);
  const m = Math.floor(left / 60), s = left % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

// Build one node's TX card once. Live fields carry data-role markers for updateCard().
function buildCard(id, ctx) {
  const card = el('div', 'tx-card');
  card.dataset.id = id;
  card.innerHTML = `<div class="tx-head"><span data-role="pip"></span>
      <span class="tx-title mono">${escapeHtml(id)}</span></div>
    <div class="tx-banner" data-role="banner"></div>
    <div class="tx-form">
      <label>Файл<select class="tx-file" data-role="file"></select></label>
      <label>Частота, МГц<input class="tx-freq" type="number" min="100" max="6000" step="1" placeholder="МГц"></label>
      <label>Gain<input class="tx-gain" type="number" min="0" max="60" step="1"></label>
      <label>Девіація, МГц<input class="tx-dev" type="number" min="0.5" max="10" step="0.5"></label>
      <label>Стандарт<select class="tx-std">${STANDARDS.map((s) => `<option>${s}</option>`).join('')}</select></label>
    </div>
    <div class="tx-actions">
      <button type="button" class="btn tx-start">▶ Старт</button>
      <button type="button" class="btn tx-stop" data-role="stop">■ Стоп</button>
      <span class="tx-err" data-role="err"></span>
    </div>`;

  const files = card.querySelector('[data-role=file]');
  const freq = card.querySelector('.tx-freq');
  const gain = card.querySelector('.tx-gain');
  const dev = card.querySelector('.tx-dev');
  const std = card.querySelector('.tx-std');

  card.querySelector('.tx-start').addEventListener('click', () => {
    const file = files.value;
    const f = Number(freq.value);
    if (!file || !Number.isFinite(f) || f < 100 || f > 6000) return;
    // eslint-disable-next-line no-alert
    if (!(typeof confirm === 'function') || confirm(`Почати передачу «${file}» на ${f} МГц?`)) {
      ctx.onTxStart(id, {
        file, freqMhz: f,
        gainDb: gain.value === '' ? undefined : Number(gain.value),
        deviationMhz: dev.value === '' ? undefined : Number(dev.value),
        standard: std.value,
      });
    }
  });
  card.querySelector('.tx-stop').addEventListener('click', () => ctx.onTxStop(id));
  return card;
}

// Refresh live fields only; never rebuild the <select>/inputs the operator is using. The file
// <select> options are reconciled by value so a new txfiles list doesn't reset the current pick.
function updateCard(card, id, store, nowS) {
  const s = store[id] || {};
  const tx = s.txstate || {};
  card.querySelector('[data-role=pip]').innerHTML = pip(!!s.online);

  const sel = card.querySelector('[data-role=file]');
  const want = (s.txfiles && s.txfiles.files ? s.txfiles.files : []).map((f) => f.name);
  const have = [...sel.options].map((o) => o.value);
  if (want.join('') !== have.join('')) {
    const cur = sel.value;
    sel.innerHTML = want.map((n) => `<option>${escapeHtml(n)}</option>`).join('');
    if (want.includes(cur)) sel.value = cur;               // preserve the operator's pick
  }

  const active = !!tx.active;
  const banner = card.querySelector('[data-role=banner]');
  if (active && tx.status === 'transmitting') {
    banner.className = 'tx-banner on';
    banner.textContent = `📡 TX НА ${tx.freq_mhz} МГц · ${tx.file || ''} · ⏱ ${fmtCountdown(tx.until_ts, nowS)}`;
  } else if (tx.status === 'rendering') {
    banner.className = 'tx-banner rendering';
    banner.textContent = `⚙ Рендер «${tx.file || ''}»…`;
  } else {
    banner.className = 'tx-banner';
    banner.textContent = 'очікування';
  }
  card.querySelector('[data-role=stop]').disabled = !(active || tx.status === 'rendering');
  card.querySelector('[data-role=err]').textContent = tx.error || '';
}

export function render(container, ctx) {
  container.className = 'screen screen-pad';
  let root = container.querySelector('.tx-root');
  if (!root) {
    container.innerHTML = '';
    container.appendChild(el('div', 'label-caps', 'ПЕРЕДАВАЧ'));
    root = el('div', 'tx-root');
    container.appendChild(root);
  }
  const store = ctx.scanStore();
  const nowS = Math.floor(Date.now() / 1000);
  const ids = Object.keys(store).filter((id) => store[id] && store[id].txstate);

  if (!ids.length) {
    root.innerHTML = '<p class="muted">Немає TX-здатних вузлів. Увімкни TX_ENABLED на bladeRF-ноді.</p>';
    return;
  }
  // Drop cards for nodes that vanished.
  const existing = new Map();
  for (const child of [...root.children]) {
    const id = child.dataset && child.dataset.id;
    if (!id) { child.remove(); continue; }
    if (!ids.includes(id)) child.remove(); else existing.set(id, child);
  }
  for (const id of ids) {
    let card = existing.get(id);
    if (!card) { card = buildCard(id, ctx); root.appendChild(card); }
    updateCard(card, id, store, nowS);
  }
}
