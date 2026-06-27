// Overview charts: fetch /api/stats/trend and render with Chart.js.
const ACCENT = '#2f7d3a';
const PALETTE = ['#2f7d3a', '#4a9d5a', '#6fb87d', '#f0a04b', '#e06c75',
                 '#61afef', '#c678dd', '#56b6c2', '#d19a66', '#98c379'];

Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.color = '#6b7280';

async function main() {
  let data;
  try {
    data = await (await fetch('/api/stats/trend?days=120')).json();
  } catch (e) { return; }

  // Daily reading minutes (bar).
  const daily = data.daily || [];
  bar('dailyChart', daily.map(d => d.date), daily.map(d => d.minutes), '分钟', ACCENT);

  // Monthly hours (bar).
  const monthly = data.monthly || [];
  bar('monthlyChart', monthly.map(m => m.month), monthly.map(m => m.hours), '小时', '#4a9d5a');

  // Preferred categories (doughnut).
  const cats = (data.preferCategory || []).slice(0, 8);
  doughnut('categoryChart', cats.map(c => c.categoryTitle), cats.map(c => c.readingTime));

  // 24h distribution (line). API note: index 0 corresponds to 6:00.
  const pt = data.preferTime || [];
  const hours = pt.map((_, i) => ((i + 6) % 24) + ':00');
  line('hourChart', hours, pt, '阅读秒数');

  // Preferred authors (tags).
  const at = document.getElementById('authorTags');
  if (at) {
    const authors = (data.preferAuthor || []).slice(0, 12);
    at.innerHTML = authors.length
      ? authors.map(a => `<span class="tag">${esc(a.name)} · ${esc(a.readTime || a.count)}</span>`).join('')
      : '<span class="sub">暂无数据</span>';
  }
}

function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function ctx(id) { const el = document.getElementById(id); return el ? el.getContext('2d') : null; }

function bar(id, labels, values, label, color) {
  const c = ctx(id); if (!c) return;
  new Chart(c, {
    type: 'bar',
    data: { labels, datasets: [{ label, data: values, backgroundColor: color, borderRadius: 3 }] },
    options: { plugins: { legend: { display: false } },
      scales: { x: { ticks: { maxTicksLimit: 12 }, grid: { display: false } },
                y: { beginAtZero: true } } }
  });
}

function line(id, labels, values, label) {
  const c = ctx(id); if (!c) return;
  new Chart(c, {
    type: 'line',
    data: { labels, datasets: [{ label, data: values, borderColor: ACCENT,
      backgroundColor: 'rgba(47,125,58,.12)', fill: true, tension: .35, pointRadius: 0 }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
  });
}

function doughnut(id, labels, values) {
  const c = ctx(id); if (!c) return;
  new Chart(c, {
    type: 'doughnut',
    data: { labels, datasets: [{ data: values, backgroundColor: PALETTE }] },
    options: { plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } } } }
  });
}

main();
