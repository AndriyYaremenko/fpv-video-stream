import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  emptyViewer, applyDetections, seedFromJournal, viewerRows,
  pickViewer, pickRxScanner, viewStream, ageLabel, RECENT_TTL_S, LIVE_STALE_S,
  viewerListHtml,
} from '../dashboard/public/viewer.js';

const det = (over = {}) => ({
  band: '5.8G', center_mhz: 5865, channel: 'A1', class: 'analog',
  snr_db: 18, power_dbm: -50, ...over,
});

test('applyDetections merges the same signal from two scanners into one row', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', { ts: 100, detections: [det()] }, 100);
  applyDetections(vs, 'hackrf', { ts: 101, detections: [det({ center_mhz: 5864.2 })] }, 101);
  const rows = viewerRows(vs, 102);
  assert.equal(rows.length, 1);
  assert.deepEqual(Object.keys(rows[0].scanners).sort(), ['bladerf', 'hackrf']);
  assert.equal(rows[0].live, true);
});

test('applyDetections is idempotent for the same payload ts', () => {
  const vs = emptyViewer();
  const payload = { ts: 100, detections: [det()] };
  applyDetections(vs, 'bladerf', payload, 100);
  applyDetections(vs, 'bladerf', payload, 150);
  assert.equal(viewerRows(vs, 150).length, 1);
});

test('a detection missing from the next cycle goes recent, then expires after TTL', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', { ts: 100, detections: [det()] }, 100);
  applyDetections(vs, 'bladerf', { ts: 140, detections: [] }, 140);
  let rows = viewerRows(vs, 141);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].live, false);                    // dimmed but clickable
  rows = viewerRows(vs, 100 + RECENT_TTL_S + 1);
  assert.equal(rows.length, 0);                         // expired
});

test('a stale claim from a dead scanner does not keep an entry live', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', { ts: 100, detections: [det()] }, 100);
  const rows = viewerRows(vs, 100 + LIVE_STALE_S + 1);  // scanner went silent
  assert.equal(rows.length, 1);
  assert.equal(rows[0].live, false);
});

test('viewerRows sorts live-by-power then recent-by-freshness', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', {
    ts: 100,
    detections: [
      det({ band: '1.2G', center_mhz: 1280, channel: null, power_dbm: -70 }),
      det({ band: '4.9G', center_mhz: 4240, channel: null, power_dbm: -40 }),
    ],
  }, 100);
  seedFromJournal(vs, [
    { ts: 90, scanner_id: 'hackrf', event: 'gone', band: '2.4G', center_mhz: 2450, channel: null, class: 'digital', snr_db: 9 },
    { ts: 60, scanner_id: 'hackrf', event: 'gone', band: '3.3G', center_mhz: 3470, channel: null, class: 'analog', snr_db: 12 },
  ], 100);
  const rows = viewerRows(vs, 101);
  assert.deepEqual(rows.map((r) => r.center_mhz), [4240, 1280, 2450, 3470]);
  assert.deepEqual(rows.map((r) => r.live), [true, true, false, false]);
});

test('seedFromJournal keeps only fresh gone events and never overwrites live entries', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'bladerf', { ts: 100, detections: [det()] }, 100);
  seedFromJournal(vs, [
    { ts: 95, scanner_id: 'bladerf', event: 'gone', band: '5.8G', center_mhz: 5865, channel: 'A1', class: 'analog', snr_db: 5 },
    { ts: 100 - RECENT_TTL_S - 5, scanner_id: 'bladerf', event: 'gone', band: '1.2G', center_mhz: 1280, channel: null, class: 'analog', snr_db: 7 },
    { ts: 99, scanner_id: 'bladerf', event: 'appeared', band: '3.3G', center_mhz: 3470, channel: null, class: 'analog', snr_db: 8 },
  ], 100);
  const rows = viewerRows(vs, 100);
  assert.equal(rows.length, 1);                 // live 5865 kept (snr 18, not 5); stale+appeared skipped
  assert.equal(rows[0].snr_db, 18);
});

test('pickViewer wants an ONLINE scanner with a view state, idle preferred', () => {
  assert.equal(pickViewer({}), null);
  const store = {
    bladerf: { online: true, view: null, rxtune: null },
    hackrf: { online: true, view: { active: true, stream: 'hackrf-view' }, rxtune: {} },
  };
  assert.equal(pickViewer(store), 'hackrf');
  store.second = { online: true, view: { active: false, stream: 's2-view' } };
  assert.equal(pickViewer(store), 'second');    // idle wins over busy
  store.hackrf.online = false;
  store.second.online = false;
  assert.equal(pickViewer(store), null);        // offline viewers don't count
});

test('pickRxScanner finds the online scanner driving an RX5808', () => {
  assert.equal(pickRxScanner({ a: { online: true, rxtune: null } }), null);
  assert.equal(pickRxScanner({ a: { online: true, rxtune: { freq_mhz: 5865 } } }), 'a');
});

test('viewStream falls back to <id>-view for old agents', () => {
  assert.equal(viewStream({ h: { view: { stream: 'custom' } } }, 'h'), 'custom');
  assert.equal(viewStream({ h: { view: { stream: null } } }, 'h'), 'h-view');
});

test('ageLabel', () => {
  assert.equal(ageLabel(100, 70), 'щойно');
  assert.equal(ageLabel(400, 100), '5 хв тому');
});

test('applyDetections never freezes a scanner on missing/zero payload ts', () => {
  const vs = emptyViewer();
  applyDetections(vs, 'b', { ts: 0, detections: [det()] }, 100);
  applyDetections(vs, 'b', { ts: 0, detections: [det({ band: '1.2G', center_mhz: 1280, channel: null })] }, 130);
  const rows = viewerRows(vs, 131);
  assert.equal(rows.length, 2);                       // the second zero-ts payload was NOT dropped
  assert.equal(rows.filter((r) => r.live).length, 1); // 1280 live; 5865 lost its claim, now recent
});

test('viewerListHtml renders clickable rows with band/freq data attrs', () => {
  const rows = [
    { key: 'k1', band: '4.9G', center_mhz: 4240, channel: null, class: 'analog',
      snr_db: 22, power_dbm: -40, scanners: { bladerf: 100 }, seen_by: { bladerf: true },
      last_seen: 100, live: true },
    { key: 'k2', band: '5.8G', center_mhz: 5865, channel: 'A1', class: 'analog',
      snr_db: 18, power_dbm: -50, scanners: {}, seen_by: { bladerf: true, hackrf: true },
      last_seen: 40, live: false },
  ];
  const html = viewerListHtml(rows, 100, 4240, true);
  assert.match(html, /data-vwfreq="4240" data-vwband="4\.9G"/);
  assert.match(html, /data-vwfreq="5865" data-vwband="5\.8G"/);
  assert.match(html, /is-viewing/);              // 4240 row highlighted (active view)
  assert.match(html, /vw-recent/);               // 5865 row dimmed
  assert.match(html, /5865 МГц \(A1\)/);
  assert.match(html, /1 хв тому/);
  assert.match(html, /bladerf/);
  assert.doesNotMatch(html, /SDR view недоступний/);
});

test('viewerListHtml without a viewer shows the hint and no play markers', () => {
  const rows = [{ key: 'k', band: '5.8G', center_mhz: 5865, channel: null, class: 'analog',
    snr_db: 18, power_dbm: -50, scanners: { b: 100 }, seen_by: { b: true }, last_seen: 100, live: true }];
  const html = viewerListHtml(rows, 100, null, false);
  assert.match(html, /SDR view недоступний/);
  assert.doesNotMatch(html, /▶/);
});

test('viewerListHtml with no rows renders the empty note', () => {
  assert.match(viewerListHtml([], 100), /детекцій немає/);
});
