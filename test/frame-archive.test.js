import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FrameArchive } from '../lib/frame-archive.js';

// A "PNG" is anything starting with the 8-byte PNG signature — enough for the magic check.
const PNG = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  Buffer.from('frame-bytes'),
]);
const B64 = PNG.toString('base64');

function payload(ts, center, extra = {}) {
  return {
    scanner_id: 'hackrf', ts, center_mhz: center,
    standard: 'PAL', line_hz: 15625, sync_snr_db: 12.5, frame_png_b64: B64, ...extra,
  };
}
const tmp = () => mkdtempSync(join(tmpdir(), 'fr-'));

test('ingest decodes and writes the PNG, indexes it, persists the index', () => {
  const dir = tmp();
  const idx = join(dir, 'frames-index.json');
  const a = new FrameArchive({ dir, indexFile: idx });
  const rec = a.ingest('hackrf', payload(1751500000.25, 5865.0));
  assert.equal(rec.id, 'hackrf/1751500000250_5865');
  assert.equal(rec.scanner_id, 'hackrf');
  assert.equal(rec.center_mhz, 5865);
  assert.equal(rec.sync_snr_db, 12.5);
  const file = join(dir, 'hackrf', '1751500000250_5865.png');
  assert.ok(existsSync(file));
  assert.deepEqual(readFileSync(file), PNG);          // decoded bytes, not base64 text
  const persisted = JSON.parse(readFileSync(idx, 'utf8'));
  assert.equal(persisted.length, 1);
  assert.equal(persisted[0].id, 'hackrf/1751500000250_5865');
});

test('ingest skips malformed payloads and bad scanner ids', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  assert.equal(a.ingest('hackrf', null), null);
  assert.equal(a.ingest('hackrf', payload(1, 5865, { frame_png_b64: '' })), null);
  assert.equal(a.ingest('hackrf', payload(1, 5865, { frame_png_b64: Buffer.from('not a png').toString('base64') })), null);
  assert.equal(a.ingest('hackrf', payload(0, 5865)), null);              // bad ts
  assert.equal(a.ingest('hackrf', payload('nope', 5865)), null);         // non-numeric ts
  assert.equal(a.ingest('hackrf', payload(1, NaN)), null);               // bad center
  assert.equal(a.ingest('../evil', payload(1, 5865)), null);             // path traversal
  assert.equal(a.ingest('', payload(1, 5865)), null);
  assert.equal(a.list().length, 0);
});

test('ingest dedupes the retained-message redelivery (same id → skip)', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  assert.ok(a.ingest('hackrf', payload(100, 5865)));
  assert.equal(a.ingest('hackrf', payload(100, 5865)), null);
  assert.equal(a.list().length, 1);
});

test('list: newest-first with scanner/since/limit filters and url', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  a.ingest('hackrf', payload(100, 5865));
  a.ingest('bladerf', payload(200, 5800));
  a.ingest('hackrf', payload(300, 5745));
  const all = a.list();
  assert.deepEqual(all.map((f) => f.ts), [300, 200, 100]);            // newest first
  assert.equal(all[0].url, '/api/frames/hackrf/300000_5745.png');
  assert.deepEqual(a.list({ scanner: 'hackrf' }).map((f) => f.ts), [300, 100]);
  assert.deepEqual(a.list({ since: 100 }).map((f) => f.ts), [300, 200]);   // strictly newer
  assert.equal(a.list({ limit: 1 }).length, 1);
  assert.equal(a.list({ limit: 1 })[0].ts, 300);
});

test('filePath: serves only well-formed, indexed, on-disk ids', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  a.ingest('hackrf', payload(100, 5865));
  const p = a.filePath('hackrf', '100000_5865.png');
  assert.ok(p && existsSync(p));
  assert.deepEqual(readFileSync(p), PNG);
  assert.equal(a.filePath('hackrf', '999999_5865.png'), null);        // not indexed
  assert.equal(a.filePath('..', '100000_5865.png'), null);            // traversal
  assert.equal(a.filePath('hackrf', '../../etc/passwd'), null);       // traversal
  assert.equal(a.filePath('hackrf', '100000_5865.PNG'), null);        // strict name
});

test('prune drops old entries AND deletes their files, persists, returns count', () => {
  const dir = tmp();
  const idx = join(dir, 'i.json');
  const a = new FrameArchive({ dir, indexFile: idx });
  a.ingest('hackrf', payload(1000, 5865));                  // old
  a.ingest('hackrf', payload(2000, 5800));                  // fresh
  const oldFile = join(dir, 'hackrf', '1000000_5865.png');
  assert.ok(existsSync(oldFile));
  // now = 2000s in ms; maxAge = 500s → cutoff 1500s: drops ts=1000, keeps ts=2000
  const removed = a.prune(2000 * 1000, 500 * 1000);
  assert.equal(removed, 1);
  assert.ok(!existsSync(oldFile));                          // file deleted, not just the entry
  assert.deepEqual(a.list().map((f) => f.ts), [2000]);
  assert.equal(JSON.parse(readFileSync(idx, 'utf8')).length, 1);
  assert.equal(a.prune(2000 * 1000, 500 * 1000), 0);        // idempotent
});

test('reloads the persisted index; filePath works after reload', () => {
  const dir = tmp();
  const idx = join(dir, 'i.json');
  new FrameArchive({ dir, indexFile: idx }).ingest('hackrf', payload(100, 5865));
  const b = new FrameArchive({ dir, indexFile: idx });
  assert.equal(b.list().length, 1);
  assert.ok(b.filePath('hackrf', '100000_5865.png'));
  assert.equal(b.ingest('hackrf', payload(100, 5865)), null);   // dedupe survives restart
});

test('count cap evicts the oldest entry and its file', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json'), max: 2 });
  a.ingest('hackrf', payload(100, 5865));
  a.ingest('hackrf', payload(200, 5800));
  a.ingest('hackrf', payload(300, 5745));
  assert.deepEqual(a.list().map((f) => f.ts), [300, 200]);
  assert.ok(!existsSync(join(dir, 'hackrf', '100000_5865.png')));
});

test('list: until/before/fmin/fmax/snrMin/standard filters + combos', () => {
  const dir = tmp();
  const a = new FrameArchive({ dir, indexFile: join(dir, 'i.json') });
  a.ingest('hackrf', payload(100, 949, { standard: 'PAL', sync_snr_db: 26 }));
  a.ingest('hackrf', payload(200, 5865, { standard: 'NTSC', sync_snr_db: 9 }));
  a.ingest('hackrf', payload(300, 5745, { standard: 'PAL', sync_snr_db: null }));
  assert.deepEqual(a.list({ until: 200 }).map((f) => f.ts), [200, 100]);        // inclusive
  assert.deepEqual(a.list({ before: 200 }).map((f) => f.ts), [100]);            // strict cursor
  assert.deepEqual(a.list({ since: 100, until: 250 }).map((f) => f.ts), [200]);
  assert.deepEqual(a.list({ fmin: 5000, fmax: 6100 }).map((f) => f.ts), [300, 200]);
  assert.deepEqual(a.list({ fmax: 1000 }).map((f) => f.ts), [100]);
  assert.deepEqual(a.list({ snrMin: 10 }).map((f) => f.ts), [100]);             // 9 < 10; null excluded
  assert.deepEqual(a.list({ standard: 'pal' }).map((f) => f.ts), [300, 100]);   // case-insensitive
  assert.deepEqual(a.list({ fmin: 5000, standard: 'PAL' }).map((f) => f.ts), [300]); // combo
  assert.equal(a.list({}).length, 3);                                           // all unset = everything
});
