/* Clip Dashboard UI: no build step, plain ES2020. */
'use strict';

// ---------------------------------------------------------------- helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtTime = (s) => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return (h ? h + ':' : '') + String(m).padStart(h ? 2 : 1, '0') + ':' + String(x).padStart(2, '0'); };
const fmtBytes = (n) => { const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; n = n || 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return n.toFixed(i ? 1 : 0) + ' ' + u[i]; };
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
const fmtNum = (n) => (n ?? 0).toLocaleString();
const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone;

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && typeof o.body !== 'string') { o.body = JSON.stringify(o.body); o.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) { const err = new Error((data && (data.message || data.detail)) || r.statusText); err.data = data; err.status = r.status; throw err; }
  return data;
}
const get = (p) => api(p);
const post = (p, body) => api(p, { method: 'POST', body: body || {} });
const put = (p, body) => api(p, { method: 'PUT', body });
const del = (p) => api(p, { method: 'DELETE' });

function toast(msg, kind = '') {
  const t = document.createElement('div'); t.className = 'toast ' + kind; t.textContent = msg;
  $('#toasts').appendChild(t); setTimeout(() => t.remove(), kind === 'err' ? 7000 : 3500);
}
const toastErr = (e) => toast(e.message || String(e), 'err');

function modal(html, { narrow = false } = {}) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-bg"><div class="modal ${narrow ? 'narrow' : ''}">${html}</div></div>`;
  $('.modal-bg', root).addEventListener('click', (e) => { if (e.target.classList.contains('modal-bg')) closeModal(); });
  return $('.modal', root);
}
function closeModal() { $('#modal-root').innerHTML = ''; }
function confirmDlg(msg, okLabel = 'Continue') {
  return new Promise((res) => {
    const m = modal(`<div class="modal-head"><h2 style="margin:0">Are you sure?</h2></div><p>${esc(msg)}</p>
      <div class="row" style="justify-content:flex-end"><button class="btn" id="c-no">Cancel</button><button class="btn pink" id="c-yes">${esc(okLabel)}</button></div>`, { narrow: true });
    $('#c-no', m).onclick = () => { closeModal(); res(false); };
    $('#c-yes', m).onclick = () => { closeModal(); res(true); };
  });
}

// ---------------------------------------------------------------- state & routing
const S = { campaigns: [], campaignId: Number(localStorage.getItem('campaignId')) || null, state: null, view: 'home', timer: null, tick: null };
const campaign = () => S.campaigns.find((c) => c.id === S.campaignId) || S.campaigns[0] || null;

async function loadCampaigns() {
  S.campaigns = await get('/api/campaigns');
  if (!S.campaigns.find((c) => c.id === S.campaignId)) S.campaignId = S.campaigns[0]?.id || null;
  const sel = $('#campaign-select');
  sel.innerHTML = S.campaigns.map((c) => `<option value="${c.id}" ${c.id === S.campaignId ? 'selected' : ''}>${esc(c.name)}${c.active ? '' : ' (paused)'}</option>`).join('') + `<option value="new">+ New campaign…</option>`;
}
$('#campaign-select').addEventListener('change', (e) => {
  if (e.target.value === 'new') { e.target.value = S.campaignId || ''; openCampaignEditor(); return; }
  S.campaignId = Number(e.target.value); localStorage.setItem('campaignId', S.campaignId); render();
});

async function refreshState() {
  try {
    S.state = await get('/api/state');
    const st = S.state;
    $('#brand-sub').textContent = 'v' + st.version;
    $('#pill-videos').textContent = st.videos_in_progress || '';
    $('#pill-review').textContent = st.clips_to_review || '';
    $('#pill-publish').textContent = st.clips_approved || '';
    $('#pill-subs').textContent = st.submissions_due || '';
    $('#dry-run-flag').classList.toggle('hidden', !st.dry_run);
    $('#jobs-mini').innerHTML = st.jobs_running.length ? st.jobs_running.map((j) => `<div>⟳ ${esc(j.type.replace('_', ' '))} ${Math.round(j.progress * 100)}%<br><span class="small">${esc(j.message)}</span></div>`).join('') + (st.jobs_queued ? `<div>${st.jobs_queued} queued</div>` : '') : `<div>Idle${st.jobs_queued ? ' · ' + st.jobs_queued + ' queued' : ''}</div>`;
    const d = st.disk;
    $('#disk-mini').innerHTML = `Disk: ${fmtBytes(d.free_bytes)} free · cache ${fmtBytes(d.cache_bytes)} · clips ${fmtBytes(d.clips_bytes)}`;
  } catch (e) { $('#jobs-mini').textContent = 'Server unreachable'; }
}

const VIEWS = {};
async function render() {
  const hash = location.hash.replace(/^#\/?/, '') || 'home';
  const [view, arg] = hash.split('/');
  S.view = view;
  $$('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === view));
  const fn = VIEWS[view] || VIEWS.home;
  try { await fn($('#main'), arg); } catch (e) { $('#main').innerHTML = `<div class="callout pink">${esc(e.message)}</div>`; }
}
window.addEventListener('hashchange', render);

async function boot() {
  await refreshState();
  await loadCampaigns();
  if (!S.state.setup_done && !location.hash.includes('setup')) location.hash = '#/setup';
  await render();
  setInterval(async () => {
    await refreshState();
    if (S.state && (S.state.jobs_running.length || S.state.jobs_queued) && ['videos', 'review', 'publish', 'home', 'setup', 'accounts'].includes(S.view) && !$('#modal-root').innerHTML) render();
  }, 4000);
}

// ---------------------------------------------------------------- Overview
VIEWS.home = async (el) => {
  const st = S.state || await get('/api/state');
  const c = campaign();
  el.innerHTML = `
    <h1>Overview</h1><p class="sub">Clips are the product. Everything else is here to keep the campaign safe.</p>
    <div class="stat-tiles">
      <div class="tile teal"><div class="n">${st.clips_to_review}</div><div class="l">clips to review</div></div>
      <div class="tile pink"><div class="n">${st.clips_approved}</div><div class="l">approved, ready to post</div></div>
      <div class="tile yellow"><div class="n">${st.posts_scheduled}</div><div class="l">scheduled posts</div></div>
      <div class="tile"><div class="n">${st.submissions_due}</div><div class="l">submissions pending</div></div>
      <div class="tile"><div class="n">${st.videos_in_progress}</div><div class="l">videos in the pipeline</div></div>
      <div class="tile"><div class="n">${fmtBytes(st.disk.free_bytes)}</div><div class="l">disk free</div></div>
    </div>
    <h2>Campaigns</h2>
    <div class="grid cols-3" id="camp-cards"></div>
    <h2>Running now</h2>
    <div class="panel" id="jobs-panel"></div>`;
  $('#camp-cards').innerHTML = S.campaigns.map((c) => `
    <div class="panel" style="cursor:pointer" data-id="${c.id}">
      <div class="row between"><b>${esc(c.name)}</b> ${c.active ? '<span class="badge ok">active</span>' : '<span class="badge">paused</span>'}</div>
      <div class="muted small">${esc(c.creator)} · ${(c.platforms || []).join(', ')} · ≥${c.min_len}s · submit within ${c.submit_minutes} min</div>
      <div class="row small" style="margin-top:8px;gap:14px"><span>${c.counts.sources} sources</span><span>${c.counts.videos} videos</span><span>${c.counts.pending} to review</span><span>${c.counts.approved} approved</span><span>${c.counts.posted} live</span></div>
      <div class="row" style="margin-top:10px"><button class="btn sm" data-edit="${c.id}">Edit rules</button>${c.url ? `<a class="btn sm ghost" href="${esc(c.url)}" target="_blank">Campaign page</a>` : ''}</div>
    </div>`).join('') + `<div class="panel" style="cursor:pointer;border-style:dashed;display:flex;align-items:center;justify-content:center" id="new-camp">+ New campaign</div>`;
  $$('#camp-cards [data-edit]').forEach((b) => b.onclick = (e) => { e.stopPropagation(); openCampaignEditor(S.campaigns.find((x) => x.id === Number(b.dataset.edit))); });
  $$('#camp-cards .panel[data-id]').forEach((p) => p.onclick = () => { S.campaignId = Number(p.dataset.id); localStorage.setItem('campaignId', S.campaignId); loadCampaigns(); location.hash = '#/videos'; });
  $('#new-camp').onclick = () => openCampaignEditor();
  const jobs = await get('/api/jobs?limit=12');
  $('#jobs-panel').innerHTML = jobs.length ? `<table><tr><th>Job</th><th>Status</th><th>Progress</th><th></th></tr>${jobs.map((j) => `<tr><td>${esc(j.type.replace(/_/g, ' '))} <span class="muted small">#${j.id}</span><br><span class="small muted">${esc(j.message || '')}</span>${j.error ? `<br><span class="small bad-text">${esc(j.error.slice(0, 300))}</span>` : ''}</td><td><span class="badge ${j.status}">${j.status}</span></td><td style="width:160px"><div class="progress"><i style="width:${Math.round(j.progress * 100)}%"></i></div></td><td>${j.status === 'error' ? `<button class="btn sm" data-retry="${j.id}">Retry</button>` : ''}</td></tr>`).join('')}</table>` : '<div class="muted">Nothing running.</div>';
  $$('#jobs-panel [data-retry]').forEach((b) => b.onclick = () => post(`/api/jobs/${b.dataset.retry}/retry`).then(render));
};

// ---------------------------------------------------------------- Campaign editor
function openCampaignEditor(c = null) {
  const v = (k, d = '') => esc(c ? (c[k] ?? d) : d);
  const plats = c ? c.platforms : ['youtube', 'tiktok', 'instagram'];
  const m = modal(`
    <div class="modal-head"><h2 style="margin:0">${c ? 'Edit campaign' : 'New campaign'}</h2><button class="btn ghost" onclick="closeModal()">✕</button></div>
    ${c ? '' : `<div class="panel" style="margin-bottom:14px"><div class="field"><label>Paste the campaign URL or its rules text; the fields below get pre-filled (check every one)</label>
      <div class="row"><input type="text" id="c-url" placeholder="https://reachclipping.com/LACYGTA"><button class="btn" id="c-parse">Read page</button></div>
      <textarea id="c-rules" placeholder="…or paste the rules here"></textarea><button class="btn sm" id="c-parse-text" style="align-self:flex-start">Pre-fill from text</button></div></div>`}
    <div class="form-grid">
      <div class="field"><label>Campaign name</label><input id="f-name" type="text" value="${v('name')}"></div>
      <div class="field"><label>Creator (the streamer)</label><input id="f-creator" type="text" value="${v('creator')}"></div>
      <div class="field"><label>Campaign page URL</label><input id="f-url" type="text" value="${v('url')}"></div>
      <div class="field"><label>Submission page URL (where links are submitted)</label><input id="f-sub" type="text" value="${v('submission_url')}"></div>
      <div class="field"><label>Required names in post text (comma separated)</label><input id="f-names" type="text" value="${esc((c?.required_names || []).join(', '))}"></div>
      <div class="field"><label>Required hashtags (comma separated)</label><input id="f-tags" type="text" value="${esc((c?.required_hashtags || []).join(', '))}"></div>
      <div class="field"><label>Minimum length (s)</label><input id="f-min" type="number" value="${v('min_len', 61)}"></div>
      <div class="field"><label>Maximum length (s)</label><input id="f-max" type="number" value="${v('max_len', 180)}"></div>
      <div class="field"><label>Source footage from (date)</label><input id="f-start" type="date" value="${esc((c?.source_start || '').slice(0, 10))}"></div>
      <div class="field"><label>Source footage until (optional)</label><input id="f-end" type="date" value="${esc((c?.source_end || '').slice(0, 10))}"></div>
      <div class="field"><label>Submit link within (minutes of posting)</label><input id="f-submit" type="number" value="${v('submit_minutes', 30)}"></div>
      <div class="field"><label>Watermark / logos</label><select id="f-wm"><option value="0" ${!c || !c.watermark_allowed ? 'selected' : ''}>Not allowed</option><option value="1" ${c?.watermark_allowed ? 'selected' : ''}>Allowed</option></select></div>
      <div class="field"><label>Pay model</label><select id="f-pay"><option value="cpm" ${c?.pay_model !== 'pool' ? 'selected' : ''}>Per 1,000 views</option><option value="pool" ${c?.pay_model === 'pool' ? 'selected' : ''}>Pool split by share of views</option></select></div>
      <div class="field"><label>Rate per 1,000 views ($)</label><input id="f-rate" type="number" step="0.01" value="${v('rate_per_1k', 0)}"></div>
      <div class="field"><label>Cap per clip ($, 0 = none)</label><input id="f-cap" type="number" step="1" value="${v('per_clip_cap', 0)}"></div>
      <div class="field"><label>Pool budget ($)</label><input id="f-pool" type="number" step="1" value="${v('pool_budget', 0)}"></div>
      <div class="field"><label>Minimum total views to qualify</label><input id="f-mv" type="number" value="${v('min_views', 0)}"></div>
    </div>
    <div class="field"><label>Allowed platforms</label><div class="chips" id="f-plats">${['youtube', 'tiktok', 'instagram', 'x'].map((p) => `<span class="chip ${plats.includes(p) ? 'on' : ''}" data-p="${p}">${p}</span>`).join('')}</div></div>
    <div class="field"><label>Review checklist (one per line: things only a person can judge)</label><textarea id="f-check">${esc((c?.checklist || ['The creator is the main focus for the whole clip', 'Nothing in the clip makes the creator look bad or pushes a false narrative', 'No logos, overlays or third-party watermarks are visible', 'The clip stands on its own (a viewer gets it without context)']).join('\n'))}</textarea></div>
    <div class="field"><label>Rules text / notes</label><textarea id="f-rules">${v('rules_text')}</textarea></div>
    <div class="row between">
      <div>${c ? `<label class="check"><input type="checkbox" id="f-active" ${c.active ? 'checked' : ''}> Active (sources are watched)</label>` : ''}</div>
      <div class="row">${c ? `<button class="btn danger" id="f-del">Delete</button>` : ''}<button class="btn primary" id="f-save">Save</button></div>
    </div>`);
  const fill = (f) => {
    const set = (id, val) => { if (val !== undefined && val !== null && val !== '') $(id, m).value = Array.isArray(val) ? val.join(', ') : val; };
    set('#f-creator', f.creator); set('#f-tags', f.required_hashtags); set('#f-submit', f.submit_minutes); set('#f-rate', f.rate_per_1k);
    set('#f-cap', f.per_clip_cap); set('#f-pool', f.pool_budget); set('#f-mv', f.min_views); set('#f-start', f.source_start); set('#f-url', f.url);
    set('#f-min', f.min_len); set('#f-rules', f.rules_text);
    if (f.pay_model) $('#f-pay', m).value = f.pay_model;
    if (f.watermark_allowed !== undefined) $('#f-wm', m).value = String(f.watermark_allowed);
    if (f.creator && !$('#f-name', m).value) $('#f-name', m).value = f.creator + ' campaign';
    if (f.creator && !$('#f-names', m).value) $('#f-names', m).value = f.creator;
    if (f.platforms) $$('#f-plats .chip', m).forEach((ch) => ch.classList.toggle('on', f.platforms.includes(ch.dataset.p)));
    toast('Pre-filled. Check every field against the campaign page.');
  };
  if (!c) {
    $('#c-parse', m).onclick = async () => { try { const r = await post('/api/campaigns/parse', { url: $('#c-url', m).value }); if (r.error) toast(r.error, 'warn'); fill(r.fields); } catch (e) { toastErr(e); } };
    $('#c-parse-text', m).onclick = async () => { try { const r = await post('/api/campaigns/parse', { text: $('#c-rules', m).value, url: $('#c-url', m).value }); fill(r.fields); } catch (e) { toastErr(e); } };
  }
  $$('#f-plats .chip', m).forEach((ch) => ch.onclick = () => ch.classList.toggle('on'));
  $('#f-save', m).onclick = async () => {
    const list = (s) => s.split(',').map((x) => x.trim()).filter(Boolean);
    const body = {
      name: $('#f-name', m).value.trim(), creator: $('#f-creator', m).value.trim(), url: $('#f-url', m).value.trim(), submission_url: $('#f-sub', m).value.trim(),
      required_names: list($('#f-names', m).value), required_hashtags: list($('#f-tags', m).value).map((t) => t.startsWith('#') ? t : '#' + t),
      min_len: Number($('#f-min', m).value) || 61, max_len: Number($('#f-max', m).value) || 180,
      source_start: $('#f-start', m).value || null, source_end: $('#f-end', m).value ? $('#f-end', m).value + 'T23:59:59+00:00' : null,
      submit_minutes: Number($('#f-submit', m).value) || 30, watermark_allowed: Number($('#f-wm', m).value), pay_model: $('#f-pay', m).value,
      rate_per_1k: Number($('#f-rate', m).value) || 0, per_clip_cap: Number($('#f-cap', m).value) || 0, pool_budget: Number($('#f-pool', m).value) || 0,
      min_views: Number($('#f-mv', m).value) || 0, platforms: $$('#f-plats .chip.on', m).map((x) => x.dataset.p),
      checklist: $('#f-check', m).value.split('\n').map((x) => x.trim()).filter(Boolean), rules_text: $('#f-rules', m).value,
    };
    if (c) body.active = $('#f-active', m).checked ? 1 : 0;
    if (!body.required_names.length) body.required_names = [body.creator];
    try {
      const saved = c ? await put(`/api/campaigns/${c.id}`, body) : await post('/api/campaigns', body);
      S.campaignId = saved.id; localStorage.setItem('campaignId', saved.id);
      closeModal(); await loadCampaigns(); render(); toast('Campaign saved');
    } catch (e) { toastErr(e); }
  };
  if (c) $('#f-del', m).onclick = async () => { if (await confirmDlg(`Delete campaign "${c.name}" with all its sources, videos, clips and posts?`, 'Delete')) { await del(`/api/campaigns/${c.id}`); closeModal(); await loadCampaigns(); render(); } };
}

// ---------------------------------------------------------------- Sources
VIEWS.sources = async (el) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  const [srcs, sugg] = await Promise.all([get(`/api/campaigns/${c.id}/sources`), get('/api/suggestions')]);
  el.innerHTML = `
    <h1>Sources · ${esc(c.name)}</h1><p class="sub">Channels are watched for new GTA VI videos; single videos are scanned once. Only sources attached to a campaign are processed.</p>
    <div class="panel">
      <div class="field"><label>Add a YouTube channel (youtube.com/@name), Twitch channel (twitch.tv/name), or a single video URL</label>
        <div class="row"><input type="text" id="src-url" placeholder="https://www.youtube.com/@Lacy  ·  https://www.twitch.tv/lacy  ·  https://www.youtube.com/watch?v=…"><button class="btn primary" id="src-add">Add</button></div></div>
      <div class="row small muted">Suggestions:</div>
      <div class="chips" style="margin-top:6px">${sugg.map((s) => `${s.youtube ? `<span class="chip" data-url="https://www.youtube.com/${s.youtube}">▶ ${esc(s.name)} (YouTube)</span>` : ''}${s.twitch ? `<span class="chip" data-url="https://www.twitch.tv/${s.twitch}">⌁ ${esc(s.name)} (Twitch)</span>` : ''}`).join('')}</div>
      <div class="small muted" style="margin-top:8px">Channels are resolved by lookup when added (no hardcoded IDs). Many streamers keep edited highlights on one channel and full streams on another or on the channel's Live tab; both tabs are checked.</div>
    </div>
    <div class="grid cols-2" style="margin-top:14px" id="src-list">${srcs.length ? '' : '<div class="empty">No sources yet. Add a channel or paste a video URL.</div>'}</div>`;
  const add = async (url) => {
    const btn = $('#src-add'); btn.disabled = true; btn.textContent = 'Resolving…';
    try { const r = await post('/api/sources', { campaign_id: c.id, url }); toast(r.video ? 'Video queued for scanning' : `Added ${r.source.name}`); if (r.video) location.hash = '#/videos'; else render(); }
    catch (e) { toastErr(e); } finally { btn.disabled = false; btn.textContent = 'Add'; }
  };
  $('#src-add').onclick = () => add($('#src-url').value.trim());
  $('#src-url').onkeydown = (e) => { if (e.key === 'Enter') add(e.target.value.trim()); };
  $$('.chip[data-url]').forEach((ch) => ch.onclick = () => { $('#src-url').value = ch.dataset.url; add(ch.dataset.url); });
  const list = $('#src-list');
  for (const s of srcs) {
    const d = document.createElement('div'); d.className = 'panel';
    d.innerHTML = `
      <div class="row between"><div><b>${esc(s.name || s.url)}</b> <span class="badge">${s.kind.replace('_', ' ')}</span></div>
        <label class="check small"><input type="checkbox" ${s.watch ? 'checked' : ''} data-watch> watch for new videos</label></div>
      <div class="small muted"><a href="${esc(s.url)}" target="_blank">${esc(s.url)}</a> · ${s.video_count} videos · last checked ${s.last_checked ? fmtDate(s.last_checked) : 'never'}</div>
      <div class="row" style="margin-top:10px">
        <button class="btn sm" data-check>Check for new videos</button>
        <button class="btn sm" data-recent>Browse recent videos</button>
        <button class="btn sm" data-facecam>${s.facecam ? 'Facecam set ✓ (edit)' : 'Set facecam crop'}</button>
        <select class="sm" data-layout style="width:auto"><option value="full" ${s.layout === 'full' ? 'selected' : ''}>Layout: gameplay full width</option><option value="stacked" ${s.layout === 'stacked' ? 'selected' : ''}>Layout: facecam top, gameplay below</option></select>
        <button class="btn sm danger" data-del>Remove</button>
      </div>
      <div data-extra></div>`;
    $('[data-watch]', d).onchange = (e) => put(`/api/sources/${s.id}`, { watch: e.target.checked ? 1 : 0 }).catch(toastErr);
    $('[data-layout]', d).onchange = (e) => put(`/api/sources/${s.id}`, { layout: e.target.value }).then(() => toast('Layout saved; re-render clips to apply')).catch(toastErr);
    $('[data-check]', d).onclick = () => post(`/api/sources/${s.id}/check`).then(() => toast('Checking in the background')).catch(toastErr);
    $('[data-del]', d).onclick = async () => { if (await confirmDlg(`Remove ${s.name}? Its videos and clips stay.`, 'Remove')) { await del(`/api/sources/${s.id}`); render(); } };
    $('[data-facecam]', d).onclick = () => openFacecamPicker(s);
    $('[data-recent]', d).onclick = async () => {
      const x = $('[data-extra]', d); x.innerHTML = '<div class="muted small">Loading…</div>';
      try {
        const vids = await get(`/api/sources/${s.id}/recent`);
        x.innerHTML = `<table style="margin-top:10px">${vids.map((v) => `<tr><td><a href="${esc(v.url)}" target="_blank">${esc(v.title)}</a><br><span class="small muted">${v.published_at ? fmtDate(v.published_at) : ''} · ${v.duration ? fmtTime(v.duration) : ''} ${v.is_stream ? '· stream' : ''} ${v.gta ? '<span class="badge teal">GTA</span>' : ''}</span></td><td style="width:90px">${v.added ? '<span class="badge ok">added</span>' : `<button class="btn sm" data-add="${esc(v.url)}">Scan</button>`}</td></tr>`).join('')}</table>`;
        $$('[data-add]', x).forEach((b) => b.onclick = () => post('/api/videos', { campaign_id: c.id, url: b.dataset.add, source_id: s.id }).then(() => { toast('Queued'); b.replaceWith('queued'); }).catch(toastErr));
      } catch (e) { x.innerHTML = `<div class="bad-text small">${esc(e.message)}</div>`; }
    };
    list.appendChild(d);
  }
};

function openFacecamPicker(s) {
  const m = modal(`
    <div class="modal-head"><h2 style="margin:0">Facecam crop · ${esc(s.name)}</h2><button class="btn ghost" onclick="closeModal()">✕</button></div>
    <p class="muted small">Drag a box around the streamer's webcam. It is cropped and placed on top of the gameplay in every clip from this source. If no frame is shown yet, grab one (downloads a 3-second sample).</p>
    <div class="row" style="margin-bottom:10px"><button class="btn sm" id="fc-grab">Grab a frame</button><span class="small muted" id="fc-status"></span></div>
    <div class="picker" id="fc-picker"><img id="fc-img" src="/media/frame/${s.id}?t=${Date.now()}" alt=""><canvas id="fc-canvas"></canvas></div>
    <div class="row" style="margin-top:12px"><span class="mono" id="fc-box">${s.facecam ? `x ${s.facecam.x.toFixed(3)} y ${s.facecam.y.toFixed(3)} w ${s.facecam.w.toFixed(3)} h ${s.facecam.h.toFixed(3)}` : 'no box yet'}</span><span class="grow"></span>
      <button class="btn" id="fc-clear">No facecam (gameplay only)</button><button class="btn primary" id="fc-save" ${s.facecam ? '' : 'disabled'}>Save crop</button></div>`);
  let box = s.facecam ? { ...s.facecam } : null;
  const img = $('#fc-img', m), cv = $('#fc-canvas', m);
  img.onerror = () => { $('#fc-status', m).textContent = 'No frame yet. Click "Grab a frame".'; img.style.minHeight = '200px'; };
  const draw = () => {
    cv.width = img.clientWidth || 640; cv.height = img.clientHeight || 360;
    const ctx = cv.getContext('2d'); ctx.clearRect(0, 0, cv.width, cv.height);
    if (!box) return;
    ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.clearRect(box.x * cv.width, box.y * cv.height, box.w * cv.width, box.h * cv.height);
    ctx.strokeStyle = '#1fd1d6'; ctx.lineWidth = 2; ctx.strokeRect(box.x * cv.width, box.y * cv.height, box.w * cv.width, box.h * cv.height);
    $('#fc-box', m).textContent = `x ${box.x.toFixed(3)} y ${box.y.toFixed(3)} w ${box.w.toFixed(3)} h ${box.h.toFixed(3)}`;
    $('#fc-save', m).disabled = false;
  };
  img.onload = draw; if (img.complete) setTimeout(draw, 50);
  let start = null;
  const pos = (e) => { const r = cv.getBoundingClientRect(); return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) }; };
  cv.onmousedown = (e) => { start = pos(e); box = { x: start.x, y: start.y, w: 0, h: 0 }; draw(); };
  cv.onmousemove = (e) => { if (!start) return; const p = pos(e); box = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) }; draw(); };
  window.onmouseup = () => { start = null; };
  $('#fc-grab', m).onclick = async () => {
    $('#fc-status', m).textContent = 'Downloading a sample… (10-30 s)';
    await post(`/api/sources/${s.id}/grab_frame`);
    let tries = 0; const iv = setInterval(async () => {
      tries++; const jobs = await get('/api/jobs?limit=5'); const j = jobs.find((x) => x.type === 'grab_frame' && x.payload.source_id === s.id);
      if (j && j.status === 'done') { clearInterval(iv); img.src = `/media/frame/${s.id}?t=${Date.now()}`; $('#fc-status', m).textContent = 'Frame ready. Drag a box.'; }
      else if (j && j.status === 'error') { clearInterval(iv); $('#fc-status', m).textContent = 'Failed: ' + j.error; }
      else if (tries > 60) clearInterval(iv);
    }, 2000);
  };
  $('#fc-save', m).onclick = async () => { if (!box || box.w < 0.03 || box.h < 0.03) return toast('Box too small', 'warn'); await put(`/api/sources/${s.id}`, { facecam: box, layout: 'stacked' }); closeModal(); toast('Facecam saved. Re-render existing clips to apply it.'); render(); };
  $('#fc-clear', m).onclick = async () => { await put(`/api/sources/${s.id}`, { facecam: null, layout: 'full' }); closeModal(); render(); };
}

// ---------------------------------------------------------------- Pipeline (videos)
const WINDOWS = [['auto', 'Auto (last 2 h for VODs over 4 h)'], ['last2h', 'Last 2 hours'], ['last6h', 'Last 6 hours'], ['whole', 'Whole video'], ['custom', 'Custom start / end']];
const parseHms = (s) => { const p = String(s || '').trim().split(':').map(Number); if (!p.length || p.some(isNaN)) return null; return p.reduce((a, b) => a * 60 + b, 0); };
function windowControls(id, sel = 'auto') {
  return `<select data-win="${id}" style="width:auto">${WINDOWS.map(([k, l]) => `<option value="${k}" ${k === sel ? 'selected' : ''}>${l}</option>`).join('')}</select>
    <span class="row hidden" data-custom="${id}"><input type="text" placeholder="start hh:mm:ss" style="width:120px" data-ws="${id}"><input type="text" placeholder="end hh:mm:ss" style="width:120px" data-we="${id}"></span>`;
}
function readWindow(root, id) {
  const sel = $(`[data-win="${id}"]`, root).value;
  if (sel !== 'custom') return sel;
  const start = parseHms($(`[data-ws="${id}"]`, root).value) || 0, end = parseHms($(`[data-we="${id}"]`, root).value);
  if (end == null) { toast('Enter the window end as hh:mm:ss', 'warn'); return null; }
  return { start, end };
}
function wireWindow(root, id) { $(`[data-win="${id}"]`, root).onchange = (e) => $(`[data-custom="${id}"]`, root).classList.toggle('hidden', e.target.value !== 'custom'); }

VIEWS.videos = async (el, arg) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  const vids = await get(`/api/videos?campaign_id=${c.id}`);
  el.innerHTML = `
    <div class="row between"><div><h1>Pipeline · ${esc(c.name)}</h1><p class="sub">Each video: scan a time window for moments → render clips → review. Long VODs default to their last 2 hours (clips in about 2 minutes); "Scan the rest" continues in 2-hour chunks in the background.</p></div></div>
    <div class="panel"><div class="row"><input type="text" id="v-url" placeholder="Paste a YouTube or Twitch video URL (a stream still in progress works too)" style="flex:1;min-width:300px">${windowControls('new')}<button class="btn primary" id="v-add">Scan</button></div>
      <div class="small muted" style="margin-top:6px">Videos published before the campaign's footage start (${esc((c.source_start || 'not set').slice(0, 10))}) are skipped unless you override.</div></div>
    <div class="panel" id="v-list" style="margin-top:14px">${vids.length ? '' : '<div class="empty">No videos yet. Paste a URL above or add a source.</div>'}</div>`;
  wireWindow(el, 'new');
  const add = async () => { const w = readWindow(el, 'new'); if (w === null) return; try { await post('/api/videos', { campaign_id: c.id, url: $('#v-url').value.trim(), window: w }); $('#v-url').value = ''; toast('Queued for scanning'); render(); } catch (e) { toastErr(e); } };
  $('#v-add').onclick = add; $('#v-url').onkeydown = (e) => { if (e.key === 'Enter') add(); };
  const list = $('#v-list');
  for (const v of vids) {
    const d = document.createElement('div'); d.className = 'video-row';
    const cc = v.clip_counts || {};
    const ranges = (v.scanned_ranges || []).map(([s, e]) => `${fmtTime(s)}–${fmtTime(e)}`).join(', ');
    const busy = ['scanning', 'queued'].includes(v.status);
    d.innerHTML = `
      <div>
        <div class="row"><b>${esc(v.title || v.url)}</b> <span class="badge ${v.status}">${v.status === 'skipped' ? 'outside campaign window' : v.status}</span> ${v.live ? '<span class="badge pink">live now</span>' : (v.is_stream ? '<span class="badge">stream VOD</span>' : '')} ${v.platform === 'twitch' ? '<span class="badge">twitch</span>' : ''}</div>
        <div class="small muted"><a href="${esc(v.url)}" target="_blank">${esc(v.url)}</a> · ${v.channel ? esc(v.channel) + ' · ' : ''}${v.duration ? fmtTime(v.duration) : ''} ${v.published_at ? '· published ' + fmtDate(v.published_at) : ''}</div>
        <div class="small" style="margin-top:6px">${esc(v.stage || '')}${v.error ? ` <span class="bad-text">${esc(v.error.slice(0, 400))}</span>` : ''}</div>
        ${busy ? `<div class="progress" style="margin-top:6px;max-width:420px"><i style="width:${Math.round((v.progress || 0) * 100)}%"></i></div>` : ''}
        <div class="small muted" style="margin-top:6px">${ranges ? 'scanned ' + ranges + ' · ' : ''}${v.moment_count} moments${v.signals.signals_used ? ' from ' + v.signals.signals_used.join(' + ') : ''}${v.signals.chat_messages ? ` · ${fmtNum(v.signals.chat_messages)} chat messages` : ''}${v.signals.chat_error ? ` · <span class="warn-text">chat: ${esc(v.signals.chat_error.slice(0, 120))}</span>` : ''} · clips: ${Object.entries(cc).map(([k, n]) => `${n} ${k}`).join(', ') || 'none'}</div>
        <div class="row" style="margin-top:8px" data-rescan-row><span class="small muted">Scan window:</span>${windowControls('v' + v.id, v.duration > 4 * 3600 ? 'last2h' : 'auto')}<button class="btn sm" data-rescan ${busy ? 'disabled' : ''}>${v.status === 'skipped' ? 'Scan anyway (override date window)' : (v.scanned_ranges?.length ? 'Scan this window' : 'Scan')}</button>
          ${v.unscanned?.length && !busy ? `<button class="btn sm yellow" data-rest>Scan the rest (${v.unscanned.length} × 2 h, background)</button>` : ''}</div>
        <div data-moments></div>
      </div>
      <div class="row" style="align-items:flex-start">
        ${v.moment_count ? `<button class="btn sm" data-show>Show / hide moments</button>` : ''}
        ${cc.pending ? `<a class="btn sm primary" href="#/review/${v.id}">Review ${cc.pending}</a>` : ''}
        <button class="btn sm danger" data-del>Delete</button>
      </div>`;
    wireWindow(d, 'v' + v.id);
    $('[data-rescan]', d).onclick = () => { const w = readWindow(d, 'v' + v.id); if (w === null) return; post(`/api/videos/${v.id}/rescan`, { window: w, override: v.status === 'skipped' }).then(render).catch(toastErr); };
    const rest = $('[data-rest]', d); if (rest) rest.onclick = () => post(`/api/videos/${v.id}/scan_rest`).then((r) => { toast(`${r.queued_chunks} chunks queued`); render(); }).catch(toastErr);
    $('[data-del]', d).onclick = async () => { if (await confirmDlg('Delete this video and all its clips?', 'Delete')) { await del(`/api/videos/${v.id}`); render(); } };
    const momentsBox = $('[data-moments]', d);
    const loadMoments = async () => {
      const ms = await get(`/api/videos/${v.id}/moments`);
      if (!ms.length) { momentsBox.innerHTML = ''; return; }
      const unrendered = ms.filter((m) => !m.clip).length;
      momentsBox.innerHTML = `<div style="margin-top:10px"><div class="row between"><b class="small">Ranked moments</b>
          ${unrendered ? `<button class="btn sm primary" data-next>Render next best</button>` : ''}</div>
        ${ms.map((m) => `<div class="moment ${m.flags?.length ? 'flagged' : ''}">
          <div class="mthumb" style="background-image:url('${m.thumb_url || ''}')"></div>
          <span class="t">${m.ts}</span><span class="score" title="${m.raw_score != null && m.raw_score !== m.score ? 'raw ' + m.raw_score : ''}">${m.score}</span>
          <div class="grow"><div class="reason">${m.reasons.map(esc).join(' · ')}</div>
            ${m.chat_words.length ? `<div class="reason">chat: ${m.chat_words.map(esc).join(', ')}</div>` : ''}
            ${m.flags?.length ? `<div>${m.flags.map((f) => `<span class="badge warn">${f === 'chat_spam' ? 'chat spam' : f === 'low_speech' ? 'low speech' : esc(f)}</span>`).join(' ')}</div>` : ''}</div>
          ${m.clip ? (m.clip.status === 'rendering'
              ? `<span class="badge rendering">${esc(m.render_job?.message || 'rendering')}</span><button class="btn sm danger" data-cancel="${m.clip.id}">Cancel</button>`
              : `<span class="badge ${m.clip.status}">${m.clip.status}</span>${['pending', 'approved'].includes(m.clip.status) ? `<a class="btn sm" href="#/review/${v.id}">Review</a>` : ''}`)
            : `<button class="btn sm" data-render="${m.id}">Render</button>`}
          <a class="small" href="${esc(v.url)}${v.platform === 'youtube' ? '&t=' + Math.round(m.t) : '?t=' + Math.round(m.t) + 's'}" target="_blank">open ↗</a></div>`).join('')}
        <div class="row" style="margin-top:8px"><span class="small muted">Render at a time of your own:</span><input type="text" placeholder="hh:mm:ss" style="width:110px" data-custom-t><button class="btn sm" data-custom>Render</button></div></div>`;
      $$('[data-render]', momentsBox).forEach((b) => b.onclick = () => post(`/api/moments/${b.dataset.render}/render`).then(() => { toast('Rendering'); loadMoments(); }).catch(toastErr));
      $$('[data-cancel]', momentsBox).forEach((b) => b.onclick = () => post(`/api/clips/${b.dataset.cancel}/cancel`).then(() => { toast('Cancelled'); setTimeout(loadMoments, 800); }).catch(toastErr));
      const nx = $('[data-next]', momentsBox); if (nx) nx.onclick = () => post(`/api/videos/${v.id}/render_next`).then(() => { toast('Rendering next best'); loadMoments(); }).catch(toastErr);
      $('[data-custom]', momentsBox).onclick = () => { const t = parseHms($('[data-custom-t]', momentsBox).value); if (t == null) return toast('Enter hh:mm:ss', 'warn'); post(`/api/videos/${v.id}/render_custom`, { t }).then(() => { toast('Rendering'); loadMoments(); }).catch(toastErr); };
    };
    const showBtn = $('[data-show]', d);
    if (showBtn) showBtn.onclick = () => { if (momentsBox.innerHTML) momentsBox.innerHTML = ''; else loadMoments(); };
    if (v.moment_count && (S.openMoments === v.id || vids.length <= 3)) loadMoments();
    list.appendChild(d);
  }
};

// ---------------------------------------------------------------- Review
VIEWS.review = async (el, arg) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  const filter = S.reviewFilter || 'pending';
  const clips = await get(`/api/clips?campaign_id=${c.id}${filter === 'all' ? '' : '&status=' + filter}${arg ? '&video_id=' + arg : ''}`);
  el.innerHTML = `
    <div class="row between"><div><h1>Review · ${esc(c.name)}</h1><p class="sub">Watch every clip. Trim it to open on something worth watching, fix the hook, tick the checklist, then approve or reject. Clips that fail a campaign rule can't be approved.</p></div>
      <div class="chips">${[['pending', 'To review'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['posted', 'Posted'], ['rendering,error', 'Rendering / failed'], ['all', 'All']].map(([k, l]) => `<span class="chip ${filter === k ? 'on' : ''}" data-f="${k}">${l}</span>`).join('')}</div></div>
    <div class="clip-grid" id="clip-grid">${clips.length ? '' : '<div class="empty" style="grid-column:1/-1">Nothing here. Clips appear once a video has been scanned and rendered.</div>'}</div>`;
  $$('.chip[data-f]').forEach((ch) => ch.onclick = () => { S.reviewFilter = ch.dataset.f; render(); });
  const g = $('#clip-grid');
  for (const cl of clips) {
    const d = document.createElement('div'); d.className = 'clip-card ' + cl.status;
    const fails = (cl.rule_results || []).filter((r) => !r.ok).length;
    d.innerHTML = `<div class="thumb" style="background-image:url('${cl.thumb_url || ''}')">
        <div class="overlay-tag"><span class="badge ${cl.status}">${cl.status}</span>${fails ? `<span class="badge bad">${fails} rule${fails > 1 ? 's' : ''} fail</span>` : (cl.status === 'pending' ? '<span class="badge ok">rules ok</span>' : '')}</div>
        ${cl.effective_duration ? `<span class="overlay-dur">${fmtTime(cl.effective_duration)}</span>` : ''}
        ${cl.status === 'rendering' ? '<div class="progress" style="position:absolute;left:10px;right:10px;bottom:12px"><i style="width:50%"></i></div>' : ''}</div>
      <div class="meta"><b>${esc(cl.hook || cl.title || 'Untitled')}</b>${esc((cl.video?.title || '').slice(0, 60))}<br>from ${fmtTime(cl.source_start)} · ${cl.moment ? 'score ' + cl.moment.score : ''}${cl.error ? `<br><span class="bad-text">${esc(cl.error.slice(0, 120))}</span>` : ''}</div>`;
    d.onclick = () => { if (cl.status !== 'rendering') openClip(cl.id); };
    g.appendChild(d);
  }
};

async function openClip(id) {
  let cl = await get(`/api/clips/${id}`);
  const camp = S.campaigns.find((x) => x.id === cl.campaign_id) || campaign();
  const plats = camp.platforms || Object.keys(cl.post_text || {});
  let tab = plats[0] || 'tiktok';
  const m = modal(`<div class="modal-head"><div><h2 style="margin:0">${esc(cl.video?.title || 'Clip')}</h2><span class="small muted">from ${fmtTime(cl.source_start)} · ${cl.moment ? cl.moment.reasons.map(esc).join(' · ') : ''}</span></div><button class="btn ghost" onclick="closeModal()">✕</button></div>
    <div class="review-layout">
      <div>
        <video id="rv" src="${cl.video_url}" controls playsinline></video>
        <div class="trim panel" style="margin-top:10px">
          <div class="row between"><b>Trim</b><span id="trim-len" class="small"></span></div>
          <label class="small muted">Start <span id="ts-v"></span></label><input type="range" id="ts" min="0" max="${cl.duration}" step="0.1" value="${cl.trim_start}">
          <label class="small muted">End <span id="te-v"></span></label><input type="range" id="te" min="0" max="${cl.duration}" step="0.1" value="${cl.trim_end || cl.duration}">
          <div class="row"><button class="btn sm" id="ts-now">Start = here</button><button class="btn sm" id="te-now">End = here</button><button class="btn sm" id="trim-play">▶ Play trimmed</button><span class="grow"></span><button class="btn sm primary" id="trim-save">Save trim</button></div>
          <div class="small muted">Need more lead-in/out? <button class="btn sm ghost" id="shift-back">Re-cut 15 s earlier</button> <button class="btn sm ghost" id="shift-fwd">Re-cut 15 s later</button></div>
        </div>
      </div>
      <div>
        <div class="panel">
          <div class="row between"><b>Rule checks</b><span class="small muted">machine-checked before approval</span></div>
          <div id="rules"></div>
        </div>
        <div class="panel" style="margin-top:12px">
          <div class="field"><label>Hook (a real sentence from the clip; stays editable)</label><input type="text" id="hook" value="${esc(cl.hook)}">
            <div class="chips" id="hook-cands"></div></div>
          <div class="tabs" id="pt-tabs">${plats.map((p) => `<button data-p="${p}" class="${p === tab ? 'on' : ''}">${p}</button>`).join('')}</div>
          <div id="pt-body"></div>
          <div class="row" style="margin-top:8px"><button class="btn sm" id="pt-regen">Rebuild post text from hook</button><button class="btn sm primary" id="pt-save">Save text</button></div>
        </div>
        <div class="panel" style="margin-top:12px">
          <b>Review checklist</b>
          <div id="checks">${cl.checklist_items.map((t, i) => `<label class="check"><input type="checkbox" data-i="${i}" ${cl.checklist && cl.checklist[i] ? 'checked' : ''}> ${esc(t)}</label>`).join('')}</div>
          <div class="row" style="margin-top:12px">
            <button class="btn primary" id="approve" ${cl.status === 'posted' ? 'disabled' : ''}>Approve</button>
            <button class="btn danger" id="reject" ${cl.status === 'posted' ? 'disabled' : ''}>Reject</button>
            <span class="grow"></span>
            <button class="btn sm" id="rerender">Re-render</button>
            <button class="btn sm" id="reveal">Reveal file</button>
            <button class="btn sm danger" id="delete">Delete</button>
          </div>
          <div class="small muted" style="margin-top:8px">Approving triggers the final trimmed export. Posting happens on the Publish page.</div>
        </div>
      </div>
    </div>`);
  const v = $('#rv', m), ts = $('#ts', m), te = $('#te', m);
  const minLen = camp.min_len || 61, maxLen = camp.max_len || 0;
  const updTrim = () => {
    let a = Number(ts.value), b = Number(te.value); if (b < a + 1) { b = a + 1; te.value = b; }
    $('#ts-v', m).textContent = fmtTime(a) + '.' + Math.round(a % 1 * 10); $('#te-v', m).textContent = fmtTime(b) + '.' + Math.round(b % 1 * 10);
    const len = b - a; const ok = len >= minLen && (!maxLen || len <= maxLen);
    $('#trim-len', m).innerHTML = `<span class="${ok ? 'ok-text' : 'bad-text'}">${len.toFixed(1)} s</span> (needs ${minLen}${maxLen ? '–' + maxLen : '+'} s)`;
  };
  ts.oninput = () => { updTrim(); v.currentTime = Number(ts.value); };
  te.oninput = () => { updTrim(); v.currentTime = Math.max(0, Number(te.value) - 1); };
  updTrim();
  $('#ts-now', m).onclick = () => { ts.value = v.currentTime.toFixed(1); updTrim(); };
  $('#te-now', m).onclick = () => { te.value = v.currentTime.toFixed(1); updTrim(); };
  let looping = false;
  $('#trim-play', m).onclick = () => { looping = true; v.currentTime = Number(ts.value); v.play(); };
  v.ontimeupdate = () => { if (looping && v.currentTime >= Number(te.value)) { v.pause(); looping = false; } };
  const save = async (body) => { try { cl = await put(`/api/clips/${cl.id}`, body); renderRules(); return true; } catch (e) { toastErr(e); return false; } };
  $('#trim-save', m).onclick = async () => { if (await save({ trim_start: Number(ts.value), trim_end: Number(te.value) })) toast('Trim saved'); };
  $('#shift-back', m).onclick = () => post(`/api/clips/${cl.id}/rerender`, { shift: -15 }).then(() => { closeModal(); toast('Re-cutting'); render(); }).catch(toastErr);
  $('#shift-fwd', m).onclick = () => post(`/api/clips/${cl.id}/rerender`, { shift: 15 }).then(() => { closeModal(); toast('Re-cutting'); render(); }).catch(toastErr);
  const renderRules = () => {
    $('#rules', m).innerHTML = (cl.rule_results || []).map((r) => `<div class="rule ${r.ok ? '' : 'bad'}"><span class="dot"></span><span>${esc(r.detail)}</span></div>`).join('') || '<div class="muted small">Not checked yet</div>';
  };
  renderRules();
  get(`/api/clips/${cl.id}/hooks`).then((cands) => { $('#hook-cands', m).innerHTML = cands.map((h) => `<span class="chip" title="at ${fmtTime(h.t)}">${esc(h.text)}</span>`).join(''); $$('#hook-cands .chip', m).forEach((ch) => ch.onclick = () => { $('#hook', m).value = ch.textContent; }); });
  const ptBody = () => {
    const pt = (cl.post_text || {})[tab] || { title: '', text: '' };
    $('#pt-body', m).innerHTML = `${tab === 'youtube' ? `<div class="field"><label>Title (≤100)</label><input type="text" id="pt-title" value="${esc(pt.title)}"></div>` : ''}
      <div class="field"><label>${tab === 'youtube' ? 'Description' : 'Caption'} · must mention ${(camp.required_names || []).join(', ')} and include ${(camp.required_hashtags || []).join(' ')}</label><textarea id="pt-text">${esc(pt.text)}</textarea></div>`;
  };
  ptBody();
  $$('#pt-tabs button', m).forEach((b) => b.onclick = () => { collectPt(); tab = b.dataset.p; $$('#pt-tabs button', m).forEach((x) => x.classList.toggle('on', x === b)); ptBody(); });
  const collectPt = () => { const pt = { ...(cl.post_text || {}) }; pt[tab] = { title: $('#pt-title', m) ? $('#pt-title', m).value : '', text: $('#pt-text', m).value }; cl.post_text = pt; };
  $('#pt-save', m).onclick = async () => { collectPt(); if (await save({ post_text: cl.post_text, hook: $('#hook', m).value, title: (cl.post_text.youtube || {}).title || $('#hook', m).value })) toast('Post text saved'); };
  $('#pt-regen', m).onclick = async () => { if (await save({ hook: $('#hook', m).value })) { ptBody(); toast('Rebuilt'); } };
  const checks = () => Object.fromEntries($$('#checks input', m).map((i) => [i.dataset.i, i.checked]));
  $('#approve', m).onclick = async () => {
    collectPt();
    if (!(await save({ post_text: cl.post_text, hook: $('#hook', m).value, trim_start: Number(ts.value), trim_end: Number(te.value), checklist: checks() }))) return;
    try { await post(`/api/clips/${cl.id}/approve`, { checklist: checks() }); closeModal(); toast('Approved. Final export is being prepared.'); render(); }
    catch (e) { const f = e.data && e.data.failed; toast((e.data && e.data.message) || e.message + (f && f.length ? ': ' + f.map((x) => x.detail).join('; ') : ''), 'err'); cl = await get(`/api/clips/${cl.id}`); renderRules(); }
  };
  $('#reject', m).onclick = () => post(`/api/clips/${cl.id}/reject`).then(() => { closeModal(); render(); }).catch(toastErr);
  $('#rerender', m).onclick = () => post(`/api/clips/${cl.id}/rerender`).then(() => { closeModal(); toast('Re-rendering'); render(); }).catch(toastErr);
  $('#reveal', m).onclick = () => post(`/api/clips/${cl.id}/reveal`).catch(toastErr);
  $('#delete', m).onclick = async () => { try { await del(`/api/clips/${cl.id}`); closeModal(); render(); } catch (e) { if (e.status === 409 && await confirmDlg(e.data.message, 'Delete anyway')) { await del(`/api/clips/${cl.id}?force=true`); closeModal(); render(); } else toastErr(e); } };
}

// ---------------------------------------------------------------- Publish
VIEWS.publish = async (el) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  const [clips, posts, accounts] = await Promise.all([get(`/api/clips?campaign_id=${c.id}&status=approved,posted`), get(`/api/posts?campaign_id=${c.id}`), get('/api/accounts')]);
  const usable = accounts.filter((a) => (c.platforms || []).includes(a.platform) && (!a.campaign_ids?.length || a.campaign_ids.includes(c.id)));
  el.innerHTML = `
    <h1>Publish · ${esc(c.name)}</h1><p class="sub">Pick accounts, then Post now or Schedule. Each clip goes to at most one account per platform.</p>
    <div class="callout">Scheduled posts fire from this app's own scheduler, so Clip Dashboard has to be running at that time. YouTube (direct) and the posting service schedule on their side and don't need the app.</div>
    ${usable.length ? '' : `<div class="callout pink">No accounts for this campaign's platforms yet. <a href="#/accounts">Add accounts</a> (assisted posting needs no setup).</div>`}
    <h2>Approved clips</h2>
    <div class="clip-grid" id="pub-grid">${clips.length ? '' : '<div class="empty" style="grid-column:1/-1">Approve clips on the Review page first.</div>'}</div>
    <h2>Queue</h2>
    <div class="panel" id="queue"></div>`;
  const g = $('#pub-grid');
  for (const cl of clips) {
    const d = document.createElement('div'); d.className = 'clip-card ' + cl.status;
    const done = cl.posts.filter((p) => !['failed', 'cancelled'].includes(p.status)).map((p) => p.platform);
    d.innerHTML = `<div class="thumb" style="background-image:url('${cl.thumb_url || ''}')"><div class="overlay-tag">${done.map((p) => `<span class="badge posted">${p}</span>`).join('')}${cl.final_path ? '' : '<span class="badge warn">exporting…</span>'}</div><span class="overlay-dur">${fmtTime(cl.effective_duration)}</span></div>
      <div class="meta"><b>${esc(cl.hook || cl.title)}</b><div class="row" style="margin-top:6px"><button class="btn sm primary" data-post ${usable.length ? '' : 'disabled'}>Post / schedule</button><button class="btn sm" data-view>Preview</button></div></div>`;
    $('[data-post]', d).onclick = () => openPostDialog(cl, usable, c);
    $('[data-view]', d).onclick = () => openClip(cl.id);
    g.appendChild(d);
  }
  renderQueue($('#queue'), posts);
};

function renderQueue(el, posts) {
  if (!posts.length) { el.innerHTML = '<div class="muted">No posts yet.</div>'; return; }
  el.innerHTML = `<table><tr><th>When</th><th>Clip</th><th>Account</th><th>Status</th><th>Link</th><th></th></tr>${posts.map((p) => `<tr>
    <td class="small">${p.scheduled_at ? fmtDate(p.scheduled_at) : (p.posted_at ? fmtDate(p.posted_at) : fmtDate(p.created_at))}${p.tz ? `<br><span class="muted">${esc(p.tz)}</span>` : ''}</td>
    <td class="small">${esc(p.clip?.hook || p.title || '#' + p.clip_id)}</td>
    <td class="small">${esc(p.account?.label || '')}<br><span class="badge ${p.route}">${p.route}</span></td>
    <td class="small"><span class="badge ${p.status}">${p.status}</span><br><span class="muted">${esc(p.state_detail || p.error || '')}</span></td>
    <td class="small">${p.url ? `<a href="${esc(p.url)}" target="_blank">open ↗</a>` : (['manual', 'private', 'draft'].includes(p.status) ? `<div class="row"><input type="text" placeholder="paste post link" data-link="${p.id}" style="width:170px"><button class="btn sm" data-mark="${p.id}">Live</button></div>` : '')}</td>
    <td class="small"><div class="row">
      ${p.status === 'manual' ? `<button class="btn sm" data-open="${esc(p.account?.upload_page || '')}">Open upload page</button><button class="btn sm" data-copy="${esc((p.title ? p.title + '\n\n' : '') + p.post_text)}">Copy caption</button><button class="btn sm" data-reveal="${p.clip_id}">Reveal file</button>` : ''}
      ${['failed', 'private', 'draft'].includes(p.status) ? `<button class="btn sm" data-retry="${p.id}">Retry</button>` : ''}
      ${['pending', 'scheduled', 'draft', 'private'].includes(p.status) && p.external_id ? `<button class="btn sm" data-check="${p.id}">Check</button>` : ''}
      ${['scheduled', 'failed', 'manual', 'pending'].includes(p.status) ? `<button class="btn sm danger" data-cancel="${p.id}">Cancel</button>` : ''}
      ${['cancelled', 'failed'].includes(p.status) ? `<button class="btn sm ghost" data-delp="${p.id}">Remove</button>` : ''}
    </div></td></tr>`).join('')}</table>`;
  $$('[data-mark]', el).forEach((b) => b.onclick = () => post(`/api/posts/${b.dataset.mark}/mark_live`, { url: $(`[data-link="${b.dataset.mark}"]`, el).value }).then(() => { toast('Marked live; submission timer started'); render(); }).catch(toastErr));
  $$('[data-open]', el).forEach((b) => b.onclick = () => window.open(b.dataset.open, '_blank'));
  $$('[data-copy]', el).forEach((b) => b.onclick = () => navigator.clipboard.writeText(b.dataset.copy).then(() => toast('Caption copied')));
  $$('[data-reveal]', el).forEach((b) => b.onclick = () => post(`/api/clips/${b.dataset.reveal}/reveal`).catch(toastErr));
  $$('[data-retry]', el).forEach((b) => b.onclick = () => post(`/api/posts/${b.dataset.retry}/retry`).then(render).catch(toastErr));
  $$('[data-check]', el).forEach((b) => b.onclick = () => post(`/api/posts/${b.dataset.check}/check`).then(() => toast('Checking…')).catch(toastErr));
  $$('[data-cancel]', el).forEach((b) => b.onclick = async () => { try { await post(`/api/posts/${b.dataset.cancel}/cancel`); render(); } catch (e) { if (e.status === 409 && await confirmDlg(e.data.message, 'Cancel anyway')) { await post(`/api/posts/${b.dataset.cancel}/cancel?force=true`); render(); } else toastErr(e); } });
  $$('[data-delp]', el).forEach((b) => b.onclick = () => del(`/api/posts/${b.dataset.delp}`).then(render).catch(toastErr));
}

function openPostDialog(cl, accounts, camp) {
  const m = modal(`<div class="modal-head"><h2 style="margin:0">Post “${esc(cl.hook || cl.title)}”</h2><button class="btn ghost" onclick="closeModal()">✕</button></div>
    <div id="acc-list">${accounts.map((a) => `<div class="option-box"><label class="check"><input type="checkbox" data-acc="${a.id}" ${cl.posts.some((p) => p.platform === a.platform && !['failed', 'cancelled'].includes(p.status)) ? 'disabled' : ''}> <b>${esc(a.label)}</b> <span class="badge ${a.route}">${a.route}</span> <span class="badge">${a.platform}</span> ${a.status !== 'ready' && a.status !== 'connected' ? `<span class="badge warn">${a.status}</span>` : ''}</label>
      <div class="small muted">${esc(a.route_notes)}</div>
      <div data-opts="${a.id}" class="hidden" style="margin-top:8px">
        ${a.platform === 'youtube' ? `<div class="field"><label>Title</label><input type="text" data-title="${a.id}" value="${esc((cl.post_text?.youtube || {}).title || cl.title)}"></div>` : ''}
        <div class="field"><label>${a.platform === 'youtube' ? 'Description' : 'Caption'}</label><textarea data-text="${a.id}">${esc((cl.post_text?.[a.platform] || {}).text || '')}</textarea></div>
        ${a.options_schema.map((o) => optField(a.id, o)).join('')}
      </div></div>`).join('')}</div>
    <div class="panel" style="margin-top:12px">
      <div class="row"><label class="check"><input type="radio" name="mode" value="now" checked> Post now</label><label class="check"><input type="radio" name="mode" value="schedule"> Schedule</label></div>
      <div id="sched" class="hidden row" style="margin-top:8px"><input type="datetime-local" id="sch-at" style="width:220px"><select id="sch-tz" style="width:260px"></select></div>
    </div>
    <div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn primary" id="go">Confirm</button></div>`);
  $$('[data-acc]', m).forEach((cb) => cb.onchange = () => $(`[data-opts="${cb.dataset.acc}"]`, m).classList.toggle('hidden', !cb.checked));
  $$('input[name=mode]', m).forEach((r) => r.onchange = () => $('#sched', m).classList.toggle('hidden', $('input[name=mode]:checked', m).value !== 'schedule'));
  const tzs = (Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : [localTz]);
  $('#sch-tz', m).innerHTML = tzs.map((t) => `<option ${t === localTz ? 'selected' : ''}>${t}</option>`).join('');
  const dflt = new Date(Date.now() + 3600e3); dflt.setMinutes(0, 0, 0); $('#sch-at', m).value = new Date(dflt.getTime() - dflt.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $('#go', m).onclick = async () => {
    const ids = $$('[data-acc]:checked', m).map((x) => Number(x.dataset.acc));
    if (!ids.length) return toast('Pick at least one account', 'warn');
    const body = { clip_id: cl.id, account_ids: ids, mode: $('input[name=mode]:checked', m).value, scheduled_at: $('#sch-at', m).value, tz: $('#sch-tz', m).value, post_text: {}, titles: {}, options: {} };
    for (const id of ids) {
      body.post_text[id] = $(`[data-text="${id}"]`, m).value; const t = $(`[data-title="${id}"]`, m); if (t) body.titles[id] = t.value;
      body.options[id] = {};
      $$(`[data-opt-acc="${id}"]`, m).forEach((i) => { body.options[id][i.dataset.optKey] = i.type === 'checkbox' ? i.checked : (i.value === '' ? null : i.value); });
    }
    try { const r = await post('/api/posts', body); closeModal(); toast(`${r.length} post${r.length > 1 ? 's' : ''} ${body.mode === 'now' ? 'queued' : 'scheduled'}`); render(); } catch (e) { toastErr(e); }
  };
}
function optField(aid, o) {
  if (o.type === 'note') return `<div class="small muted" style="margin:6px 0">${esc(o.label)}</div>`;
  if (o.type === 'bool') return `<label class="check small"><input type="checkbox" data-opt-acc="${aid}" data-opt-key="${o.key}" ${o.default ? 'checked' : ''} ${o.disabled ? 'disabled' : ''}> ${esc(o.label)}${o.disabled ? ' (disabled by the account)' : ''}</label>`;
  return `<div class="field"><label>${esc(o.label)}</label><select data-opt-acc="${aid}" data-opt-key="${o.key}">${o.default === null ? '<option value="">— choose —</option>' : ''}${o.choices.map(([v, l]) => `<option value="${esc(v)}" ${o.default === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></div>`;
}

// ---------------------------------------------------------------- Submissions
VIEWS.submissions = async (el) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  const posts = (await get(`/api/posts?campaign_id=${c.id}&status=live`)).sort((a, b) => (a.submitted_at ? 1 : 0) - (b.submitted_at ? 1 : 0) || (a.submission_deadline || '').localeCompare(b.submission_deadline || ''));
  el.innerHTML = `<h1>Submissions · ${esc(c.name)}</h1><p class="sub">Every live post must be submitted to the campaign within ${c.submit_minutes} minutes. Copy the link, open the submission page, paste, then tick Submitted. Never edit or delete a submitted post.</p>
    ${c.submission_url ? '' : '<div class="callout pink">This campaign has no submission page URL yet. Add it in the campaign rules.</div>'}
    <div class="panel" id="subs">${posts.length ? '' : '<div class="muted">No live posts yet.</div>'}</div>`;
  const box = $('#subs');
  if (!posts.length) return;
  box.innerHTML = `<table><tr><th>Countdown</th><th>Post</th><th>Link</th><th>Actions</th></tr>${posts.map((p) => `<tr data-row="${p.id}">
    <td><div class="countdown" data-dl="${p.submission_deadline || ''}" data-sub="${p.submitted_at ? 1 : ''}"></div><div class="small muted">live ${fmtDate(p.posted_at)}</div></td>
    <td class="small"><b>${esc(p.clip?.hook || p.title)}</b><br>${esc(p.account?.label || '')} · <span class="badge">${p.platform}</span></td>
    <td class="small"><a href="${esc(p.url || '')}" target="_blank">${esc((p.url || '').slice(0, 60))}</a></td>
    <td><div class="row"><button class="btn sm" data-copy="${esc(p.url || '')}">Copy link</button>${c.submission_url ? `<button class="btn sm yellow" data-open>Copy + open submission page</button>` : ''}
      ${p.submitted_at ? `<span class="badge ok">submitted ${fmtDate(p.submitted_at)}</span>` : `<button class="btn sm primary" data-submitted="${p.id}">Submitted ✓</button>`}</div></td></tr>`).join('')}</table>`;
  $$('[data-copy]', box).forEach((b) => b.onclick = () => navigator.clipboard.writeText(b.dataset.copy).then(() => toast('Link copied')));
  $$('[data-open]', box).forEach((b) => b.onclick = () => { navigator.clipboard.writeText($('[data-copy]', b.parentElement).dataset.copy); window.open(c.submission_url, '_blank'); });
  $$('[data-submitted]', box).forEach((b) => b.onclick = () => post(`/api/posts/${b.dataset.submitted}/submitted`).then(render).catch(toastErr));
  const tick = () => $$('.countdown', box).forEach((d) => {
    if (d.dataset.sub) { d.textContent = 'done'; d.className = 'countdown'; return; }
    if (!d.dataset.dl) { d.textContent = '—'; return; }
    const left = (new Date(d.dataset.dl) - Date.now()) / 1000;
    d.textContent = (left < 0 ? '-' : '') + fmtTime(Math.abs(left)); d.className = 'countdown ' + (left < 0 ? 'late' : left < 600 ? 'soon' : '');
  });
  tick(); clearInterval(S.tick); S.tick = setInterval(tick, 1000);
};

// ---------------------------------------------------------------- Stats
VIEWS.stats = async (el) => {
  const c = campaign();
  const st = await get(`/api/stats${c ? '?campaign_id=' + c.id : ''}`);
  el.innerHTML = `<div class="row between"><div><h1>Stats</h1><p class="sub">Views where the platform API provides them (direct and service routes). Assisted posts show no views. Estimated earnings use the campaign's rate and cap; pool campaigns pay by share of approved views, which the app can't know.</p></div><button class="btn" id="refresh">Refresh views</button></div>
    <div class="stat-tiles">${st.campaigns.map((x) => `<div class="tile teal"><div class="n">${fmtNum(x.views)}</div><div class="l">${esc(x.campaign.name)} views over ${x.posts} live posts</div></div>
      <div class="tile yellow"><div class="n">${x.campaign.pay_model === 'pool' ? '$' + fmtNum(x.campaign.pool_budget) + ' pool' : '$' + x.earnings.toFixed(2)}</div><div class="l">${x.campaign.pay_model === 'pool' ? `pool · ${x.campaign.min_views ? fmtNum(x.views) + ' / ' + fmtNum(x.campaign.min_views) + ' views needed' : 'split by view share'}` : 'estimated earnings'}</div></div>`).join('') || '<div class="muted">No live posts yet.</div>'}</div>
    <div class="panel" style="margin-top:14px"><table><tr><th>Post</th><th>Platform</th><th>Views</th><th>Est.</th><th>Checked</th></tr>${st.posts.map((p) => `<tr><td class="small"><a href="${esc(p.url || '#')}" target="_blank">${esc(p.clip?.hook || p.title)}</a></td><td><span class="badge">${p.platform}</span> <span class="badge ${p.route}">${p.route}</span></td><td>${p.views == null ? '<span class="muted">n/a</span>' : fmtNum(p.views)}</td><td>${p.earnings_estimate ? '$' + p.earnings_estimate.toFixed(2) : ''}</td><td class="small muted">${p.views_checked_at ? fmtDate(p.views_checked_at) : ''}</td></tr>`).join('')}</table></div>`;
  $('#refresh').onclick = () => post(`/api/stats/refresh${c ? '?campaign_id=' + c.id : ''}`).then(() => toast('Refreshing in the background')).catch(toastErr);
};

// ---------------------------------------------------------------- Accounts
VIEWS.accounts = async (el) => {
  const [accounts, routes] = await Promise.all([get('/api/accounts'), get('/api/routes')]);
  el.innerHTML = `<div class="row between"><div><h1>Accounts</h1><p class="sub">Each account posts through one route. Direct = your own developer app on the platform's official API. Service = an audited third-party posting API. Assisted = you upload, the app does the rest.</p></div><button class="btn primary" id="add-acc">Add account</button></div>
    <div class="grid cols-2" id="acc-grid">${accounts.length ? '' : '<div class="empty">No accounts. Add one; assisted posting works immediately.</div>'}</div>
    <h2>What each route can do</h2><div class="panel"><table><tr><th>Route</th><th>Public posts</th><th>Scheduling</th><th>Notes</th></tr>${Object.values(routes).map((r) => `<tr><td><span class="badge">${r.platform}</span> <span class="badge ${r.route}">${r.route}</span></td><td>${r.public ? '<span class="ok-text">yes</span>' : '<span class="warn-text">private only until audited</span>'}</td><td>${r.native_schedule ? 'on the platform/service' : 'this app (must be running)'}</td><td class="small muted">${esc(r.notes)}</td></tr>`).join('')}</table></div>`;
  const g = $('#acc-grid');
  for (const a of accounts) {
    const d = document.createElement('div'); d.className = 'panel';
    d.innerHTML = `<div class="row between"><div><b>${esc(a.label)}</b> ${a.handle ? `<span class="muted">${esc(a.handle)}</span>` : ''}</div><div><span class="badge">${a.platform}</span> <span class="badge ${a.route}">${a.route}</span> <span class="badge ${a.status}">${a.status}</span></div></div>
      <div class="small muted" style="margin-top:6px">${esc(a.status_detail || a.route_notes)}</div>
      <div class="row" style="margin-top:10px">${a.route !== 'assisted' ? `<button class="btn sm primary" data-connect>Connect</button><button class="btn sm" data-test>Test</button>` : ''}<button class="btn sm" data-camps>Campaigns: ${a.campaign_ids?.length ? a.campaign_ids.map((id) => esc(S.campaigns.find((c) => c.id === id)?.name || id)).join(', ') : 'all'}</button><span class="grow"></span><button class="btn sm danger" data-del>Remove</button></div>`;
    const cb = $('[data-connect]', d); if (cb) cb.onclick = async () => { try { const r = await post(`/api/accounts/${a.id}/connect`); if (r.auth_url) { window.open(r.auth_url, '_blank'); toast('Approve access in the new tab, then click Test'); } else toast(r.detail || 'Connecting…'); setTimeout(render, 2500); } catch (e) { toastErr(e); } };
    const tb = $('[data-test]', d); if (tb) tb.onclick = async () => { tb.disabled = true; try { const r = await post(`/api/accounts/${a.id}/test`); toast(r.detail, r.ok ? '' : 'err'); render(); } catch (e) { toastErr(e); } };
    $('[data-camps]', d).onclick = () => {
      const m = modal(`<div class="modal-head"><h2 style="margin:0">Campaigns for ${esc(a.label)}</h2><button class="btn ghost" onclick="closeModal()">✕</button></div><p class="muted small">Leave all unticked to allow every campaign.</p>${S.campaigns.map((c) => `<label class="check"><input type="checkbox" value="${c.id}" ${a.campaign_ids?.includes(c.id) ? 'checked' : ''}> ${esc(c.name)}</label>`).join('')}<div class="row" style="justify-content:flex-end"><button class="btn primary" id="cs">Save</button></div>`, { narrow: true });
      $('#cs', m).onclick = () => put(`/api/accounts/${a.id}`, { campaign_ids: $$('input:checked', m).map((i) => Number(i.value)) }).then(() => { closeModal(); render(); });
    };
    $('[data-del]', d).onclick = async () => { if (await confirmDlg(`Remove ${a.label}? Its stored token is deleted.`, 'Remove')) { await del(`/api/accounts/${a.id}`); render(); } };
    g.appendChild(d);
  }
  $('#add-acc').onclick = () => {
    const m = modal(`<div class="modal-head"><h2 style="margin:0">Add account</h2><button class="btn ghost" onclick="closeModal()">✕</button></div>
      <div class="field"><label>Platform</label><select id="a-plat"><option value="youtube">YouTube</option><option value="tiktok">TikTok</option><option value="instagram">Instagram</option><option value="x">X</option></select></div>
      <div class="field"><label>Route</label><select id="a-route"><option value="assisted">Assisted (always works)</option><option value="direct">Direct (your own developer app)</option><option value="service">Posting service (Zernio)</option></select></div>
      <div class="field"><label>Label</label><input type="text" id="a-label" placeholder="Vice City Daily Clips · TikTok"></div>
      <div class="row" style="justify-content:flex-end"><button class="btn primary" id="a-save">Add</button></div>`, { narrow: true });
    $('#a-save', m).onclick = () => post('/api/accounts', { platform: $('#a-plat', m).value, route: $('#a-route', m).value, label: $('#a-label', m).value || undefined }).then(() => { closeModal(); render(); }).catch(toastErr);
  };
};

// ---------------------------------------------------------------- Setup
VIEWS.setup = async (el) => {
  const s = await get('/api/setup/checks');
  const allOk = s.checks.filter((c) => c.name !== 'TwitchDownloaderCLI').every((c) => c.ok);
  el.innerHTML = `<h1>Setup</h1><p class="sub">First run: check the tools, then connect accounts. Everything downloads into the app's data folder; nothing needs a terminal after install.</p>
    <div class="panel"><h3 style="margin-top:0">Dependencies</h3>${s.checks.map((c) => `<div class="rule ${c.ok ? '' : 'bad'}"><span class="dot"></span><b style="min-width:170px">${esc(c.name)}</b><span class="grow">${esc(c.detail)}${c.fix && !c.ok ? `<br><span class="small muted">${esc(c.fix)}</span>` : ''}</span>${c.action ? `<button class="btn sm" data-action="${c.action}">Download</button>` : ''}</div>`).join('')}
      ${S.state?.jobs_running?.length ? `<div class="small muted" style="margin-top:8px">⟳ ${esc(S.state.jobs_running[0].message)}</div>` : ''}</div>
    <div class="grid cols-2" style="margin-top:14px">
      <div class="panel"><h3 style="margin-top:0">YouTube (direct route)</h3>
        <ol class="small muted" style="padding-left:18px"><li>console.cloud.google.com → new project → enable <b>YouTube Data API v3</b>.</li><li>OAuth consent screen → External → add your Google account as a test user.</li><li>Credentials → OAuth client ID → <b>Desktop app</b> → download the JSON.</li><li>Paste the file's contents below. Testing-mode apps must reconnect every 7 days; publish the consent screen to avoid that.</li></ol>
        <div class="field"><label>client_secret JSON ${s.secrets.youtube_client_secret ? '<span class="ok-text">(saved)</span>' : ''}</label><textarea id="sec-yt" placeholder='{"installed":{"client_id":...}}'></textarea></div></div>
      <div class="panel"><h3 style="margin-top:0">TikTok (direct route)</h3>
        <ol class="small muted" style="padding-left:18px"><li>developers.tiktok.com → create an app → add <b>Login Kit</b> and <b>Content Posting API</b> with scopes user.info.basic, video.publish, video.upload.</li><li>Platform: <b>Desktop</b>. Redirect URI: <code>${esc(s.callback_urls.tiktok)}</code></li><li>Until TikTok audits the app, posts are "only me" and your TikTok account must be private.</li></ol>
        <div class="field"><label>Client key ${s.secrets.tiktok_client_key ? `<span class="ok-text">(saved ${esc(s.secrets.tiktok_client_key)})</span>` : ''}</label><input type="text" id="sec-ttk"></div>
        <div class="field"><label>Client secret ${s.secrets.tiktok_client_secret ? '<span class="ok-text">(saved)</span>' : ''}</label><input type="password" id="sec-tts"></div></div>
      <div class="panel"><h3 style="margin-top:0">Instagram (direct route)</h3>
        <ol class="small muted" style="padding-left:18px"><li>developers.facebook.com → create a <b>Business</b> app → add Facebook Login for Business.</li><li>Your Instagram must be a professional account linked to a Facebook Page you manage, and your account must have a role on the app.</li><li>Valid OAuth redirect URI: <code>${esc(s.callback_urls.instagram)}</code> (allowed while the app is in Development mode).</li></ol>
        <div class="field"><label>App ID ${s.secrets.facebook_app_id ? `<span class="ok-text">(saved ${esc(s.secrets.facebook_app_id)})</span>` : ''}</label><input type="text" id="sec-fbid"></div>
        <div class="field"><label>App secret ${s.secrets.facebook_app_secret ? '<span class="ok-text">(saved)</span>' : ''}</label><input type="password" id="sec-fbs"></div></div>
      <div class="panel"><h3 style="margin-top:0">Posting service (Zernio)</h3>
        <p class="small muted">An audited third-party API: public TikTok/Instagram posts and server-side scheduling without your own developer apps. First two connected accounts are free. Create a key at zernio.com → dashboard → API keys, connect your social accounts on their dashboard, then add accounts here with route "service".</p>
        <div class="field"><label>API key ${s.secrets.service_api_key ? `<span class="ok-text">(saved ${esc(s.secrets.service_api_key)})</span>` : ''}</label><input type="password" id="sec-svc"></div></div>
    </div>
    <div class="row" style="margin-top:14px"><button class="btn primary" id="sec-save">Save credentials</button><span class="grow"></span>${allOk ? '<button class="btn yellow" id="setup-done">Setup complete → go to Overview</button>' : '<span class="muted small">Fix the red items above to finish setup.</span>'}</div>
    <p class="small muted" style="margin-top:14px">Credentials are stored in <code>data/secrets.json</code> (gitignored, user-only permissions), never in the repository. See <code>.env.example</code> for the list.</p>`;
  $$('[data-action]').forEach((b) => b.onclick = () => post('/api/setup/action', { action: b.dataset.action }).then(() => { toast('Downloading in the background'); b.disabled = true; }).catch(toastErr));
  $('#sec-save').onclick = async () => {
    const body = {}; const take = (id, key) => { const v = $(id).value.trim(); if (v) body[key] = v; };
    take('#sec-yt', 'youtube_client_secret'); take('#sec-ttk', 'tiktok_client_key'); take('#sec-tts', 'tiktok_client_secret'); take('#sec-fbid', 'facebook_app_id'); take('#sec-fbs', 'facebook_app_secret'); take('#sec-svc', 'service_api_key');
    if (!Object.keys(body).length) return toast('Nothing to save', 'warn');
    try { await post('/api/setup/secrets', body); toast('Saved'); render(); } catch (e) { toastErr(e); }
  };
  const done = $('#setup-done'); if (done) done.onclick = () => post('/api/setup/done').then(() => { S.state.setup_done = true; location.hash = '#/'; });
};

// ---------------------------------------------------------------- Settings
VIEWS.settings = async (el) => {
  const s = await get('/api/settings');
  const F = [
    ['lead_in', 'Seconds before the spike the clip starts', 'number'], ['download_length', 'Seconds downloaded per moment (trim inside this)', 'number'],
    ['max_moments', 'Max moments per video', 'number'], ['auto_render_top', 'Auto-render the top N moments per scan (default 1; render the rest on demand)', 'number'], ['spam_fraction', 'Flag a moment as chat spam when this share of messages repeat one thing', 'number'], ['min_words_per_min', 'Flag a clip as low speech below this many words per minute', 'number'], ['min_gap', 'Minimum gap between moments (s)', 'number'],
    ['skip_start', 'Ignore the first N seconds of a stream', 'number'], ['chat_spike_factor', 'Chat spike factor (× usual rate = score 1)', 'number'],
    ['audio_spike_db', 'Audio spike (dB above usual = score 1)', 'number'], ['whisper_model', 'Whisper model (tiny / base / small / medium)', 'text'],
    ['facecam_fraction', 'Facecam height as a fraction of the frame', 'number'], ['cache_cap_mb', 'Cache cap (MB)', 'number'], ['watch_interval_min', 'Check sources every N minutes', 'number'],
  ];
  el.innerHTML = `<h1>Settings</h1><p class="sub">Tuning for moment detection and rendering. Defaults match the prototype that worked on real footage.</p>
    <div class="panel"><div class="form-grid">${F.map(([k, l, t]) => `<div class="field"><label>${esc(l)}</label><input type="${t}" step="any" data-k="${k}" value="${esc(s[k] ?? '')}"></div>`).join('')}</div>
      <div class="field"><label>Hype tokens (comma separated; chat messages containing these weigh more)</label><input type="text" data-k="hype_tokens" value="${esc((s.hype_tokens || []).join(', '))}"></div>
      <label class="check"><input type="checkbox" data-k="gta_only" ${s.gta_only ? 'checked' : ''}> Only queue videos whose title looks like GTA VI</label>
      <label class="check"><input type="checkbox" data-k="keep_segments" ${s.keep_segments ? 'checked' : ''}> Keep downloaded segments after rendering (uses disk)</label>
      <label class="check"><input type="checkbox" data-k="censor_captions" ${s.censor_captions ? 'checked' : ''}> Censor profanity in burned-in captions (off: captions transcribe exactly what was said)</label>
      <label class="check"><input type="checkbox" data-k="dry_run" ${s.dry_run ? 'checked' : ''}> Dry run: publishing adapters simulate success and never contact a platform</label>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="s-save">Save</button><span class="grow"></span><button class="btn" id="s-prune">Clear cache now</button></div></div>
    <div class="panel" style="margin-top:14px"><b>Disk</b><div class="small muted" id="disk-detail"></div></div>`;
  const d = await get('/api/disk');
  $('#disk-detail').innerHTML = `Data folder: <code>${esc(d.data_dir)}</code><br>Cache ${fmtBytes(d.cache_bytes)} · clips ${fmtBytes(d.clips_bytes)} · tools & models ${fmtBytes(d.tools_bytes)} · free ${fmtBytes(d.free_bytes)} of ${fmtBytes(d.total_bytes)}`;
  $('#s-save').onclick = async () => {
    const body = {};
    $$('[data-k]').forEach((i) => { const k = i.dataset.k; body[k] = i.type === 'checkbox' ? i.checked : (k === 'hype_tokens' ? i.value.split(',').map((x) => x.trim()).filter(Boolean) : (i.type === 'number' ? Number(i.value) : i.value)); });
    try { await put('/api/settings', body); toast('Saved'); refreshState(); } catch (e) { toastErr(e); }
  };
  $('#s-prune').onclick = () => post('/api/disk/prune').then((r) => { toast(`Freed ${fmtBytes(r.freed)}`); render(); });
};

function noCampaign() { return `<h1>No campaign yet</h1><p class="sub">Clips only earn inside a campaign you have joined, so everything starts with one.</p><button class="btn primary" onclick="openCampaignEditor()">Create a campaign</button>`; }
window.openCampaignEditor = openCampaignEditor; window.closeModal = closeModal;

boot();
