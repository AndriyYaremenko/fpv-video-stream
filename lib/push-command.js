function encoderArgs(o) {
  return [
    '-c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p',
    `-g ${o.framerate} -b:v ${o.bitrate} -maxrate ${o.bitrate} -bufsize ${o.bitrate}`,
  ].join(' ');
}

function inputArgs(o) {
  return `-f v4l2 -framerate ${o.framerate} -video_size ${o.videoSize} -i ${o.videoDevice}`;
}

export function buildRtspPush(device, o) {
  const url = `rtsp://${device.id}:${device.publish_pass}@${o.wgIp}:${o.rtspPort}/${device.id}`;
  return [
    'ffmpeg', inputArgs(o), encoderArgs(o),
    '-f rtsp -rtsp_transport tcp', `"${url}"`,
  ].join(' ');
}

export function buildSrtPush(device, o) {
  const streamid = `#!::m=publish,r=${device.id},u=${device.id},s=${device.publish_pass}`;
  const url = `srt://${o.wgIp}:${o.srtPort}?streamid=${streamid}&latency=200000&pkt_size=1316`;
  return [
    'ffmpeg', inputArgs(o), encoderArgs(o),
    '-f mpegts', `"${url}"`,
  ].join(' ');
}
