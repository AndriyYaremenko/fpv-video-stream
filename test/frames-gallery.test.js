import { test } from 'node:test';
import assert from 'node:assert/strict';
import { galleryHtml, scannerIds, frameCaption, BAND_PRESETS, buildFramesQuery, toLocalDatetime, scannerOptions } from '../dashboard/public/frames-gallery.js';

const fr = (over = {}) => ({
  id: 'hackrf/1751500000000_5865', scanner_id: 'hackrf', ts: 1751500000,
  center_mhz: 5865, standard: 'PAL', sync_snr_db: 12.5,
  url: '/api/frames/hackrf/1751500000000_5865.png', ...over,
});

test('scannerIds: unique + sorted', () => {
  assert.deepEqual(scannerIds([fr(), fr({ scanner_id: 'bladerf' }), fr()]), ['bladerf', 'hackrf']);
  assert.deepEqual(scannerIds([]), []);
});

test('frameCaption: time, MHz, scanner, SNR; SNR omitted when null', () => {
  const cap = frameCaption(fr());
  assert.match(cap, /\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/);
  assert.match(cap, /5865 МГц · hackrf · sync 12\.5 dB/);
  assert.ok(!frameCaption(fr({ sync_snr_db: null })).includes('sync'));
});

test('galleryHtml: toolbar reflects the filter, tiles render all given frames', () => {
  const frames = [fr(), fr({ scanner_id: 'bladerf', url: '/api/frames/bladerf/1_1.png' })];
  const html = galleryHtml(frames, {
    filter: { scanner: 'bladerf', band: '5.8', standard: 'PAL', snrMin: '12', from: '2026-07-05T10:30', to: '' },
    scanners: ['rx-pi'],
  });
  assert.match(html, /id="frames-refresh"/);
  assert.match(html, /<option value="bladerf" selected>/);
  assert.match(html, /<option value="rx-pi">/);                       // registry id present
  assert.match(html, /<option value="5.8" selected>/);                // band
  assert.match(html, /<option value="PAL" selected>/);                // standard
  assert.match(html, /id="frames-snr"[^>]*value="12"/);
  assert.match(html, /id="frames-from"[^>]*value="2026-07-05T10:30"/);
  assert.match(html, /data-tp="24h"/);
  assert.ok(html.includes('/api/frames/hackrf/'));                    // NO client-side tile filtering
  assert.ok(html.includes('/api/frames/bladerf/'));
  assert.match(html, /<img loading="lazy" src="\/api\/frames\/hackrf\/1751500000000_5865\.png"/);
});

test('galleryHtml: «Показати ще» only when hasMore', () => {
  assert.match(galleryHtml([fr()], { hasMore: true }), /id="frames-more"/);
  assert.ok(!galleryHtml([fr()], { hasMore: false }).includes('frames-more'));
  assert.ok(!galleryHtml([fr()]).includes('frames-more'));
});

test('galleryHtml: empty state + html escaping', () => {
  assert.match(galleryHtml([]), /Кадрів немає\./);
  const html = galleryHtml([fr({ scanner_id: '<x&>' })]);
  assert.ok(!html.includes('<x&>'));
  assert.ok(html.includes('&lt;x&amp;&gt;'));
});

test('buildFramesQuery: defaults, band mapping, snr, before/limit', () => {
  assert.equal(buildFramesQuery({}), 'limit=200');
  const q = new URLSearchParams(buildFramesQuery(
    { scanner: 'hackrf', band: '5.8', standard: 'PAL', snrMin: '12' },
    { limit: 200, before: 123.5 }));
  assert.equal(q.get('scanner'), 'hackrf');
  assert.equal(q.get('fmin'), '5000');
  assert.equal(q.get('fmax'), '6100');
  assert.equal(q.get('standard'), 'PAL');
  assert.equal(q.get('snr_min'), '12');
  assert.equal(q.get('before'), '123.5');
  assert.equal(buildFramesQuery({ band: 'nope', snrMin: '0' }), 'limit=200'); // unknown band + snr 0 ignored
});

test('buildFramesQuery: from/to → since/until epoch (TZ-independent roundtrip)', () => {
  const from = '2026-07-05T10:30';
  const q = new URLSearchParams(buildFramesQuery({ from, to: '' }));
  assert.equal(q.get('since'), String(Math.floor(new Date(from).getTime() / 1000)));
  assert.equal(q.get('until'), null);
});

test('toLocalDatetime: format + roundtrips through Date at minute precision', () => {
  const ms = 1783111522000;
  const s = toLocalDatetime(ms);
  assert.match(s, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
  assert.equal(new Date(s).getTime(), Math.floor(ms / 60000) * 60000);
});

test('scannerOptions: union of registry, frames and selection, sorted', () => {
  const frames = [fr(), fr({ scanner_id: 'bladerf' })];
  assert.deepEqual(scannerOptions(frames, ['rx-pi'], 'zeta'), ['bladerf', 'hackrf', 'rx-pi', 'zeta']);
  assert.deepEqual(scannerOptions([], [], ''), []);
});

test('BAND_PRESETS ranges match the spec', () => {
  assert.deepEqual(BAND_PRESETS['0.9'], { fmin: 800, fmax: 1000, label: '0.9G (шум)' });
  assert.deepEqual(BAND_PRESETS['2.4'], { fmin: 2200, fmax: 2700, label: '2.4G' });
});
