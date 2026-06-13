#!/usr/bin/env node
// bin/add-device.js — add a device, regenerate config, reload MediaMTX, print push commands.
import { writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { loadRegistry, saveRegistry, ensureReadUser, ensurePublishSecrets, addDevice } from '../lib/registry.js';
import { renderConfig } from '../lib/render-config.js';
import { buildRtspPush, buildSrtPush } from '../lib/push-command.js';

const [, , id, name, location] = process.argv;
if (!id) {
  console.error('Usage: node bin/add-device.js <device-id> "<friendly name>" "<location>"');
  process.exit(2);
}

const env = process.env;
const devicesFile = env.DEVICES_FILE || 'devices.yml';
const outFile = env.MEDIAMTX_CONFIG || 'mediamtx.yml';
const wgIp = env.WG_IP || '10.8.0.1';

const reg = loadRegistry(devicesFile);
ensureReadUser(reg);
ensurePublishSecrets(reg);
let device;
try {
  device = addDevice(reg, { id, name, location });
} catch (e) {
  console.error(`Error: ${e.message}`);
  process.exit(1);
}
saveRegistry(devicesFile, reg);

const opts = {
  wgIp,
  rtspPort: Number(env.RTSP_PORT || 8554),
  srtPort: Number(env.SRT_PORT || 8890),
  webrtcPort: Number(env.WEBRTC_PORT || 8889),
  iceUdpPort: Number(env.ICE_UDP_PORT || 8189),
  apiHost: env.API_HOST || '127.0.0.1',
  apiPort: Number(env.API_PORT || 9997),
};
writeFileSync(outFile, renderConfig(reg, opts), 'utf8');

// Reload MediaMTX so the new publish user takes effect (no-op failure on dev machines).
let reloaded = false;
try { execFileSync('systemctl', ['reload-or-restart', 'mediamtx'], { stdio: 'ignore' }); reloaded = true; } catch { /* not on the server */ }

const pushOpts = {
  wgIp,
  rtspPort: opts.rtspPort,
  srtPort: opts.srtPort,
  videoDevice: env.PI_VIDEO_DEVICE || '/dev/video0',
  framerate: Number(env.PI_FRAMERATE || 30),
  videoSize: env.PI_VIDEO_SIZE || '720x576',
  bitrate: env.PI_BITRATE || '2M',
};

console.log(`\n✅ Added device "${device.id}" (${device.name}).`);
console.log(`   Registry: ${devicesFile}   Config: ${outFile}   MediaMTX reload: ${reloaded ? 'done' : 'SKIPPED (run on server)'}`);
console.log(`\n🔑 Publish password: ${device.publish_pass}`);
console.log(`\n▶ Pi 5 push — RTSP (software x264):\n${buildRtspPush(device, pushOpts)}`);
console.log(`\n▶ Pi 5 push — SRT (alternative):\n${buildSrtPush(device, pushOpts)}\n`);
