import { test } from 'node:test';
import assert from 'node:assert/strict';
import { splitByKind, classColor, fmtFreq, fmtPct, psdToPoints, detectionX, psdColor, frameCaption, rxtuneCaption } from '../dashboard/public/spectrum.js';

test('splitByKind separates scanners from cameras (missing kind = camera)', () => {
  const { cameras, scanners } = splitByKind([
    { id: 'a', kind: 'camera' }, { id: 'b' }, { id: 's', kind: 'scanner' },
  ]);
  assert.deepEqual(cameras.map((d) => d.id), ['a', 'b']);
  assert.deepEqual(scanners.map((d) => d.id), ['s']);
});

test('frameCaption combines standard, freq, snr, time', () => {
  const cap = frameCaption({ standard: 'PAL', center_mhz: 5800, sync_snr_db: 18.3, ts: 1718700000 });
  assert.match(cap, /PAL/);
  assert.match(cap, /5800/);
  assert.match(cap, /18\.3/);
  assert.match(cap, /\d{2}:\d{2}:\d{2}/);     // HH:MM:SS, tz-independent shape
});

test('frameCaption tolerates missing snr and ts', () => {
  const cap = frameCaption({ standard: 'NTSC', center_mhz: 1200 });
  assert.match(cap, /NTSC/);
  assert.match(cap, /1200/);
  assert.doesNotMatch(cap, /SNR/);
});

test('frameCaption is empty for nullish input', () => {
  assert.equal(frameCaption(null), '');
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

test('detectionX maps center freq within an explicit band range and clamps', () => {
  assert.ok(Math.abs(detectionX(5795, 5645, 5945, 300) - 150) < 1); // mid -> 150
  assert.equal(detectionX(1000, 5645, 5945, 300), 0);               // below -> 0
  assert.equal(detectionX(9999, 5645, 5945, 300), 300);             // above -> width
});

test('psdColor clamps below/above the range to the endpoint colors', () => {
  assert.equal(psdColor(-200), psdColor(-100));   // clamp low
  assert.equal(psdColor(0), psdColor(-20));        // clamp high
});

test('psdColor returns an rgb() string and varies with power', () => {
  assert.match(psdColor(-60), /^rgb\(\d+, ?\d+, ?\d+\)$/);
  assert.notEqual(psdColor(-90), psdColor(-30));
});

test('fmtFreq formats MHz with a Ukrainian unit and rounds', () => {
  assert.equal(fmtFreq(5800), '5800 МГц');
  assert.equal(fmtFreq(5800.4), '5800 МГц');
});

test('rxtuneCaption shows freq, channel, mode', () => {
  const c = rxtuneCaption({ freq_mhz: 5865, channel: 'A1', mode: 'scan' });
  assert.match(c, /RX5808/);
  assert.match(c, /5865/);
  assert.match(c, /A1/);
  assert.match(c, /scan/);
});

test('rxtuneCaption is empty for nullish input', () => {
  assert.equal(rxtuneCaption(null), '');
});
