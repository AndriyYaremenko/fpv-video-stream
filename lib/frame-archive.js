import { readFileSync, writeFileSync, mkdirSync, unlinkSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const SCANNER_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;   // also blocks '.'/'..'/path tricks
const FILE_RE = /^\d+_\d+\.png$/;

// Logical frame id: "<scanner>/<tsMs>_<centerMhz>". ts is float epoch seconds on
// the wire; ms in the name avoids two same-second frames overwriting each other.
export function frameId(scannerId, ts, centerMhz) {
  return `${scannerId}/${Math.round(Number(ts) * 1000)}_${Math.round(Number(centerMhz))}`;
}

export class FrameArchive {
  constructor({ dir, indexFile = '', max = 20000 } = {}) {
    this._dir = dir;
    this._indexFile = indexFile;
    this._max = max;
    this._entries = [];        // oldest..newest {id, scanner_id, ts, center_mhz, standard, line_hz, sync_snr_db}
    this._ids = new Set();
    this._load();
  }

  // Archive one fpv/<id>/video payload: validate, decode, write PNG, index.
  // Returns the record, or null when malformed or already archived (the topic is
  // retained → the broker redelivers the last frame on every reconnect).
  ingest(scannerId, payload) {
    if (!SCANNER_RE.test(String(scannerId || ''))) return null;
    const p = payload || {};
    const ts = Number(p.ts);
    const center = Number(p.center_mhz);
    if (!Number.isFinite(ts) || ts <= 0 || !Number.isFinite(center) || center <= 0) return null;
    if (typeof p.frame_png_b64 !== 'string' || !p.frame_png_b64) return null;
    const png = Buffer.from(p.frame_png_b64, 'base64');
    if (png.length <= PNG_MAGIC.length || !png.subarray(0, PNG_MAGIC.length).equals(PNG_MAGIC)) return null;
    const id = frameId(scannerId, ts, center);
    if (this._ids.has(id)) return null;
    mkdirSync(join(this._dir, scannerId), { recursive: true });
    writeFileSync(join(this._dir, `${id}.png`), png);
    const rec = {
      id, scanner_id: scannerId, ts, center_mhz: center,
      standard: p.standard ?? null, line_hz: p.line_hz ?? null,
      sync_snr_db: p.sync_snr_db ?? null,
    };
    this._entries.push(rec);
    this._ids.add(id);
    while (this._entries.length > this._max) this._remove(0);  // count backstop; age retention is the real bound
    this._persist();
    return rec;
  }

  // Newest-first metadata for GET /api/frames. since = epoch seconds, strictly newer.
  list({ scanner = '', since = 0, limit = 200 } = {}) {
    const out = [];
    for (let i = this._entries.length - 1; i >= 0 && out.length < limit; i--) {
      const e = this._entries[i];
      if (scanner && e.scanner_id !== scanner) continue;
      if (since && e.ts <= since) continue;
      out.push({ ...e, url: `/api/frames/${e.id}.png` });
    }
    return out;
  }

  // Absolute PNG path for GET /api/frames/:scanner/:file — null unless both
  // segments are strictly well-formed AND the id is indexed AND the file exists
  // (pruned → 404). res.sendFile requires an absolute path.
  filePath(scanner, file) {
    if (!SCANNER_RE.test(String(scanner || '')) || !FILE_RE.test(String(file || ''))) return null;
    const id = `${scanner}/${file.slice(0, -'.png'.length)}`;
    if (!this._ids.has(id)) return null;
    const p = resolve(join(this._dir, `${id}.png`));
    return existsSync(p) ? p : null;
  }

  // Drop the entry at position i AND its PNG.
  _remove(i) {
    const [e] = this._entries.splice(i, 1);
    this._ids.delete(e.id);
    try { unlinkSync(join(this._dir, `${e.id}.png`)); } catch { /* already gone */ }
  }

  _load() {
    if (!this._indexFile) return;
    try {
      const arr = JSON.parse(readFileSync(this._indexFile, 'utf8'));
      if (Array.isArray(arr)) {
        this._entries = arr.filter((e) => e && typeof e.id === 'string').slice(-this._max);
        this._ids = new Set(this._entries.map((e) => e.id));
      }
    } catch { /* no index yet — start empty */ }
  }

  // Sync full rewrite per ingest/prune, like the detection journal: frames are
  // infrequent (one per demod hit) and the index is capped, so O(n) is fine.
  _persist() {
    if (!this._indexFile) return;
    try { writeFileSync(this._indexFile, JSON.stringify(this._entries), 'utf8'); } catch { /* best-effort */ }
  }
}
