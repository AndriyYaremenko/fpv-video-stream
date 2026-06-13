import yaml from 'js-yaml';

const DEFAULTS = {
  wgIp: '10.8.0.1',
  rtspPort: 8554,
  srtPort: 8890,
  webrtcPort: 8889,
  iceUdpPort: 8189,
  apiHost: '127.0.0.1',
  apiPort: 9997,
};

export function buildConfigObject(reg, opts = {}) {
  const o = { ...DEFAULTS, ...opts };
  const devices = reg.devices || [];

  const authInternalUsers = [
    { user: 'any', pass: '', ips: ['127.0.0.1', '::1'], permissions: [{ action: 'api' }, { action: 'pprof' }] },
    { user: reg.read_user, pass: reg.read_pass, ips: [], permissions: [{ action: 'read' }] },
    ...devices.map((d) => ({
      user: d.id,
      pass: d.publish_pass,
      ips: [],
      permissions: [{ action: 'publish', path: d.id }],
    })),
  ];

  return {
    logLevel: 'info',
    logDestinations: ['stdout'],
    readTimeout: '10s',
    writeTimeout: '10s',

    api: true,
    apiAddress: `${o.apiHost}:${o.apiPort}`,
    metrics: false,
    pprof: false,
    playback: false,

    rtsp: true,
    rtspAddress: `${o.wgIp}:${o.rtspPort}`,
    rtspTransports: ['tcp', 'udp'],
    rtspEncryption: 'no',

    rtmp: false,
    hls: false,

    webrtc: true,
    webrtcAddress: `${o.wgIp}:${o.webrtcPort}`,
    webrtcEncryption: 'no',
    webrtcLocalUDPAddress: `:${o.iceUdpPort}`,
    webrtcLocalTCPAddress: '',
    webrtcIPsFromInterfaces: false,
    webrtcAdditionalHosts: [o.wgIp],
    webrtcICEServers2: [],

    srt: true,
    srtAddress: `${o.wgIp}:${o.srtPort}`,

    authMethod: 'internal',
    authInternalUsers,

    pathDefaults: {
      source: 'publisher',
    },
    paths: {
      all_others: {},
    },
  };
}

export function renderConfig(reg, opts = {}) {
  const header = '# GENERATED FILE — do not edit by hand.\n'
    + '# Source of truth: devices.yml. Regenerate with: node bin/gen-mediamtx.js\n';
  return header + yaml.dump(buildConfigObject(reg, opts), { lineWidth: 120, quotingType: '"' });
}
