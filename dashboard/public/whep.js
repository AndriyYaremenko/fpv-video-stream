// dashboard/public/whep.js — minimal non-trickle WHEP reader.
export async function startWhep(video, whepUrl, user, pass) {
  const pc = new RTCPeerConnection({ iceServers: [] });
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });
  const stream = new MediaStream();
  pc.ontrack = (e) => { stream.addTrack(e.track); video.srcObject = stream; };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await iceGatheringComplete(pc, 2000);

  const res = await fetch(whepUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/sdp',
      Authorization: 'Basic ' + btoa(`${user}:${pass}`),
    },
    body: pc.localDescription.sdp,
  });
  if (!res.ok) { pc.close(); throw new Error(`WHEP ${res.status}`); }
  const answer = await res.text();
  await pc.setRemoteDescription({ type: 'answer', sdp: answer });
  return { close: () => { pc.close(); video.srcObject = null; } };
}

function iceGatheringComplete(pc, timeoutMs) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener('icegatheringstatechange', check); resolve(); };
    const check = () => { if (pc.iceGatheringState === 'complete') done(); };
    pc.addEventListener('icegatheringstatechange', check);
    setTimeout(done, timeoutMs);
  });
}
