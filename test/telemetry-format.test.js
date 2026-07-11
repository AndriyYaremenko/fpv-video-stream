import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isFresh, fmtTemp, fmtMem, fmtPctVal, fmtUptimeShort, throttleState, TELEM_STALE_S } from '../dashboard/public/telemetry-format.js';

test('isFresh uses the 45s window', () => {
  assert.equal(TELEM_STALE_S, 45);
  assert.equal(isFresh(1000, 1040), true);
  assert.equal(isFresh(1000, 1046), false);
  assert.equal(isFresh(null, 1000), false);
});

test('fmtTemp / fmtPctVal / fmtMem render dashes for null', () => {
  assert.equal(fmtTemp(62.4), '62.4°C');
  assert.equal(fmtTemp(null), '—');
  assert.equal(fmtPctVal(38), '38%');
  assert.equal(fmtPctVal(null), '—');
  assert.equal(fmtMem({ mem_used_pct: 29, mem_total_mb: 4096 }), '29% (4.0G)');
  assert.equal(fmtMem(null), '—');
});

test('fmtUptimeShort', () => {
  assert.equal(fmtUptimeShort(90061), '1д 1г');
  assert.equal(fmtUptimeShort(3720), '1г 2хв');
  assert.equal(fmtUptimeShort(null), '—');
});

test('throttleState flags now vs ever vs clear', () => {
  assert.deepEqual(throttleState({ throttled: true, throttled_ever: true }), { text: '🔥 THROTTLED', warn: true });
  assert.deepEqual(throttleState({ throttled: false, throttled_ever: true }), { text: '⚠ був throttle', warn: true });
  assert.equal(throttleState({ throttled: false, throttled_ever: false }), null);
  assert.equal(throttleState(null), null);
});
