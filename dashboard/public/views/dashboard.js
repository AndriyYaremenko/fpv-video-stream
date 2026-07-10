// dashboard/public/views/dashboard.js
export function render(container, ctx){ container.className='screen screen-pad';
  container.innerHTML = `<h2 class="section-title">Панель — ${ctx.cameras().length} камер</h2>`; }
