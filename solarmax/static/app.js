/* Solarmax dashboard behaviours. */
(function () {
  function formatAustralianDate(isoDate) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
    return match ? `${match[3]}/${match[2]}/${match[1]}` : isoDate;
  }

  function renderChart() {
    const chart = document.getElementById('bill-chart');
    if (!chart) return;
    const points = JSON.parse(chart.dataset.points || '[]');
    if (!points.length) {
      chart.innerHTML = '<p class="muted">No billing data yet. Poll the inverter to start building history.</p>';
      return;
    }
    const maxAbs = Math.max(...points.map(p => Math.abs(p.amount_cents)), 0.01);
    chart.innerHTML = points.map((point) => {
      const height = Math.max(8, Math.round((Math.abs(point.amount_cents) / maxAbs) * 220));
      const cls = point.amount_cents < 0 ? 'bar neg' : 'bar';
      return `
        <div class="bar-wrap" title="${formatAustralianDate(point.day)}: $${(point.amount_cents / 100).toFixed(2)}">
          <div class="bar-slot" style="height: 220px; display:flex; align-items:${point.amount_cents < 0 ? 'start' : 'end'}; width:100%;">
            <div class="${cls}" style="height:${height}px"></div>
          </div>
          <div class="label">${formatAustralianDate(point.day)}</div>
          <div class="value">$${(point.amount_cents / 100).toFixed(2)}</div>
        </div>
      `;
    }).join('');
  }

  function bindBillToggle() {
    const button = document.getElementById('bill-toggle');
    const details = document.getElementById('bill-details');
    if (!button || !details) return;
    button.addEventListener('click', () => details.classList.toggle('hidden'));
  }

  document.addEventListener('DOMContentLoaded', () => {
    renderChart();
    bindBillToggle();
  });
})();
