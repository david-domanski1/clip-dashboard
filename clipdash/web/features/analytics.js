// Earnings: what the clipping earns and what works. A Settings tab with stat tiles, a views timeline, bar blocks and a
// per-clip table, plus a small Today card. Charts are inline SVG built here (no libraries). Every dollar figure is an
// estimate and says so; pool campaigns show an upper bound with the reason.
SETTINGS_TABS.push(['analytics', 'Earnings']);

(function injectAnalyticsStyles() {
  if (document.getElementById('an-style')) return;
  const s = document.createElement('style');
  s.id = 'an-style';
  s.textContent = `
    :root{--an-mark:#a9a9b8;--an-mark-hover:#e6e6ee;--an-wash:rgba(169,169,184,.10)}
    .an-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
    .an-filters{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .an-filters select{width:auto}
    .an-tiles{margin-top:14px}.an-tiles .tile .n{font-variant-numeric:normal}
    .an-tiles .tile .note{font-size:11px;color:var(--faint);margin-top:6px;line-height:1.4}
    .an-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:14px}
    .an-two>.panel+.panel{margin-top:0}
    .an-block h3{margin:0 0 2px}
    .an-block .sub{margin:0 0 10px;font-size:12px}
    .an-chart{width:100%;position:relative}
    .an-chart svg{display:block;overflow:visible}
    .an-chart text{font-family:var(--sans);font-size:11px;fill:var(--muted);font-variant-numeric:tabular-nums}
    .an-chart text.v{fill:var(--text-2);font-weight:700}
    .an-chart .hit{fill:transparent;cursor:default;outline:none}
    .an-chart .bar{fill:var(--an-mark);transition:fill .12s}
    .an-chart g.row:hover .bar,.an-chart g.row:focus-within .bar{fill:var(--an-mark-hover)}
    .an-chart .grid{stroke:var(--line);stroke-width:1}
    .an-chart .base{stroke:var(--line-2);stroke-width:1}
    .an-chart .line{fill:none;stroke:var(--an-mark);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
    .an-chart .area{fill:var(--an-wash)}
    .an-chart .dot{fill:var(--an-mark);stroke:var(--surface);stroke-width:2}
    .an-chart .cross{stroke:var(--line-2);stroke-width:1}
    .an-earn{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:18px}
    .an-earn h4{margin:0 0 6px;font-size:12px;color:var(--text-2);font-weight:700}
    .an-tip{position:fixed;z-index:60;pointer-events:none;background:var(--surface-3);border:1px solid var(--line-2);border-radius:8px;padding:7px 10px;font-size:12px;color:var(--text-2);box-shadow:var(--shadow);max-width:260px}
    .an-tip b{display:block;color:var(--text);font-size:13px}
    .an-table th button{background:none;border:0;padding:0;color:inherit;font:inherit;letter-spacing:inherit;text-transform:inherit;cursor:pointer}
    .an-table th button:hover{color:var(--text-2)}
    .an-table td.num,.an-table th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
    .an-table-wrap{overflow-x:auto}
    .an-dim{opacity:.55;transition:opacity .15s}
  `;
  document.head.appendChild(s);
})();

const AN = { days: 30, sort: { key: 'views', dir: -1 } };

const anCompact = (n) => {
  n = Number(n) || 0;
  if (Math.abs(n) < 10000) return Math.round(n).toLocaleString();
  return new Intl.NumberFormat([], { notation: 'compact', maximumFractionDigits: 1 }).format(n);
};
const anMoney = (x) => '$' + (Number(x) || 0).toLocaleString([], { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const anHour = (h) => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: 'numeric' });
const anDay = (iso) => new Date(iso + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' });
const AN_PLATFORMS = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram', x: 'X', facebook: 'Facebook' };
const anPlatform = (p) => AN_PLATFORMS[p] || String(p || 'Other');
const anSecs = (s) => (s == null ? '' : Math.round(s) + ' s');

// What the estimate tile and Today card say. Pool = upper bound, CPM = estimate, anything else = no estimate.
function anEstimate(e) {
  if (!e) return { value: '—', label: 'Estimated earnings', note: '' };
  if (e.model === 'pool') return { value: 'Up to ' + anMoney(e.amount), label: 'Estimated earnings (upper bound)', note: e.note };
  if (e.model === 'cpm') return { value: anMoney(e.amount), label: 'Estimated earnings', note: 'Estimate: ' + e.note + '. The campaign decides what it actually pays.' };
  return { value: '—', label: 'Estimated earnings', note: e.note };
}

// ---- tooltip (labels go in with textContent: hooks and names are data)
function anTip() {
  let t = document.getElementById('an-tip');
  if (!t) { t = document.createElement('div'); t.id = 'an-tip'; t.className = 'an-tip hidden'; document.body.appendChild(t); }
  return t;
}
function anShowTip(ev, value, label) {
  const t = anTip();
  t.textContent = '';
  const b = document.createElement('b'); b.textContent = value; t.appendChild(b);
  if (label) t.appendChild(document.createTextNode(label));
  t.classList.remove('hidden');
  let x, y;
  if (ev.clientX != null && ev.type !== 'focus') { x = ev.clientX; y = ev.clientY; } else { const r = ev.target.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top; }
  const w = t.offsetWidth, h = t.offsetHeight;
  t.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, x + 12)) + 'px';
  t.style.top = Math.max(8, y - h - 12) + 'px';
}
const anHideTip = () => anTip().classList.add('hidden');

const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs = {}, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
// A bar growing from the left baseline: square at the baseline, 4px rounded data-end.
function barPath(x, y, w, h) {
  if (w <= 0) return '';
  const r = Math.min(4, w, h / 2);
  return `M${x},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h - r}A${r},${r} 0 0 1 ${x + w - r},${y + h}H${x}Z`;
}

// Horizontal bars: rows = [{label, value, text, tip}] (one series, neutral mark, value at the tip).
function anBars(box, rows) {
  box.textContent = '';
  if (!rows.length) return;
  const W = Math.max(240, box.clientWidth || 320), rowH = 28, barH = 14, labelW = Math.min(120, Math.round(W * 0.34)), valW = 64;
  const max = Math.max(1, ...rows.map((r) => r.value));
  const span = W - labelW - valW;
  const svg = svgEl('svg', { width: W, height: rows.length * rowH, role: 'img', 'aria-label': rows.map((r) => `${r.label}: ${r.text}`).join('; ') });
  rows.forEach((r, i) => {
    const y = i * rowH, by = y + (rowH - barH) / 2, w = Math.max(r.value > 0 ? 2 : 0, span * r.value / max);
    const g = svgEl('g', { class: 'row' });
    g.appendChild(svgEl('text', { x: 0, y: y + rowH / 2 + 4 }, r.label));
    g.appendChild(svgEl('path', { class: 'bar', d: barPath(labelW, by, w, barH) }));
    g.appendChild(svgEl('text', { class: 'v', x: labelW + w + 6, y: y + rowH / 2 + 4 }, r.text));
    const hit = svgEl('rect', { class: 'hit', x: 0, y, width: W, height: rowH, tabindex: 0, 'aria-label': `${r.label}: ${r.tip || r.text}` });
    const show = (ev) => anShowTip(ev, r.text, r.label + (r.tip ? ' · ' + r.tip : ''));
    hit.addEventListener('pointermove', show); hit.addEventListener('focus', show);
    hit.addEventListener('pointerleave', anHideTip); hit.addEventListener('blur', anHideTip);
    g.appendChild(hit);
    svg.appendChild(g);
  });
  box.appendChild(svg);
}

// Total views over time: one area series with a crosshair that snaps to the nearest day.
function anTimeline(box, points) {
  box.textContent = '';
  const W = Math.max(280, box.clientWidth || 600), H = 200, L = 52, R = 56, T = 12, B = 26;
  const n = points.length, max = Math.max(...points.map((p) => p.views), 0);
  const step = (() => { const raw = Math.max(1, max) / 3, p = Math.pow(10, Math.floor(Math.log10(raw))); return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= raw); })();
  const top = step * 3;
  const x = (i) => L + (n <= 1 ? (W - L - R) / 2 : i * (W - L - R) / (n - 1));
  const y = (v) => T + (H - T - B) * (1 - v / top);
  const svg = svgEl('svg', { width: W, height: H, role: 'img', 'aria-label': `Total views by day, ${anDay(points[0].day)} to ${anDay(points[n - 1].day)}; now ${points[n - 1].views.toLocaleString()}` });
  for (let k = 0; k <= 3; k++) {
    const v = step * k;
    svg.appendChild(svgEl('line', { class: k ? 'grid' : 'base', x1: L, x2: W - R, y1: y(v), y2: y(v) }));
    svg.appendChild(svgEl('text', { x: L - 8, y: y(v) + 4, 'text-anchor': 'end' }, anCompact(v)));
  }
  [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i).forEach((i) => {
    svg.appendChild(svgEl('text', { x: x(i), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' }, anDay(points[i].day)));
  });
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.views).toFixed(1)}`).join('');
  svg.appendChild(svgEl('path', { class: 'area', d: `${line}L${x(n - 1).toFixed(1)},${y(0)}L${x(0).toFixed(1)},${y(0)}Z` }));
  svg.appendChild(svgEl('path', { class: 'line', d: line }));
  const last = points[n - 1];
  svg.appendChild(svgEl('circle', { class: 'dot', cx: x(n - 1), cy: y(last.views), r: 4 }));
  svg.appendChild(svgEl('text', { class: 'v', x: x(n - 1) + 8, y: y(last.views) + 4 }, anCompact(last.views)));
  const cross = svgEl('line', { class: 'cross hidden', y1: T, y2: H - B });
  const hov = svgEl('circle', { class: 'dot hidden', r: 4 });
  svg.appendChild(cross); svg.appendChild(hov);
  const hit = svgEl('rect', { class: 'hit', x: L, y: T, width: W - L - R, height: H - T - B, tabindex: 0, 'aria-label': 'Views by day; use left and right arrows' });
  let cur = n - 1;
  const showAt = (i, ev) => {
    cur = Math.max(0, Math.min(n - 1, i));
    const p = points[cur];
    cross.setAttribute('x1', x(cur)); cross.setAttribute('x2', x(cur)); cross.classList.remove('hidden');
    hov.setAttribute('cx', x(cur)); hov.setAttribute('cy', y(p.views)); hov.classList.remove('hidden');
    const r = svg.getBoundingClientRect();
    anShowTip({ clientX: r.left + x(cur), clientY: r.top + y(p.views), type: 'move' }, p.views.toLocaleString() + ' views in total',
      `${anDay(p.day)} · +${(p.gained || 0).toLocaleString()} that day`);
  };
  hit.addEventListener('pointermove', (ev) => { const r = svg.getBoundingClientRect(); const px = ev.clientX - r.left; showAt(n <= 1 ? 0 : Math.round((px - L) / ((W - L - R) / (n - 1))), ev); });
  hit.addEventListener('focus', () => showAt(cur));
  hit.addEventListener('keydown', (ev) => { if (ev.key === 'ArrowLeft') { showAt(cur - 1); ev.preventDefault(); } if (ev.key === 'ArrowRight') { showAt(cur + 1); ev.preventDefault(); } });
  const hide = () => { cross.classList.add('hidden'); hov.classList.add('hidden'); anHideTip(); };
  hit.addEventListener('pointerleave', hide); hit.addEventListener('blur', hide);
  svg.appendChild(hit);
  box.appendChild(svg);
}

function anClipTable(rows) {
  const { key, dir } = AN.sort;
  const val = (r) => key === 'posted_at' ? (r.posted_at || '') : (r[key] ?? -1);
  const sorted = [...rows].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * dir);
  const head = (k, label, num) => `<th class="${num ? 'num' : ''}"><button data-sort="${k}" aria-label="Sort by ${label}">${label}${AN.sort.key === k ? (dir < 0 ? ' ↓' : ' ↑') : ''}</button></th>`;
  return `<table class="an-table"><tr><th>Clip</th><th>Platform</th>${head('posted_at', 'Posted')}${head('views', 'Views', true)}${head('est', 'Est. $', true)}<th>Moment</th><th>Narration</th>${head('length', 'Length', true)}</tr>
    ${sorted.map((r) => `<tr><td class="small">${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.hook || 'untitled')}</a>` : esc(r.hook || 'untitled')}</td>
      <td><span class="badge">${esc(anPlatform(r.platform))}</span></td><td class="small muted">${r.posted_at ? fmtDate(r.posted_at) : ''}</td>
      <td class="num">${r.views == null ? '<span class="muted">not read yet</span>' : fmtNum(r.views)}</td><td class="num">${r.views == null ? '' : anMoney(r.est)}</td>
      <td class="small">${esc(r.strength)}</td><td class="small">${r.narration ? 'On' : 'Off'}</td><td class="num small">${anSecs(r.length)}</td></tr>`).join('')}</table>`;
}

async function anRefreshViews(btn, c, after) {
  btn.disabled = true;
  try {
    const { job_id } = await post(`/api/stats/refresh${c ? '?campaign_id=' + c.id : ''}`);
    toast('Refreshing views from the platforms');
    for (let i = 0; i < 45; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const j = (await get('/api/jobs?limit=80')).find((x) => x.id === job_id);
      if (!j || !['queued', 'running'].includes(j.status)) { if (j && j.status === 'error') toast(j.error_text || 'Refreshing views failed', 'err'); break; }
    }
    after();
  } catch (e) { toastErr(e); } finally { btn.disabled = false; }
}

PANES.analytics = async (el) => {
  const c = campaign();
  const prev = el.querySelector('#an-body');
  if (prev) prev.classList.add('an-dim');
  let s;
  try {
    s = await get(`/api/analytics/summary?days=${AN.days}&tz=${encodeURIComponent(localTz)}${c ? '&campaign_id=' + c.id : ''}`);
  } catch (e) { el.innerHTML = `<div class="empty">Could not load earnings: ${esc(e.message)}</div>`; return; }
  const t = s.totals, est = anEstimate(s.earnings);
  const name = s.campaign?.name || c?.name || '';
  el.innerHTML = `<div class="an-head"><div><h2 style="margin-top:0">Earnings</h2>
      <p class="sub">What ${esc(name)} has earned and which clips worked. Views come from the platforms; dollar figures are estimates from the campaign's pay terms.${s.last_checked ? ' Views last read ' + esc(fmtDate(s.last_checked)) + '.' : ''}</p></div>
      <div class="an-filters"><select id="an-days" aria-label="Time range">${[7, 30, 90].map((d) => `<option value="${d}" ${d === AN.days ? 'selected' : ''}>Last ${d} days</option>`).join('')}</select>
      <button class="btn" id="an-refresh">Refresh views</button></div></div>
    <div id="an-body"></div>`;
  const body = $('#an-body', el);
  $('#an-days', el).onchange = (e) => { AN.days = Number(e.target.value); PANES.analytics(el); };
  $('#an-refresh', el).onclick = (e) => anRefreshViews(e.target, c, () => { if (el.isConnected) PANES.analytics(el); });

  if (!t.live_posts) {
    body.innerHTML = `<div class="empty">No live posts for this campaign yet. Post a clip and come back; views refresh every 6 hours, or press Refresh views.<a class="btn sm primary" href="#/publish">Publish →</a></div>`;
    return;
  }
  const measured = s.clips.filter((r) => r.views != null).length;
  body.innerHTML = `
    <div class="stat-tiles an-tiles">
      <div class="tile"><div class="n">${fmtNum(t.live_posts)}</div><div class="l">Live posts</div><div class="note">${measured === t.live_posts ? 'All have a view count' : `${measured} with a view count so far`}</div></div>
      <div class="tile"><div class="n">${anCompact(t.views)}</div><div class="l">Total views</div></div>
      <div class="tile"><div class="n">${anCompact(t.views_24h)}</div><div class="l">Views in the last 24 h</div><div class="note">${anCompact(t.views_7d)} in the last 7 days. Counted from view refreshes, so it can read low until a few refreshes have happened.</div></div>
      <div class="tile"><div class="n">${esc(est.value)}</div><div class="l">${esc(est.label)}</div>${est.note ? `<div class="note">${esc(est.note)}</div>` : ''}</div>
    </div>
    <div class="panel an-block" style="margin-top:14px"><h3>Total views over time</h3><p class="sub muted">All live posts in this campaign, last ${s.days} days, from each view refresh.</p><div class="an-chart" id="an-tl"></div></div>
    <div class="an-two">
      <div class="panel an-block"><h3>By platform</h3><p class="sub muted">Views per post. Hover a bar for posts, views and estimated $ per 1,000 views.</p><div class="an-chart" id="an-plat"></div></div>
      <div class="panel an-block"><h3>Best hours to post</h3><p class="sub muted">Views per post by the hour it went live (your time). Hours with one post are a hint, not a pattern.</p><div class="an-chart" id="an-hours"></div></div>
    </div>
    <div class="panel an-block" style="margin-top:14px"><h3>What earns</h3><p class="sub muted">Average views per post. A group shows once it has at least ${s.min_posts_per_bucket} posts.</p><div class="an-earn" id="an-earn"></div></div>
    <div class="panel an-block" style="margin-top:14px"><h3>Every clip</h3><p class="sub muted">One row per live post. Click a column title to sort.</p><div class="an-table-wrap" id="an-clips"></div></div>`;

  const draw = () => {
    if (!body.isConnected) { window.removeEventListener('resize', AN.onResize); return; }
    const tl = $('#an-tl', body);
    if (s.timeline.some((p) => p.views > 0)) anTimeline(tl, s.timeline);
    else tl.innerHTML = '<div class="small muted">No view refreshes in this range yet. Press Refresh views, or wait for the next automatic refresh (every 6 hours).</div>';
    anBars($('#an-plat', body), [...s.platforms].sort((a, b) => b.views_per_post - a.views_per_post).map((p) => ({ label: anPlatform(p.platform), value: p.views_per_post, text: anCompact(p.views_per_post),
      tip: `${p.posts} post${p.posts === 1 ? '' : 's'}, ${p.views.toLocaleString()} views, est. ${p.views ? anMoney(p.rpm) + ' per 1,000 views' : 'n/a'}${s.earnings.model === 'pool' ? ' (upper bound)' : ''}` })));
    const hrs = $('#an-hours', body);
    if (s.hours.length) {
      // "best" only for an hour with at least 2 posts, so one lucky clip does not crown an hour
      const solid = s.hours.filter((h) => h.posts >= 2);
      const best = solid.length ? Math.max(...solid.map((h) => h.views_per_post)) : null;
      const posts = (n) => `${n} post${n === 1 ? '' : 's'}`;
      anBars(hrs, s.hours.map((h) => ({ label: anHour(h.hour) + (h.posts >= 2 && h.views_per_post === best ? ' · best' : ''), value: h.views_per_post,
        text: `${anCompact(h.views_per_post)} · ${posts(h.posts)}`, tip: posts(h.posts) })));
    } else hrs.innerHTML = `<div class="small muted">Shows once ${s.min_posts_for_hours} posts have gone live. Post at different times to see what works.</div>`;
    const groups = [['Moment strength', s.earned.by_label], ['Clip length', s.earned.by_length], ['Narrated hook', s.earned.by_narration]];
    const earn = $('#an-earn', body);
    earn.innerHTML = groups.map(([g], i) => `<div><h4>${esc(g)}</h4><div class="an-chart" data-g="${i}"></div></div>`).join('');
    groups.forEach(([, rows], i) => {
      const box = $(`[data-g="${i}"]`, earn);
      if (rows.length) anBars(box, rows.map((r) => ({ label: r.key, value: r.avg_views, text: anCompact(r.avg_views), tip: `${r.posts} posts` })));
      else box.innerHTML = '<div class="small muted">Not enough posts to compare yet.</div>';
    });
  };
  const table = () => {
    const box = $('#an-clips', body);
    box.innerHTML = anClipTable(s.clips);
    $$('[data-sort]', box).forEach((b) => b.onclick = () => {
      const k = b.dataset.sort;
      AN.sort = AN.sort.key === k ? { key: k, dir: -AN.sort.dir } : { key: k, dir: -1 };
      table();
    });
  };
  draw();
  table();
  window.removeEventListener('resize', AN.onResize);
  let tm = null;
  AN.onResize = () => { clearTimeout(tm); tm = setTimeout(draw, 150); };
  window.addEventListener('resize', AN.onResize);
};

// Today card: views in the last 24 h and the estimate, only when the selected campaign has a live post.
TODAY_EXTRAS.push((t, c) => {
  if (!c || t.workspace) return '';
  const id = 'an-today-' + c.id;
  get(`/api/analytics/today?campaign_id=${c.id}`).then((d) => {
    const card = document.getElementById(id);
    if (!card) return;
    if (!d || !d.live_posts) { card.remove(); return; }
    const est = anEstimate(d.earnings);
    $('.step-sub', card).innerHTML = `<b>${fmtNum(d.views_24h)}</b> views in the last 24 h · ${fmtNum(d.views)} in total over ${d.live_posts} live post${d.live_posts === 1 ? '' : 's'}<br>
      <span>${esc(est.label)}: <b>${esc(est.value)}</b></span>${est.note ? `<div class="small muted">${esc(est.note)}</div>` : ''}`;
    card.classList.remove('hidden');
  }).catch(() => { const card = document.getElementById(id); if (card) card.remove(); });
  return `<div class="step done hidden" id="${id}"><div class="step-n">$</div>
    <div class="step-body"><div class="step-title">Earnings</div><div class="step-sub"></div></div>
    <a class="btn" href="#/settings/analytics">Details</a></div>`;
});
