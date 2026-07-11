// dashboard/public/views/frames.js — «Кадри» screen: filterable frame gallery + pagination.
// Reuses the pure query/format helpers from /frames-gallery.js, but NOT its galleryHtml() —
// that builds the old fr-grid/fr-tile modal markup which v2's CSS (.frames-toolbar/.frames-grid2,
// styles.css:209-213) does not style.
import { BAND_PRESETS, buildFramesQuery, scannerOptions, toLocalDatetime, frameCaption } from '/frames-gallery.js';
import { el, escapeHtml } from '/views/components.js';

const PAGE_LIMIT = 200;
const TIME_PRESETS = [['1', '1 год'], ['24', '24 год'], ['168', '7 д']];

// Module-level (closure) state so the applied filter + already-loaded frames survive the 30s
// re-render tick (router.renderActive() re-mounts the active screen) and leaving/returning to
// this screen — render() only ever fetches on first mount or on an explicit user action.
let filter = { scanner: '', band: '', standard: '', snrMin: '', from: '', to: '' };
let frames = [];
let lastPageLen = 0;
let loaded = false;

function optionsHtml(rows) {
  return rows.map(([value, label, selected]) =>
    `<option value="${escapeHtml(value)}"${selected ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('');
}

function toolbarHtml(ctx) {
  const scanRows = [['', 'всі сканери', !filter.scanner]]
    .concat(scannerOptions(frames, ctx.scanners().map((s) => s.id), filter.scanner)
      .map((id) => [id, id, id === filter.scanner]));
  const bandRows = [['', 'всі бенди', !filter.band]]
    .concat(Object.entries(BAND_PRESETS).map(([k, b]) => [k, b.label, k === filter.band]));
  const stdRows = [
    ['', 'всі стандарти', !filter.standard],
    ['PAL', 'PAL', filter.standard === 'PAL'],
    ['NTSC', 'NTSC', filter.standard === 'NTSC'],
  ];
  const presets = TIME_PRESETS.map(([hours, label]) =>
    `<button type="button" class="btn-ghost" data-preset-hours="${escapeHtml(hours)}">${escapeHtml(label)}</button>`).join('');
  return `
    <select id="fr-scanner">${optionsHtml(scanRows)}</select>
    <select id="fr-band">${optionsHtml(bandRows)}</select>
    <select id="fr-standard">${optionsHtml(stdRows)}</select>
    <input type="number" id="fr-snr" min="0" step="1" placeholder="SNR ≥ dB" value="${escapeHtml(filter.snrMin || '')}" />
    ${presets}
    <label>від <input type="datetime-local" id="fr-from" value="${escapeHtml(filter.from || '')}" /></label>
    <label>до <input type="datetime-local" id="fr-to" value="${escapeHtml(filter.to || '')}" /></label>
    <button type="button" id="fr-apply" class="btn">Застосувати</button>`;
}

function appendFigures(gridEl, ctx, list) {
  for (const f of list) {
    const cap = frameCaption(f);
    const fig = el('figure', null, `<img src="${escapeHtml(f.url)}" loading="lazy" alt="кадр"><figcaption>${escapeHtml(cap)}</figcaption>`);
    fig.addEventListener('click', () => ctx.handlers.openImage(f.url, cap));
    gridEl.appendChild(fig);
  }
}

function renderGridReplace(gridEl, ctx) {
  gridEl.innerHTML = '';
  if (!frames.length) { gridEl.appendChild(el('p', 'muted', 'Кадрів немає.')); return; }
  appendFigures(gridEl, ctx, frames);
}

function renderMoreButton(moreWrap, ctx, gridEl) {
  moreWrap.innerHTML = '';
  if (lastPageLen < PAGE_LIMIT) return;
  const btn = el('button', 'btn', 'Показати ще');
  btn.type = 'button';
  btn.addEventListener('click', () => fetchAndAppend(ctx, gridEl, moreWrap));
  moreWrap.appendChild(btn);
}

async function fetchAndReplace(ctx, gridEl, moreWrap) {
  const query = buildFramesQuery(filter, { limit: PAGE_LIMIT });
  const result = await ctx.fetchFrames(query);
  frames = (result && result.frames) || [];
  lastPageLen = frames.length;
  loaded = true;
  renderGridReplace(gridEl, ctx);
  renderMoreButton(moreWrap, ctx, gridEl);
}

async function fetchAndAppend(ctx, gridEl, moreWrap) {
  const oldest = frames.length ? frames[frames.length - 1].ts : 0;
  const query = buildFramesQuery(filter, { limit: PAGE_LIMIT, before: oldest });
  const result = await ctx.fetchFrames(query);
  const page = (result && result.frames) || [];
  frames = frames.concat(page);
  lastPageLen = page.length;
  appendFigures(gridEl, ctx, page);
  renderMoreButton(moreWrap, ctx, gridEl);
}

export function render(container, ctx) {
  container.className = 'screen screen-pad';
  container.innerHTML = '';

  const toolbar = el('div', 'frames-toolbar', toolbarHtml(ctx));
  container.appendChild(toolbar);

  const gridEl = el('div', 'frames-grid2');
  const moreWrap = el('div', 'frames-more');

  const fromInput = toolbar.querySelector('#fr-from');
  const toInput = toolbar.querySelector('#fr-to');
  for (const btn of toolbar.querySelectorAll('[data-preset-hours]')) {
    btn.addEventListener('click', () => {
      const hours = Number(btn.dataset.presetHours);
      fromInput.value = toLocalDatetime(Date.now() - hours * 3600 * 1000);
      toInput.value = '';
    });
  }

  toolbar.querySelector('#fr-apply').addEventListener('click', () => {
    filter = {
      scanner: toolbar.querySelector('#fr-scanner').value,
      band: toolbar.querySelector('#fr-band').value,
      standard: toolbar.querySelector('#fr-standard').value,
      snrMin: toolbar.querySelector('#fr-snr').value,
      from: fromInput.value,
      to: toInput.value,
    };
    fetchAndReplace(ctx, gridEl, moreWrap);
  });

  container.appendChild(gridEl);
  container.appendChild(moreWrap);

  if (loaded) {
    renderGridReplace(gridEl, ctx);
    renderMoreButton(moreWrap, ctx, gridEl);
  } else {
    gridEl.appendChild(el('p', 'muted', 'Завантаження…'));
    fetchAndReplace(ctx, gridEl, moreWrap);
  }
}
