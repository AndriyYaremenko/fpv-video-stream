import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { diffDetections, DetectionJournal } from '../lib/detection-journal.js';

function payload(ts, dets) { return { scanner_id: 'hackrf', ts, detections: dets, occupancy: {} }; }

test('diffDetections: baseline yields no events', () => {
  const { events, current } = diffDetections(new Map(), 'hackrf',
    payload(1, [{ band: '5.8G', center_mhz: 5800, channel: 'F4', class: 'analog', snr_db: 28, power_dbm: -47 }]), true);
  assert.equal(events.length, 0);
  assert.ok(current.has('5.8G:F4'));
});

test('diffDetections: appeared + gone', () => {
  const prev = new Map([['5.8G:F4', { band: '5.8G', center_mhz: 5800, channel: 'F4', class: 'analog', snr_db: 28, power_dbm: -47 }]]);
  const { events } = diffDetections(prev, 'hackrf',
    payload(9, [{ band: '5.8G', center_mhz: 5769, channel: 'R4', class: 'digital', snr_db: 22, power_dbm: -50 }]), false);
  const kinds = events.map((e) => `${e.event}:${e.channel}`).sort();
  assert.deepEqual(kinds, ['appeared:R4', 'gone:F4']);
  const gone = events.find((e) => e.event === 'gone');
  assert.equal(gone.center_mhz, 5800);        // gone carries the prior detection's fields
});

test('DetectionJournal: ingest logs changes, newest-first, capped, persists', () => {
  const dir = mkdtempSync(join(tmpdir(), 'jr-'));
  const file = join(dir, 'detections.json');
  const j = new DetectionJournal({ file, max: 3 });
  j.ingest('hackrf', payload(1, [{ band: '5.8G', channel: 'F4', center_mhz: 5800, class: 'analog', snr_db: 28 }]));  // baseline, no events
  j.ingest('hackrf', payload(2, [{ band: '5.8G', channel: 'R1', center_mhz: 5658, class: 'analog', snr_db: 20 }]));  // F4 gone + R1 appeared
  const evs = j.events(10);
  assert.equal(evs.length, 2);
  assert.ok(evs[0].ts >= evs[1].ts);          // newest first
  j.ingest('hackrf', payload(3, [{ band: '5.8G', channel: 'R2', center_mhz: 5695, class: 'analog', snr_db: 20 }]));  // R1 gone + R2 appeared
  assert.equal(j.events(99).length, 3);       // capped at max=3
  const reloaded = new DetectionJournal({ file, max: 3 });
  assert.equal(reloaded.events(99).length, 3);
  assert.equal(JSON.parse(readFileSync(file, 'utf8')).length, 3);
});

test('diffDetections: ignores null/garbage detection entries', () => {
  const { events, current } = diffDetections(new Map(), 'hackrf',
    payload(1, [null, 42, { band: '5.8G', channel: 'F4', center_mhz: 5800, class: 'analog', snr_db: 28 }]), true);
  assert.equal(events.length, 0);          // baseline
  assert.equal(current.size, 1);           // only the valid object kept
  assert.ok(current.has('5.8G:F4'));
});

test('DetectionJournal: bounds tracked scanners (LRU evict, re-baselines)', () => {
  const j = new DetectionJournal({ file: '', max: 100, maxScanners: 2 });
  j.ingest('a', payload(1, [{ band: '5.8G', channel: 'F1', center_mhz: 5740, class: 'analog' }]));  // baseline a
  j.ingest('b', payload(1, [{ band: '5.8G', channel: 'F2', center_mhz: 5760, class: 'analog' }]));  // baseline b
  j.ingest('c', payload(1, [{ band: '5.8G', channel: 'F3', center_mhz: 5780, class: 'analog' }]));  // baseline c -> evicts a
  const evs = j.ingest('a', payload(2, [{ band: '5.8G', channel: 'F4', center_mhz: 5800, class: 'analog' }]));
  assert.equal(evs.length, 0);             // 'a' evicted -> fresh baseline, no spurious events
});
