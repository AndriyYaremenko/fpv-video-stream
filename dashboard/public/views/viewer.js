// dashboard/public/views/viewer.js — «FPV Viewer»: merged detection list (left) +
// a reconciled grid of per-viewer player cards (right), one per online view-capable SDR.
// Cards (and their live #viewer-video-<id> + MHz input) are built ONCE per id and reused
// across renders — never re-innerHTML'd — so WHEP players and typed input survive data ticks.
import { el, escapeHtml } from '/views/components.js';
import { viewerRows, viewerCards, stepDetectionFreq, ageLabel } from '/viewer.js';
import { renderMiniSpectrum, classColor, fmtFreq, viewCaption } from '/spectrum.js';
import { nearestRxChannel } from '/rx5808-channels.js';

export function render(container, ctx) {
  container.className = 'screen';
  let root = container.querySelector('.viewer');
  if (!root) {
    container.innerHTML = '';
    root = el('div', 'viewer');
    const list = el('div', 'viewer-list');
    list.addEventListener('click', (e) => {
      const btn = e.target.closest('.vw-go');
      if (!btn) return;
      const freq = Number(btn.dataset.vwfreq);
      if (!Number.isFinite(freq)) return;
      ctx.handlers.viewerRowClick(freq, btn.dataset.vwband || '', btn.dataset.vid || '');
    });
    root.appendChild(list);
    root.appendChild(el('div', 'viewer-cards'));
    container.appendChild(root);
  }
  update(root, ctx);
}

function update(root, ctx) {
  const nowS = Math.floor(Date.now() / 1000);
  const store = ctx.scanStore();
  const cards = ctx.scanners().length ? viewerCards(store) : [];
  const rows = viewerRows(ctx.viewerState(), nowS);

  renderList(root.querySelector('.viewer-list'), cards, rows, nowS);
  reconcileCards(root.querySelector('.viewer-cards'), cards, ctx, rows, store);

  ctx.handlers.syncViewerPlayers();   // (re)bind WHEP players to the mounted #viewer-video-<id>
}

// ---- left: merged detection list; each row carries a ▶ button per online viewer ----
function renderList(list, cards, rows, nowS) {
  // Highlight rows whose freq matches ANY active view session.
  const activeFreqs = cards.filter((c) => c.view && c.view.active).map((c) => c.view.freq_mhz);
  list.innerHTML = '';
  if (!cards.length) list.appendChild(el('p', 'muted', 'SDR view недоступний (view-сканер офлайн)'));
  if (!rows.length) { list.appendChild(el('p', 'muted', 'детекцій немає — чекаємо на скан')); return; }
  for (const e of rows) list.appendChild(rowEl(e, cards, nowS, activeFreqs));
}

function rowEl(entry, cards, nowS, activeFreqs) {
  const viewing = activeFreqs.some((f) => f != null && Math.abs(entry.center_mhz - f) < 3);
  const clsName = entry.class === 'analog' ? 'analog' : entry.class === 'digital' ? 'digital' : '';
  const row = el('div', `viewer-row${clsName ? ` ${clsName}` : ''}${entry.live ? '' : ' recent'}${viewing ? ' is-viewing' : ''}`);
  const freqTxt = `${fmtFreq(entry.center_mhz)}${entry.channel ? ` (${escapeHtml(entry.channel)})` : ''}`;
  const snr = entry.snr_db == null ? '—' : `${Number(entry.snr_db).toFixed(1)} dB`;
  const src = Object.keys(entry.seen_by || {}).map(escapeHtml).join(', ') || '—';
  const age = entry.live ? 'зараз' : ageLabel(nowS, entry.last_seen);
  const btns = cards.map((c) =>
    `<button type="button" class="vw-go" data-vid="${escapeHtml(c.id)}" data-vwfreq="${Number(entry.center_mhz)}" ` +
    `data-vwband="${escapeHtml(entry.band || '')}">▶ ${escapeHtml(c.label)}</button>`).join('');
  row.innerHTML = `<div class="vr-top"><span class="vr-freq mono">${freqTxt}</span>` +
    `<span class="mono" style="color:${classColor(entry.class)}">${escapeHtml(entry.class || '')}</span></div>` +
    `<div class="vr-meta">${escapeHtml(entry.band || '')} · SNR ${snr} · ${src} · ${age}</div>` +
    `<div class="vr-go">${btns}</div>`;
  return row;
}

// ---- right: reconcile a card per viewer id (reuse; never destroy a live <video> or MHz input) ----
function reconcileCards(wrap, cards, ctx, rows, store) {
  const wantIds = new Set(cards.map((c) => c.id));
  for (const child of [...wrap.children]) {
    const id = child.id ? child.id.replace('viewer-card-', '') : '';
    if (id && !wantIds.has(id)) child.remove();
  }
  const empty = wrap.querySelector('.viewer-cards-empty'); if (empty) empty.remove();
  for (const c of cards) {
    let card = document.getElementById(`viewer-card-${c.id}`);
    if (!card) {
      card = buildCard(c, ctx, rows);
      const after = [...wrap.children].find(
        (ch) => ch.id && ch.id.startsWith('viewer-card-') && ch.id.slice('viewer-card-'.length) > c.id);
      wrap.insertBefore(card, after || null);
    }
    card.__ctxRows = rows;                                  // fresh rows for the steppers' click reads
    updateCard(card, c, store);
  }
  if (!cards.length) wrap.appendChild(el('p', 'muted viewer-cards-empty', 'Немає доступних вьюверів.'));
}

function buildCard(c, ctx, rows) {
  const card = el('div', 'viewer-card');
  card.id = `viewer-card-${c.id}`;
  card.__ctxRows = rows;
  card.innerHTML = `
    <div class="vc-head mono">${escapeHtml(c.label)}</div>
    <video id="viewer-video-${escapeHtml(c.id)}" autoplay playsinline muted></video>
    <div class="view-controls">
      <button type="button" class="btn vc-step" data-dir="-1">◀</button>
      <input class="vc-freq" type="number" min="100" max="6000" step="1" placeholder="МГц" />
      <button type="button" class="btn vc-step" data-dir="1">▶</button>
      <button type="button" class="btn vc-play">▶ дивитись</button>
      <button type="button" class="btn vc-stop" hidden>■ свіп</button>
      <span class="vc-badge view-badge"></span>
      <span class="vc-err view-err"></span>
    </div>
    <canvas class="mini-spectrum" width="300" height="60"></canvas>`;

  const freqInput = card.querySelector('.vc-freq');
  const curFreq = () => {
    const view = card.__view;
    if (view && view.active && view.freq_mhz != null) return view.freq_mhz;
    const v = Number(freqInput.value);
    return Number.isFinite(v) ? v : null;
  };
  card.querySelectorAll('.vc-step').forEach((b) => b.addEventListener('click', () => {
    const f = stepDetectionFreq(card.__ctxRows, curFreq(), Number(b.dataset.dir));
    if (f != null) { freqInput.value = String(f); ctx.onViewStart(c.id, f); }
  }));
  card.querySelector('.vc-play').addEventListener('click', () => {
    const f = Number(freqInput.value);
    if (Number.isFinite(f) && f >= 100 && f <= 6000) ctx.onViewStart(c.id, f);
  });
  card.querySelector('.vc-stop').addEventListener('click', () => ctx.onViewStop(c.id));

  const canvas = card.querySelector('canvas.mini-spectrum');
  canvas.addEventListener('click', (e) => {
    const lo = Number(canvas.dataset.lowMhz), hi = Number(canvas.dataset.highMhz);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return;
    const rect = canvas.getBoundingClientRect();
    const x = Math.min(rect.width, Math.max(0, e.clientX - rect.left));
    const freq = Math.round(lo + (x / (rect.width || 1)) * (hi - lo));
    freqInput.value = String(freq);
    if (canvas.classList.contains('tunable') && canvas.dataset.sid) {
      const ch = nearestRxChannel(freq);
      if (ch) ctx.onScanCmd(canvas.dataset.sid, { mode: 'manual', channel: ch.name });
    }
  });
  return card;
}

// The band behind a card's mini-spectrum: band containing the active view freq; else 5.8G; else first.
function pickBand(live, view) {
  const bands = (live && live.bands) || {};
  if (view && view.active && view.freq_mhz != null) {
    for (const [name, range] of Object.entries(bands)) {
      if (range && view.freq_mhz >= range.low_mhz && view.freq_mhz <= range.high_mhz) return name;
    }
  }
  if (bands['5.8G']) return '5.8G';
  const keys = Object.keys(bands);
  return keys.length ? keys[0] : null;
}

function updateCard(card, c, store) {
  const view = c.view;
  card.__view = view;                                        // for the steppers' curFreq()
  card.querySelector('.vc-badge').textContent = view ? viewCaption(view) : '';
  card.querySelector('.vc-err').textContent = (view && view.error) || '';
  card.querySelector('.vc-stop').hidden = !(view && view.active);
  card.classList.toggle('is-active', !!(view && view.active));

  const live = store[c.id] || null;
  const band = live ? pickBand(live, view) : null;
  const range = (live && band && live.bands && live.bands[band]) || {};
  const psd = (live && band && live.latestPsd && live.latestPsd[band]) || [];
  const dets = live ? ((live.detection && live.detection.detections) || []).filter((d) => d.band === band) : [];
  const rxFreq = live && live.rxtune ? live.rxtune.freq_mhz : null;

  const canvas = card.querySelector('canvas.mini-spectrum');
  canvas.classList.remove('tunable');
  if (range.low_mhz != null) {
    canvas.dataset.lowMhz = range.low_mhz; canvas.dataset.highMhz = range.high_mhz; canvas.dataset.sid = c.id;
  } else { delete canvas.dataset.lowMhz; delete canvas.dataset.highMhz; delete canvas.dataset.sid; }
  renderMiniSpectrum(canvas, { psd, range, dets, rxFreq, tunable: band === '5.8G' });
}
