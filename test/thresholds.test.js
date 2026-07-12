import { test } from 'node:test';
import assert from 'node:assert/strict';
import { THRESHOLD_FIELDS, clampThreshold, scannerThresholdCards } from '../dashboard/public/thresholds.js';

test('THRESHOLD_FIELDS has the five keys with ranges + labels', () => {
  const keys = THRESHOLD_FIELDS.map((f) => f.key);
  assert.deepEqual(keys, ['snr_threshold_db', 'min_bandwidth_mhz', 'occupancy_snr_db', 'carrier_snr_db', 'carrier_min_bw_mhz']);
  const snr = THRESHOLD_FIELDS.find((f) => f.key === 'snr_threshold_db');
  assert.equal(snr.lo, 3); assert.equal(snr.hi, 60); assert.ok(snr.label);
});

test('clampThreshold clamps to the field range; non-number -> null', () => {
  assert.equal(clampThreshold('snr_threshold_db', 999), 60);
  assert.equal(clampThreshold('min_bandwidth_mhz', -1), 0.1);
  assert.equal(clampThreshold('snr_threshold_db', 'x'), null);
  assert.equal(clampThreshold('bogus', 5), null);
});

test('scannerThresholdCards lists online scanners with their scancfg', () => {
  const store = {
    bladerf: { online: true, view: {}, scancfg: { snr_threshold_db: 12 } },
    hackrf: { online: false, scancfg: { snr_threshold_db: 20 } },
    cam: { online: true },  // no scancfg -> still listed? no: only scanners with scancfg OR online scanner
  };
  const cards = scannerThresholdCards(store);
  assert.deepEqual(cards.map((c) => c.id), ['bladerf']);   // online + has scancfg
  assert.equal(cards[0].scancfg.snr_threshold_db, 12);
});
