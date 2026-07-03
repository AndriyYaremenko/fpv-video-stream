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
