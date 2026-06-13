import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRtspPush, buildSrtPush } from '../lib/push-command.js';

const device = { id: 'pi-01', publish_pass: 's3cr3t' };
const opts = { wgIp: '10.8.0.1', rtspPort: 8554, srtPort: 8890, videoDevice: '/dev/video0', framerate: 30, videoSize: '720x576', bitrate: '2M' };

test('rtsp push targets the device path with its credentials', () => {
  const cmd = buildRtspPush(device, opts);
  assert.match(cmd, /rtsp:\/\/pi-01:s3cr3t@10\.8\.0\.1:8554\/pi-01/);
  assert.match(cmd, /-c:v libx264/);
  assert.match(cmd, /-tune zerolatency/);
  assert.match(cmd, /-rtsp_transport tcp/);
  assert.match(cmd, /-i \/dev\/video0/);
});

test('srt push uses the standard streamid with s= for the password', () => {
  const cmd = buildSrtPush(device, opts);
  assert.match(cmd, /streamid=#!::m=publish,r=pi-01,u=pi-01,s=s3cr3t/);
  assert.match(cmd, /-f mpegts/);
  assert.match(cmd, /srt:\/\/10\.8\.0\.1:8890/);
});
