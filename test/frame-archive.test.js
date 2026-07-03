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
