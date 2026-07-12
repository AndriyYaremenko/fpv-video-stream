import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emptyStore, reduce, buildCommand, buildViewCommand } from '../dashboard/public/mqtt-scan.js';

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

test('reduce stores the latest video frame', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/video', JSON.stringify({
    ts: 1718700000, center_mhz: 5800, standard: 'PAL', line_hz: 15625,
    sync_snr_db: 18.3, frame_png_b64: 'QUJD',
  }));
  assert.equal(s.hackrf.video.standard, 'PAL');
  assert.equal(s.hackrf.video.center_mhz, 5800);
  assert.equal(s.hackrf.video.line_hz, 15625);
  assert.equal(s.hackrf.video.sync_snr_db, 18.3);
  assert.equal(s.hackrf.video.frame_png_b64, 'QUJD');
});

test('reduce video defaults frame to empty string when missing', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/video', JSON.stringify({ ts: 1, standard: 'NTSC' }));
  assert.equal(s.hackrf.video.frame_png_b64, '');
});

test('reduce stores the rxtune state', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/rxtune', JSON.stringify({
    ts: 5, freq_mhz: 5865, channel: 'A1', mode: 'detected', targets: [5865, 5800],
  }));
  assert.equal(s.hackrf.rxtune.freq_mhz, 5865);
  assert.equal(s.hackrf.rxtune.channel, 'A1');
  assert.equal(s.hackrf.rxtune.mode, 'detected');
  assert.deepEqual(s.hackrf.rxtune.targets, [5865, 5800]);
});

test('buildCommand shapes mode + channel', () => {
  assert.deepEqual(buildCommand('manual', 'A1'), { mode: 'manual', channel: 'A1' });
  assert.deepEqual(buildCommand('scan'), { mode: 'scan', channel: null });
});

test('reduce: fpv/<id>/view updates the view state incl. stream', () => {
  const s = reduce(emptyStore(), 'fpv/hackrf/view', JSON.stringify({
    scanner_id: 'hackrf', ts: 5, active: true, freq_mhz: 5865, until_ts: 605,
    error: null, stream: 'hackrf-view',
  }));
  assert.deepEqual(s.hackrf.view,
    { ts: 5, active: true, freq_mhz: 5865, until_ts: 605, error: null, stream: 'hackrf-view', bandwidth_mhz: null });
  reduce(s, 'fpv/hackrf/view', JSON.stringify({ ts: 6, active: false, error: 'ffmpeg exited' }));
  assert.equal(s.hackrf.view.active, false);
  assert.equal(s.hackrf.view.freq_mhz, null);
  assert.equal(s.hackrf.view.stream, null);       // absent field -> null (old agents)
  assert.equal(s.hackrf.view.error, 'ffmpeg exited');
});

test('reduce view carries bandwidth_mhz (null when absent)', () => {
  const store = {};
  reduce(store, 'fpv/hackrf/view', JSON.stringify({ ts: 1, active: true, freq_mhz: 5865, stream: 'hackrf-view', bandwidth_mhz: 2.5 }));
  assert.equal(store.hackrf.view.bandwidth_mhz, 2.5);
  reduce(store, 'fpv/bladerf/view', JSON.stringify({ ts: 1, active: false, stream: 'bladerf-view' }));
  assert.equal(store.bladerf.view.bandwidth_mhz, null);
});

test('buildViewCommand: start carries freq, stop does not', () => {
  assert.deepEqual(buildViewCommand('start', 5865), { view: 'start', freq_mhz: 5865 });
  assert.deepEqual(buildViewCommand('stop'), { view: 'stop' });
});

test('buildViewCommand carries bandwidth_mhz when given, omits when not', () => {
  assert.deepEqual(buildViewCommand('start', 5865, 3), { view: 'start', freq_mhz: 5865, bandwidth_mhz: 3 });
  assert.deepEqual(buildViewCommand('start', 5865), { view: 'start', freq_mhz: 5865 });
  assert.deepEqual(buildViewCommand('start', 5865, ''), { view: 'start', freq_mhz: 5865 }); // empty -> omit
  assert.deepEqual(buildViewCommand('stop'), { view: 'stop' });
});

test('telemetry message reduces into store[id].telemetry', () => {
  const store = {};
  reduce(store, 'fpv/bladerf/telemetry', JSON.stringify({
    node_id: 'bladerf', ts: 1752200000, cpu_temp_c: 62.4, cpu_load_pct: 38,
    mem_used_mb: 1200, mem_total_mb: 4096, mem_used_pct: 29, disk_used_pct: 47,
    uptime_s: 123456, throttled: false, throttled_ever: true, throttle_flags: '0x50000',
  }));
  const t = store.bladerf.telemetry;
  assert.equal(t.cpu_temp_c, 62.4);
  assert.equal(t.mem_used_pct, 29);
  assert.equal(t.throttled_ever, true);
  assert.equal(t.throttle_flags, '0x50000');
});

test('telemetry reduce ignores malformed payload', () => {
  const store = {};
  reduce(store, 'fpv/bladerf/telemetry', 'not json');
  assert.equal(store.bladerf === undefined || store.bladerf.telemetry === null, true);
});
