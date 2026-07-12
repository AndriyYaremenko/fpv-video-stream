// dashboard/public/fixtures.js — DEV ONLY sample data for ?preview=1.
// Timestamps are relative to Date.now() so the FPV Viewer's TTL-based merged list
// (RECENT_TTL_S=300, LIVE_STALE_S=120) keeps entries live/recent at render time.
const psd = (n, base) => Array.from({length:n}, (_,i) => base + 8*Math.sin(i/4) - (i%7===0?18:0) + (i%13)*0.7);
const NOW = Math.floor(Date.now() / 1000);
export const FIXTURES = {
  config: { webrtcBase:'', readUser:'read', readPass:'x' },
  operator: 'operator_042',
  devices: [
    { id:'cam-north', name:'Вхідні ворота', location:'Периметр — Північ', kind:'camera', online:true, bitrateKbps:2100, uptimeSec:5400, readers:2 },
    { id:'cam-yard', name:'Двір', location:'Периметр — Схід', kind:'camera', online:false },
    { id:'bladerf', name:'Сканер bladeRF', location:'Дах', kind:'scanner', node:'bladerf', online:true, uptimeSec:9000 },
    { id:'hackrf', name:'HackRF', location:'Дах', kind:'scanner', node:'bladerf', online:true, uptimeSec:9000 },
    { id:'hackrf-view', name:'SDR (hackrf) Viewer', location:'Дах', kind:'camera', node:'bladerf', online:true, bitrateKbps:800, uptimeSec:9000, readers:1 },
  ],
  detections: [
    { ts:NOW-5, scanner_id:'bladerf', band:'5.8G', center_mhz:5800, channel:'F4', class:'analog', snr_db:18, power_dbm:-42, event:'appeared' },
    { ts:NOW-90, scanner_id:'bladerf', band:'1.2G', center_mhz:1280, class:'digital', snr_db:12, power_dbm:-55, event:'gone' },
  ],
  frames: { frames: [
    { id:'bladerf/'+(NOW-5)+'_5800', scanner_id:'bladerf', ts:NOW-5, center_mhz:5800, standard:'PAL', line_hz:15625, sync_snr_db:18.3, url:'' },
  ] },
  scanStore: { bladerf: {
    online:true, status_ts:NOW,
    telemetry:{ ts:NOW, cpu_temp_c:62.4, cpu_load_pct:38, mem_used_mb:1200, mem_total_mb:4096, mem_used_pct:29, disk_used_pct:47, uptime_s:123456, throttled:false, throttled_ever:true, throttle_flags:'0x50000' },
    bands:{ '5.8G':{low_mhz:5645,high_mhz:5945}, '1.2G':{low_mhz:1080,high_mhz:1360}, '900M':{low_mhz:840,high_mhz:960} },
    latestPsd:{ '5.8G':psd(64,-70), '1.2G':psd(64,-80), '900M':psd(64,-75) }, waterfalls:{ '5.8G':[], '1.2G':[], '900M':[] },
    detection:{ ts:NOW, occupancy:{'5.8G':0.32,'1.2G':0.08,'900M':0.5}, detections:[
      { band:'5.8G',center_mhz:5800,class:'analog',power_dbm:-42,bandwidth_mhz:18,confidence:0.9,channel:'F4',snr_db:18 },
      { band:'900M',center_mhz:915,class:'digital',power_dbm:-55,bandwidth_mhz:10,confidence:0.7 } ] },
    video:{ ts:NOW-5, center_mhz:5800, standard:'PAL', line_hz:15625, sync_snr_db:18.3, frame_png_b64:'' },
    rxtune:{ ts:NOW, freq_mhz:5865, channel:'A1', mode:'scan', targets:[] },
    view:{ ts:NOW, active:true, freq_mhz:5800, until_ts:NOW+600, error:null, stream:'bladerf-view', bandwidth_mhz:3 },
    scancfg: { ts: NOW, snr_threshold_db: 20, min_bandwidth_mhz: 5, occupancy_snr_db: 10, carrier_snr_db: 15, carrier_min_bw_mhz: 0.5 },
  }
  , hackrf: {
    online: true, status_ts: NOW,
    bands: { '5.8G': { low_mhz: 5645, high_mhz: 5945 } },
    latestPsd: { '5.8G': psd(64, -72) }, waterfalls: { '5.8G': [] },
    detection: { ts: NOW, occupancy: { '5.8G': 0.2 }, detections: [
      { band: '5.8G', center_mhz: 5865, class: 'analog', power_dbm: -48, bandwidth_mhz: 17, confidence: 0.9, channel: 'A1', snr_db: 16 } ] },
    rxtune: { ts: NOW, freq_mhz: 5865, channel: 'A1', mode: 'manual', targets: [] },
    view: { ts: NOW, active: false, freq_mhz: null, until_ts: null, error: null, stream: 'hackrf-view' },
  }
  },
};
