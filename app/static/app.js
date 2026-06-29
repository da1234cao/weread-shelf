// Overview: a 周/月/年/总 tabbed view backed by /api/stats (a pure DB read).
// The selected period drives the metric cards + distribution chart; the fixed
// "总体偏好" section always reflects the cumulative (overall) preferences.
const ACCENT = '#2f7d3a';
const PALETTE = ['#2f7d3a', '#4a9d5a', '#6fb87d', '#f0a04b', '#e06c75',
                 '#61afef', '#c678dd', '#56b6c2', '#d19a66', '#98c379'];

Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.color = '#6b7280';

const state = { mode: 'monthly', offset: 0 };
const charts = {};

function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function ctx(id) { const el = document.getElementById(id); return el ? el.getContext('2d') : null; }

// Charts are recreated on every navigation, so dispose the previous instance first.
function chart(id, config) {
  if (charts[id]) charts[id].destroy();
  const c = ctx(id);
  if (c) charts[id] = new Chart(c, config);
}

function bar(id, labels, values, unit, color) {
  chart(id, {
    type: 'bar',
    data: { labels, datasets: [{ data: values, backgroundColor: color, borderRadius: 3 }] },
    options: { plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (it) => `${it.parsed.y} ${unit}` } } },
      scales: { x: { ticks: { maxTicksLimit: 12 }, grid: { display: false } },
                y: { beginAtZero: true } } }
  });
}

function doughnut(id, labels, values) {
  chart(id, {
    type: 'doughnut',
    data: { labels, datasets: [{ data: values, backgroundColor: PALETTE }] },
    options: { plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } } } }
  });
}

function line(id, labels, values, label) {
  chart(id, {
    type: 'line',
    data: { labels, datasets: [{ label, data: values, borderColor: ACCENT,
      backgroundColor: 'rgba(47,125,58,.12)', fill: true, tension: .35, pointRadius: 0 }] },
    options: { plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, title: { display: true, text: label } } } }
  });
}

// Mirror of the server's fmt_duration, for the (raw seconds) day-average.
function fmtDuration(sec) {
  sec = sec || 0;
  if (sec < 60) return sec + '秒';
  const m = Math.floor(sec / 60), h = Math.floor(m / 60), mm = m % 60;
  if (h) return mm ? `${h}小时${mm}分` : `${h}小时`;
  return mm + '分钟';
}

function card(label, value) {
  return `<div class="card kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`;
}

function renderCards(d) {
  const cards = [card('阅读时长', d.total_read_time_h)];
  if (d.day_average != null) cards.push(card('日均', fmtDuration(d.day_average)));
  // readStat carries 读过/读完/阅读/笔记 (month/year/total); week has none, so
  // fall back to the day count it does provide.
  if (d.read_stat && d.read_stat.length) {
    d.read_stat.forEach(s => cards.push(card(s.stat, s.counts)));
  } else {
    cards.push(card('阅读天数', d.read_days + ' 天'));
  }
  document.getElementById('statCards').innerHTML = cards.join('');
}

async function loadStats() {
  const { mode, offset } = state;
  const nav = document.getElementById('periodNav');
  const label = document.getElementById('periodLabel');
  // overall is cumulative — no period to page through.
  nav.style.visibility = (mode === 'overall') ? 'hidden' : 'visible';

  let d;
  try {
    d = await (await fetch(`/api/stats?mode=${mode}&offset=${offset}`)).json();
  } catch (e) {
    document.getElementById('statCards').innerHTML = '<div class="sub">加载失败，请稍后重试。</div>';
    return;
  }

  if (d.empty) {
    label.textContent = '';
    document.getElementById('statCards').innerHTML = '<div class="sub">该周期暂无数据（可能正在首次同步）。</div>';
    if (charts.distChart) charts.distChart.destroy();
    return;
  }

  label.textContent = d.period_label;
  document.getElementById('prevBtn').disabled = !d.has_prev;
  document.getElementById('nextBtn').disabled = !d.has_next;

  renderCards(d);
  const minutes = d.dist_unit === 'minutes';
  const values = d.distribution.map(x => minutes ? Math.round(x.seconds / 60) : Math.round(x.seconds / 360) / 10);
  bar('distChart', d.distribution.map(x => x.label), values, minutes ? '分钟' : '小时', ACCENT);
}

// The fixed "总体偏好" section always reads the cumulative (overall) row.
async function loadPreferences() {
  let d;
  try { d = await (await fetch('/api/stats?mode=overall&offset=0')).json(); } catch (e) { return; }
  if (d.empty) return;

  const cats = (d.prefer_category || []).slice(0, 8);
  doughnut('categoryChart', cats.map(c => c.categoryTitle), cats.map(c => c.readingTime));

  // 24h distribution: index 0 corresponds to 6:00; values are seconds.
  const pt = d.prefer_time || [];
  const hours = pt.map((_, i) => ((i + 6) % 24) + ':00');
  line('hourChart', hours, pt.map(s => Math.round((s / 60) * 10) / 10), '阅读分钟');

  const at = document.getElementById('authorTags');
  if (at) {
    const authors = (d.prefer_author || []).slice(0, 12);
    at.innerHTML = authors.length
      ? authors.map(a => `<span class="tag">${esc(a.name)} · ${esc(a.readTime || a.count)}</span>`).join('')
      : '<span class="sub">暂无数据</span>';
  }
}

document.getElementById('modeTabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.subtab');
  if (!btn) return;
  state.mode = btn.dataset.mode;
  state.offset = 0;
  document.querySelectorAll('#modeTabs .subtab').forEach(b => b.classList.toggle('active', b === btn));
  loadStats();
});
document.getElementById('prevBtn').addEventListener('click', () => { state.offset -= 1; loadStats(); });
document.getElementById('nextBtn').addEventListener('click', () => { state.offset = Math.min(0, state.offset + 1); loadStats(); });

loadStats();
loadPreferences();
