// dashboard/public/frames-gallery.js — pure HTML builders for the 🖼️ frames
// gallery modal. No DOM access at module scope so node --test can import it.

function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

function fmtWhen(tsSec) {
  const t = new Date(Number(tsSec) * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
}

export function scannerIds(frames) {
  return [...new Set((frames || []).map((f) => f && f.scanner_id).filter(Boolean))].sort();
}

export function frameCaption(f) {
  const parts = [fmtWhen(f.ts), `${Math.round(Number(f.center_mhz))} МГц`, f.scanner_id];
  if (f.sync_snr_db != null) parts.push(`sync ${f.sync_snr_db} dB`);
  return parts.join(' · ');
}

// Full modal body. `scanner` filters the tiles; the select always lists every
// scanner present in `frames` so the operator can switch back to "всі".
export function galleryHtml(frames, scanner = '') {
  const all = frames || [];
  const shown = scanner ? all.filter((f) => f.scanner_id === scanner) : all;
  const opts = ['<option value="">всі сканери</option>']
    .concat(scannerIds(all).map((id) =>
      `<option value="${escapeHtml(id)}"${id === scanner ? ' selected' : ''}>${escapeHtml(id)}</option>`))
    .join('');
  const tiles = shown.map((f) => {
    const cap = frameCaption(f);
    return `<button type="button" class="fr-tile" data-src="${escapeHtml(f.url)}" data-cap="${escapeHtml(cap)}">
      <img loading="lazy" src="${escapeHtml(f.url)}" alt="кадр" />
      <span class="fr-cap">${escapeHtml(cap)}</span>
    </button>`;
  }).join('');
  const grid = shown.length ? `<div class="fr-grid">${tiles}</div>` : '<p class="muted">Кадрів немає.</p>';
  return `<h2>🖼️ Кадри <button type="button" id="frames-refresh" class="btn-ghost">оновити</button></h2>
    <div class="fr-toolbar"><label>Сканер <select id="frames-scanner">${opts}</select></label></div>
    ${grid}
    <div class="form-actions"><button type="button" data-close class="btn-primary">Закрити</button></div>`;
}

// Band presets for the toolbar — keys are the <select> values, ranges go to
// the server as fmin/fmax (MHz, inclusive).
export const BAND_PRESETS = {
  '0.9': { fmin: 800, fmax: 1000, label: '0.9G (шум)' },
  '1.2': { fmin: 1000, fmax: 1500, label: '1.2G' },
  '2.4': { fmin: 2200, fmax: 2700, label: '2.4G' },
  '5.8': { fmin: 5000, fmax: 6100, label: '5.8G' },
};

// filter {scanner, band, standard, snrMin, from, to} + extra {limit, before}
// → /api/frames querystring. from/to are datetime-local strings interpreted in
// the browser's timezone; the server only ever sees epoch seconds.
export function buildFramesQuery(filter = {}, extra = {}) {
  const q = new URLSearchParams();
  q.set('limit', String(extra.limit || 200));
  if (filter.scanner) q.set('scanner', filter.scanner);
  const band = BAND_PRESETS[filter.band];
  if (band) { q.set('fmin', String(band.fmin)); q.set('fmax', String(band.fmax)); }
  if (filter.standard) q.set('standard', filter.standard);
  const snr = Number(filter.snrMin);
  if (Number.isFinite(snr) && snr > 0) q.set('snr_min', String(snr));
  const from = filter.from ? Math.floor(new Date(filter.from).getTime() / 1000) : 0;
  const to = filter.to ? Math.floor(new Date(filter.to).getTime() / 1000) : 0;
  if (from) q.set('since', String(from));
  if (to) q.set('until', String(to));
  if (extra.before) q.set('before', String(extra.before));
  return q.toString();
}

// Epoch ms → local 'YYYY-MM-DDTHH:MM' for <input type="datetime-local">.
export function toLocalDatetime(ms) {
  const t = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}T${p(t.getHours())}:${p(t.getMinutes())}`;
}

// Scanner <select> options: registry scanners ∪ ids in the current result ∪
// the current selection — so applying a scanner filter doesn't collapse the list.
export function scannerOptions(frames, registryIds = [], selected = '') {
  return [...new Set([...registryIds, ...scannerIds(frames), ...(selected ? [selected] : [])])].sort();
}
