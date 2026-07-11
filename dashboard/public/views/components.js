// dashboard/public/views/components.js — pure UI atoms shared by all screens.
import { classColor, fmtFreq, fmtPct } from '/spectrum.js';

export function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
export function el(tag,cls,html){const e=document.createElement(tag);if(cls)e.className=cls;if(html!=null)e.innerHTML=html;return e;}
export function fmtBitrate(k){return k==null?'—':k>=1000?`${(k/1000).toFixed(1)} Mbps`:`${k} kbps`;}
export function fmtUptime(s){if(s==null)return '—';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?`${h}год ${m}хв`:`${m}хв`;}
export function tempSlot(c){if(c==null)return '<span class="mono">—°C</span>';const cls=c>=75?'temp-hot':c>=60?'temp-warm':'';return `<span class="mono ${cls}">${c.toFixed(1)}°C</span>`;}
export function pip(online){return `<span class="pip ${online?'on':'off'}">${online?'ONLINE':'OFFLINE'}</span>`;}
export function cornerCard(innerHtml){const c=el('div','card corner',innerHtml);c.insertAdjacentHTML('beforeend','<span class="cm-bl"></span><span class="cm-br"></span>');return c;}
export function occupancyStrip(bands,occupancy){const wrap=el('div','occ');for(const band of Object.keys(bands||{})){const frac=(occupancy&&occupancy[band])||0;wrap.appendChild(el('div','occ-bar',`<span class="occ-label">${escapeHtml(band)}</span><span class="occ-track"><span class="occ-fill" style="width:${Math.round(frac*100)}%"></span></span><span class="occ-val">${fmtPct(frac)}</span>`));}return wrap;}
export function detectionCard(det,isNew){const cls=det.class==='analog'?'analog':det.class==='digital'?'digital':'';const c=el('div',`det-card ${cls}${isNew?' is-new':''}`);const chan=det.channel?` (${escapeHtml(det.channel)})`:'';const snr=det.snr_db!=null?` · SNR ${det.snr_db} dB`:'';c.innerHTML=`<div class="dc-top"><span class="dc-freq mono">${fmtFreq(det.center_mhz)}</span>${isNew?'<span class="pip warn">NEW</span>':''}</div><div class="dc-meta">${escapeHtml(det.band||'')}${chan}${snr} · <span style="color:${classColor(det.class)}">${escapeHtml(det.class||'')}</span></div>`;return c;}
