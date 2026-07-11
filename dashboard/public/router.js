// dashboard/public/router.js — hash router: builds sidebar nav, toggles screen sections.
export function createRouter({ routes, ctx }) {
  const nav = document.getElementById('nav');
  const title = document.getElementById('screen-title');
  const sizeCtl = document.getElementById('size-ctl');
  nav.innerHTML = '';
  for (const r of routes) {
    const item = document.createElement('div');
    item.className = 'nav-item'; item.dataset.hash = r.hash;
    item.innerHTML = `<span>${r.icon}</span><span>${r.label}</span>`;
    item.addEventListener('click', () => { location.hash = r.hash; });
    nav.appendChild(item);
  }
  function currentRoute() {
    return routes.find(r => r.hash === location.hash) || routes[0];
  }
  function renderActive() {
    const r = currentRoute();
    for (const s of document.querySelectorAll('.screen')) s.classList.add('hidden');
    document.getElementById(r.section).classList.remove('hidden');
    for (const it of nav.children) it.classList.toggle('active', it.dataset.hash === r.hash);
    title.textContent = r.label;
    sizeCtl.classList.toggle('hidden', r.hash !== '#/dashboard'); // slider only on dashboard
    r.mount(document.getElementById(r.section), ctx);
  }
  // Data-tick re-render: only re-mount the active screen if it derives from the live store
  // (dashboard/viewer/nodes = `live:true`, and their render() is reconcile-safe). Fetch-on-demand
  // screens (detections/frames) are NOT re-mounted on ticks — that would wipe half-typed toolbar
  // filters (Кадри) and pointlessly rebuild a cached table (Детекції). They render on mount +
  // their own explicit refresh/apply/pagination actions.
  function renderLive() {
    const r = currentRoute();
    if (r.live) r.mount(document.getElementById(r.section), ctx);
  }
  window.addEventListener('hashchange', renderActive);
  return { start() { if (!location.hash) location.hash = routes[0].hash; renderActive(); }, renderActive, renderLive, currentRoute };
}
