import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitByKind, classColor, fmtPct, psdToPoints, detectionX, BAND_RANGES } from '../dashboard/public/spectrum.js';

test('splitByKind separates scanners from cameras (missing kind = camera)', () => {
  const { cameras, scanners } = splitByKind([
    { id: 'a', kind: 'camera' }, { id: 'b' }, { id: 's', kind: 'scanner' },
  ]);
  assert.deepEqual(cameras.map((d) => d.id), ['a', 'b']);
  assert.deepEqual(scanners.map((d) => d.id), ['s']);
});

test('classColor maps the three classes distinctly, unknown is the default', () => {
  assert.notEqual(classColor('analog'), classColor('digital'));
  assert.equal(classColor('whatever'), classColor('unknown'));
});

test('fmtPct rounds a fraction to a percentage and tolerates undefined', () => {
  assert.equal(fmtPct(0.5), '50%');
  assert.equal(fmtPct(0), '0%');
  assert.equal(fmtPct(undefined), '0%');
});

test('psdToPoints scales endpoints to the box', () => {
  const pts = psdToPoints([-100, -20], 100, 50, -100, -20);
  assert.equal(pts.length, 2);
  assert.equal(pts[0].x, 0);
  assert.equal(pts[1].x, 100);
  assert.ok(Math.abs(pts[0].y - 50) < 1e-9);   // -100 dBm -> bottom of box
  assert.ok(Math.abs(pts[1].y - 0) < 1e-9);    // -20 dBm -> top of box
});

test('psdToPoints clamps out-of-range power into the box', () => {
  const pts = psdToPoints([10, -300], 10, 40, -100, -20);
  for (const p of pts) assert.ok(p.y >= 0 && p.y <= 40);
});

test('detectionX maps center freq within band and clamps out-of-range', () => {
  assert.ok(Math.abs(detectionX(5795, '5.8G', 300) - 150) < 1); // band 5645..5945, mid -> 150
  assert.equal(detectionX(1000, '5.8G', 300), 0);               // below -> 0
  assert.equal(detectionX(9999, '5.8G', 300), 300);             // above -> width
});

test('BAND_RANGES covers the three FPV bands', () => {
  assert.deepEqual(Object.keys(BAND_RANGES).sort(), ['1.2G', '2.4G', '5.8G']);
});
