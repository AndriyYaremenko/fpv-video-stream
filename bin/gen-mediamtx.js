#!/usr/bin/env node
// bin/gen-mediamtx.js — render mediamtx.yml from the device registry.
import { writeFileSync } from 'node:fs';
import { loadRegistry, ensureReadUser, saveRegistry } from '../lib/registry.js';
import { renderConfig } from '../lib/render-config.js';

const env = process.env;
const devicesFile = env.DEVICES_FILE || 'devices.yml';
const outFile = env.MEDIAMTX_CONFIG || 'mediamtx.yml';

const reg = loadRegistry(devicesFile);
// Backfill read creds if the operator left placeholders; persist so they're stable.
const before = reg.read_pass;
ensureReadUser(reg);
if (reg.read_pass !== before) saveRegistry(devicesFile, reg);

const opts = {
  wgIp: env.WG_IP || '10.8.0.1',
  rtspPort: Number(env.RTSP_PORT || 8554),
  srtPort: Number(env.SRT_PORT || 8890),
  webrtcPort: Number(env.WEBRTC_PORT || 8889),
  iceUdpPort: Number(env.ICE_UDP_PORT || 8189),
  apiHost: env.API_HOST || '127.0.0.1',
  apiPort: Number(env.API_PORT || 9997),
};

writeFileSync(outFile, renderConfig(reg, opts), 'utf8');
console.log(`Wrote ${outFile} (${reg.devices.length} device(s)) bound to ${opts.wgIp}`);
