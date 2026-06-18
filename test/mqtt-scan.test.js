import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emptyStore, reduce } from '../dashboard/public/mqtt-scan.js';

test('reduce ignores unknown/malformed topics', () => {
  assert.deepEqual(reduce(emptyStore(), 'fpv/x/other', '{}'), {});
  assert.deepEqual(reduce(emptyStore(), 'nope', '{}'), {});
});

test('reduce sets presence from status', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/status', JSON.stringify({ online: true, ts: 5 }));
  assert.equal(s.hackrf.online, true);
  assert.equal(s.hackrf.status_ts, 5);
});

test('reduce stores the detection payload', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/detection',
    JSON.stringify({ ts: 9, detections: [{ band: '5.8G', class: 'analog' }], occupancy: { '5.8G': 0.4 } }));
  assert.equal(s.hackrf.detection.detections[0].class, 'analog');
  assert.equal(s.hackrf.detection.occupancy['5.8G'], 0.4);
});

test('reduce captures self-describing bands + latest psd + waterfall frame', () => {
  const s = emptyStore();
  reduce(s, 'fpv/hackrf/spectrum',
    JSON.stringify({ ts: 1, bands: [{ id: '5.8G', low_mhz: 5645, high_mhz: 5945, psd: [-90, -50] }] }));
  assert.deepEqual(s.hackrf.bands['5.8G'], { low_mhz: 5645, high_mhz: 5945 });
  assert.deepEqual(s.hackrf.latestPsd['5.8G'], [-90, -50]);
  assert.equal(s.hackrf.waterfalls['5.8G'].length, 1);
});

test('reduce caps the waterfall ring buffer at depth (oldest dropped)', () => {
  const s = emptyStore();
  for (let i = 0; i < 10; i += 1) {
    reduce(s, 'fpv/hackrf/spectrum',
      JSON.stringify({ ts: i, bands: [{ id: '5.8G', low_mhz: 5645, high_mhz: 5945, psd: [i] }] }), { depth: 3 });
  }
  const buf = s.hackrf.waterfalls['5.8G'];
  assert.equal(buf.length, 3);
  assert.deepEqual(buf.map((f) => f.ts), [7, 8, 9]);
});

test('reduce swallows malformed JSON', () => {
  assert.deepEqual(reduce(emptyStore(), 'fpv/hackrf/detection', '{not json'), {});
});

test('reduce accepts an already-parsed object payload', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/status', { online: false, ts: 2 });
  assert.equal(s.hackrf.online, false);
});
