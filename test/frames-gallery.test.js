import { test } from 'node:test';
import assert from 'node:assert/strict';
import { galleryHtml, scannerIds, frameCaption } from '../dashboard/public/frames-gallery.js';

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

test('galleryHtml: tiles with data-src/data-cap, lazy img, refresh + select', () => {
  const html = galleryHtml([fr()]);
  assert.match(html, /id="frames-refresh"/);
  assert.match(html, /<select id="frames-scanner">/);
  assert.match(html, /class="fr-tile" data-src="\/api\/frames\/hackrf\/1751500000000_5865\.png"/);
  assert.match(html, /<img loading="lazy" src="\/api\/frames\/hackrf\/1751500000000_5865\.png"/);
  assert.match(html, /5865 МГц/);
});

test('galleryHtml: scanner filter narrows tiles, select keeps all + selected', () => {
  const frames = [fr(), fr({ scanner_id: 'bladerf', url: '/api/frames/bladerf/1_1.png' })];
  const html = galleryHtml(frames, 'bladerf');
  assert.ok(!html.includes('/api/frames/hackrf/'));           // hackrf tile filtered out
  assert.match(html, /<option value="hackrf">/);              // ...but still selectable
  assert.match(html, /<option value="bladerf" selected>/);
});

test('galleryHtml: empty state + html escaping', () => {
  assert.match(galleryHtml([]), /Кадрів немає\./);
  const html = galleryHtml([fr({ scanner_id: '<x&>' })]);
  assert.ok(!html.includes('<x&>'));
  assert.ok(html.includes('&lt;x&amp;&gt;'));
});
