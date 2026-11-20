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

// Plain-language status words. Machine states stay in the API; people see these.
const LABELS = {
  clip: { rendering: 'Making clip…', pending: 'Ready to review', approved: 'Approved', rejected: 'Rejected', posted: 'Posted', error: 'Failed' },
  video: { new: 'New — pick a section', queued: 'Waiting to search', scanning: 'Finding moments…', scanned: 'Moments found', rendering: 'Making clip…', done: 'Done', error: 'Failed', skipped: 'Outside campaign window' },
  post: { publishing: 'Posting…', scheduled: 'Scheduled', pending: 'Processing on the platform', live: 'Posted', manual: 'Needs your upload', private: 'Posted as private', draft: 'Draft in your TikTok inbox', failed: 'Failed', cancelled: 'Cancelled' },
  job: { queued: 'Waiting', running: 'Working…', done: 'Done', error: 'Failed', cancelled: 'Cancelled' },
  account: { ready: 'Ready', connected: 'Connected', connecting: 'Connecting…', error: 'Failed', disconnected: 'Not connected' },
};
// Rows carry `error_text` (one plain sentence from the server) next to the raw `error`; prefer it.
const reasonOf = (e) => { const line = String(e || '').trim().split('\n')[0]; return line || 'no reason was recorded — retry'; };
const errText = (row) => (row && (row.error_text || reasonOf(row.error))) || reasonOf('');
function label(kind, status, error) {
  if (['error', 'failed'].includes(status)) return 'Failed: ' + (error && typeof error === 'object' ? errText(error) : reasonOf(error));
  return (LABELS[kind] || {})[status] || status;
}
const badge = (kind, status, error, extra = '') => { const raw = error && typeof error === 'object' ? error.error : error; return `<span class="badge ${status} ${extra}" ${raw ? `title="${esc(String(raw).slice(0, 500))}"` : ''}>${esc(label(kind, status, error))}</span>`; };
// A moment's strength in words; the numbers go in the tooltip.
function momentStrength(m) {
  const txt = (m.reasons || []).join(' ');
  const num = (re) => Number((txt.match(re) || [])[1]) || 0;
  const spike = num(/Chat ([\d.]+)×/), hype = num(/(\d+)% hype/), heat = num(/most replayed' ([\d.]+)×/), db = num(/Audio \+(\d+) dB/), cues = num(/(\d+) cue words/);
  if (spike >= 8 || hype >= 40 || heat >= 4) return 'Chat exploded';
  if (spike >= 3 || heat >= 2 || db >= 8 || cues >= 6) return 'Big reaction';
  if (spike >= 1.5 || hype >= 20 || heat >= 1.5 || db >= 4 || cues >= 3) return 'Some reaction';
  return 'Quiet';
}
const FLAG_LABEL = { low_speech: 'Skipped: streamer not talking', chat_spam: 'Skipped: chat spam' };
const flagLabel = (f) => FLAG_LABEL[f] || 'Skipped: ' + f;
const emptyState = (text, btn, href) => `<div class="empty">${esc(text)} ${btn ? `<a class="btn sm primary" href="${href}" style="margin-left:8px">${esc(btn)} →</a>` : ''}</div>`;

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
const S = { campaigns: [], campaignId: Number(localStorage.getItem('campaignId')) || null, state: null, today: null, view: 'home', timer: null, tick: null, openSources: {} };
// Old links keep working: Pipeline lives inside Sources; Setup, Accounts and Stats are Settings tabs.
const ALIASES = { videos: 'sources', setup: 'settings/setup', accounts: 'settings/accounts', stats: 'settings/stats' };
const campaign = () => S.campaigns.find((c) => c.id === S.campaignId) || S.campaigns[0] || null;

async function loadCampaigns() {
  S.campaigns = await get('/api/campaigns');
  if (!S.campaigns.find((c) => c.id === S.campaignId)) S.campaignId = S.campaigns[0]?.id || null;
  const sel = $('#campaign-select');
  const ordered = [...S.campaigns.filter((c) => !c.workspace), ...S.campaigns.filter((c) => c.workspace)];
  sel.innerHTML = ordered.map((c) => `<option value="${c.id}" ${c.id === S.campaignId ? 'selected' : ''}>${c.workspace ? '🎬 ' : ''}${esc(c.name)}${c.active || c.workspace ? '' : ' (paused)'}</option>`).join('') + `<option value="new">+ New campaign…</option>`;
}
$('#campaign-select').addEventListener('change', (e) => {
  if (e.target.value === 'new') { e.target.value = S.campaignId || ''; openCampaignEditor(); return; }
  S.campaignId = Number(e.target.value); localStorage.setItem('campaignId', S.campaignId); render();
});

async function refreshState() {
  try {
    S.state = await get('/api/state');
    const st = S.state;
    if (st.watch_interval_min) S.watchMin = st.watch_interval_min;
    $('#brand-sub').textContent = 'v' + st.version;
    const c = campaign();
    try { S.today = await get(`/api/today${c ? '?campaign_id=' + c.id : ''}`); } catch (e) { S.today = null; }
    const t = S.today;
    $('#pill-videos').textContent = st.videos_in_progress || '';
    $('#pill-review').textContent = (t ? t.review.count : st.clips_to_review) || '';
    $('#pill-publish').textContent = (t ? t.publish.count : st.clips_approved) || '';
    $('#pill-subs').textContent = (t ? t.submissions.count : st.submissions_due) || '';
    $('#pill-attention').textContent = (t && t.attention.length) || '';
    $('#dry-run-flag').classList.toggle('hidden', !st.dry_run);
    $('#jobs-mini').innerHTML = jobsSummary(st.jobs_running, st.jobs_queued);
    $$('#jobs-mini [data-cancel-job]').forEach((b) => b.onclick = (e) => { e.preventDefault(); post(`/api/jobs/${b.dataset.cancelJob}/cancel`).then(refreshState).catch(toastErr); });
    const d = st.disk;
    $('#disk-mini').innerHTML = d.free_bytes < 2 * 1024 ** 3 ? `<span class="disk-warn">Disk low: ${fmtBytes(d.free_bytes)} free</span>` : '';
  } catch (e) { $('#jobs-mini').textContent = 'Server unreachable'; }
}

// One line like "Making 2 clips · 1 scan running"; the details are in a popover.
const JOB_WORDS = { render_clip: ['Making', 'clip', 'clips'], finalize_clip: ['Exporting', 'clip', 'clips'], render_intro: ['Voicing', 'intro', 'intros'], scan_video: ['', 'scan running', 'scans running'],
  check_sources: ['Checking', 'channel', 'channels'], publish_post: ['Posting', 'clip', 'clips'], check_post: ['Checking', 'post', 'posts'], refresh_stats: ['Refreshing', 'stats', 'stats'],
  grab_frame: ['Grabbing', 'frame', 'frames'], install_tdcli: ['Installing', 'tool', 'tools'], download_whisper: ['Downloading', 'model', 'models'], download_piper_voice: ['Downloading', 'voice', 'voices'] };
function jobsSummary(running, queued) {
  const counts = {}; running.forEach((j) => { counts[j.type] = (counts[j.type] || 0) + 1; });
  const parts = Object.entries(counts).map(([t, n]) => { const w = JOB_WORDS[t] || ['Running', t.replace(/_/g, ' '), t.replace(/_/g, ' ')]; return `${w[0]} ${n} ${n === 1 ? w[1] : w[2]}`.trim(); });
  const line = parts.length ? parts.join(' · ') : 'Idle';
  const tail = queued ? ` · ${queued} waiting` : '';
  const pop = running.length ? `<div class="pop">${running.map((j) => `<div><b>${esc(label('job', j.status))}</b> ${esc(j.type.replace(/_/g, ' '))} · ${Math.round(j.progress * 100)}%<br><span class="small muted">${esc(j.message || '')}</span><br><a href="#" class="small" data-cancel-job="${j.id}">Cancel</a></div>`).join('')}</div>` : '';
  return `<details><summary title="click for details">${running.length ? '⟳ ' : ''}${esc(line + tail)}</summary>${pop}</details>`;
}

const VIEWS = {};
async function render() {
  let hash = location.hash.replace(/^#\/?/, '') || 'home';
  const first = hash.split('/')[0];
  if (ALIASES[first]) { hash = ALIASES[first] + hash.slice(first.length); }
  const [view, ...rest] = hash.split('/');
  const arg = rest.join('/');
  S.view = view;
  $$('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === view));
  const cw = campaign(); $('#nav a[data-view="submissions"]').classList.toggle('hidden', !!(cw && cw.workspace));
  const fn = VIEWS[view] || VIEWS.home;
  try { await fn($('#main'), arg); } catch (e) { $('#main').innerHTML = `<div class="callout pink">${esc(e.message)}</div>`; }
}
window.addEventListener('hashchange', render);

async function boot() {
  await refreshState();
  await loadCampaigns();
  if (!S.state.setup_done && !location.hash.includes('setup')) location.hash = '#/settings/setup';
  await render();
  setInterval(async () => {
    await refreshState();
    if (S.state && (S.state.jobs_running.length || S.state.jobs_queued) && ['sources', 'review', 'publish', 'home', 'settings'].includes(S.view) && !$('#modal-root').innerHTML) render();
  }, 4000);
}

// ---------------------------------------------------------------- Today
VIEWS.home = async (el) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  const t = await get(`/api/today?campaign_id=${c.id}`);
  S.today = t;
  const soon = t.submissions.soonest ? (new Date(t.submissions.soonest) - Date.now()) / 1000 : null;
  const step = (n, title, count, body, btn, href, extra = '') => `
    <div class="step ${count ? '' : 'done'} ${extra}">
      <div class="step-n">${n}</div>
      <div class="step-body"><div class="step-title">${esc(title)} <b class="count">${count}</b></div><div class="step-sub">${body}</div></div>
      <a class="btn ${count ? 'primary' : ''}" href="${href}">${esc(btn)}</a>
    </div>`;
  const best = t.review.best;
  el.innerHTML = `
    <h1>Today</h1><p class="sub">${esc(c.name)} · work through the list top to bottom.</p>
    <div class="steps">
      ${step(1, 'Clips waiting for you', t.review.count,
        best ? `<div class="row"><div class="best-thumb ${best.thumb_url ? '' : 'none'}" style="background-image:url('${best.thumb_url || ''}')">${best.thumb_url ? '' : fmtTime(best.source_start)}</div><div>Best one: <b>${esc(best.hook || 'untitled')}</b><br><span class="muted small">from ${fmtTime(best.source_start)}</span></div></div>`
             : 'Nothing to review. Scan a video or pick a moment to render under Sources.',
        t.review.count ? 'Review' : 'Find moments', t.review.count ? '#/review' : '#/sources')}
      ${step(2, 'Approved, not posted', t.publish.count, t.publish.count ? 'Post now or schedule each approved clip.' : 'Everything approved has been posted.', 'Publish', '#/publish')}
      ${t.workspace ? '' : step(3, 'Posted, not submitted', t.submissions.count,
        t.submissions.count ? (soon !== null && soon <= 0 ? '<span class="countdown late">Missed</span> a deadline has passed' : `Soonest deadline in <span class="countdown ${soon !== null && soon < 600 ? 'late' : ''}" data-dl="${esc(t.submissions.soonest)}"></span>`) : 'Every live post has been submitted.',
        'Submissions', '#/submissions')}
      ${step(t.workspace ? 3 : 4, 'Streams being watched', t.sources.length,
        t.sources.length ? t.sources.map((s) => `<span class="chip">${s.live ? '<b class="live-dot">LIVE</b> ' : ''}${esc(s.name)}</span>`).join(' ') : 'No channel is watched yet. Add one under Sources.',
        'Sources', '#/sources')}
      ${t.new_videos.length ? `<div class="step">
        <div class="step-n">▶</div>
        <div class="step-body"><div class="step-title">New videos found <b class="count">${t.new_videos.length}</b></div>
          <div class="step-sub">${t.new_videos.map((n) => `<div class="attn"><span>New video found: <b>${esc(n.title)}</b>${n.live ? ' <b class="live-dot">LIVE</b>' : ''}<span class="muted"> · ${esc(n.source || n.platform)}${n.duration ? ' · ' + fmtTime(n.duration) : ''}</span></span><button class="btn sm primary" data-pick="${n.id}">Pick a section</button></div>`).join('')}</div></div>
      </div>` : ''}
      <div class="step ${t.attention.length ? 'warn' : 'done'}">
        <div class="step-n">!</div>
        <div class="step-body"><div class="step-title">Needs attention <b class="count">${t.attention.length}</b></div>
          <div class="step-sub">${t.attention.length ? t.attention.map((a) => `<div class="attn"><span><b>${esc(a.title)}</b> — ${esc(a.reason)}</span><button class="btn sm" data-retry="${esc(a.retry)}" data-kind="${a.kind}">Retry</button></div>`).join('') : 'Nothing failed or stuck.'}</div></div>
      </div>
    </div>`;
  $$('[data-retry]', el).forEach((b) => b.onclick = async () => { b.disabled = true; try { await post(b.dataset.retry, b.dataset.kind === 'video' ? { override: true } : {}); toast('Retrying'); render(); } catch (e) { toastErr(e); b.disabled = false; } });
  $$('[data-pick]', el).forEach((b) => b.onclick = () => openSectionPicker(Number(b.dataset.pick)));
  const cd = $('.countdown[data-dl]', el);
  if (cd) { const tick = () => { const left = (new Date(cd.dataset.dl) - Date.now()) / 1000; if (left <= 0) { cd.textContent = 'Missed'; cd.classList.add('late'); return; } cd.textContent = fmtTime(left); cd.classList.toggle('late', left < 600); }; tick(); clearInterval(S.tick); S.tick = setInterval(tick, 1000); }
};

// ---------------------------------------------------------------- Campaign editor
function openCampaignEditor(c = null) {
  const v = (k, d = '') => esc(c ? (c[k] ?? d) : d);
  const plats = c ? c.platforms : ['youtube', 'tiktok', 'instagram'];
  const m = modal(`
    <div class="modal-head"><h2 style="margin:0">${c ? 'Edit campaign' : 'New campaign'}</h2><button class="btn ghost" onclick="closeModal()">✕</button></div>
    ${c ? '' : `<div class="panel" style="margin-bottom:14px"><div class="field"><label>Paste the campaign URL or its rules text; the fields below get pre-filled (check every one)</label>
      <div class="row"><input type="text" id="c-url" placeholder="https://reachclipping.com/LACYGTA"><button class="btn" id="c-parse">Read page</button></div>
      <textarea id="c-rules" placeholder="…or paste the rules here"></textarea><button class="btn sm" id="c-parse-text" style="align-self:flex-start">Pre-fill from text</button>
      <div id="c-note" class="callout hidden"></div></div></div>`}
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
    <div class="option-box"><label class="check"><input type="checkbox" id="f-bans" ${c?.bans_ai ? 'checked' : ''}> The campaign's rules ban AI-generated content</label>
      <label class="check"><input type="checkbox" id="f-narr" ${c?.ai_narration && !c?.bans_ai ? 'checked' : ''} ${c?.bans_ai ? 'disabled' : ''}> AI narration allowed (optional 3–6 s voiced hook intro before a clip)</label>
      <div class="small muted" id="f-narr-why">${c?.bans_ai ? 'Locked off: this campaign bans AI-generated content, so a synthetic voice intro would get the clip rejected.' : 'Off by default. Narrated clips carry the altered/synthetic content disclosure on YouTube and TikTok.'}</div></div>
    <div class="row between">
      <div>${c ? `<label class="check"><input type="checkbox" id="f-active" ${c.active ? 'checked' : ''}> Active (sources are watched)</label>` : ''}</div>
      <div class="row">${c ? `<button class="btn danger" id="f-del">Delete</button>` : ''}<button class="btn primary" id="f-save">Save</button></div>
    </div>`);
  const fill = (f) => {
    const set = (id, val) => { if (val !== undefined && val !== null && val !== '') $(id, m).value = Array.isArray(val) ? val.join(', ') : val; };
    set('#f-creator', f.creator); set('#f-tags', f.required_hashtags); set('#f-submit', f.submit_minutes); set('#f-rate', f.rate_per_1k);
    set('#f-cap', f.per_clip_cap); set('#f-pool', f.pool_budget); set('#f-mv', f.min_views); set('#f-start', f.source_start); set('#f-url', f.url);
    set('#f-min', f.min_len); set('#f-rules', f.rules_text); set('#f-name', f.name); set('#f-sub', f.submission_url);
    if (f.required_hashtags && !f.required_hashtags.length) $('#f-tags', m).value = '';
    const note = $('#c-note', m); if (note) { note.classList.toggle('hidden', !f.note); note.textContent = f.note || ''; }
    if (f.rules_locked) { $('#f-tags', m).value = ''; $('#f-start', m).value = ''; $('#f-tags', m).placeholder = 'paste the rules after joining'; $('#f-start', m).placeholder = 'paste the rules after joining'; }
    if (f.pay_model) $('#f-pay', m).value = f.pay_model;
    if (f.watermark_allowed !== undefined) $('#f-wm', m).value = String(f.watermark_allowed);
    if (f.bans_ai) { $('#f-bans', m).checked = true; $('#f-bans', m).onchange({ target: $('#f-bans', m) }); }
    if (f.creator && !$('#f-name', m).value) $('#f-name', m).value = f.creator + ' campaign';
    if (f.required_names) $('#f-names', m).value = f.required_names.join(', ');
    if (f.creator && !$('#f-names', m).value) $('#f-names', m).value = f.creator;
    if (f.platforms) $$('#f-plats .chip', m).forEach((ch) => ch.classList.toggle('on', f.platforms.includes(ch.dataset.p)));
    toast(f.rules_locked ? 'Page read, but its rules are only visible after joining.' : 'Pre-filled. Check every field against the campaign page.', f.rules_locked ? 'warn' : '');
  };
  if (!c) {
    $('#c-parse', m).onclick = async () => { try { const r = await post('/api/campaigns/parse', { url: $('#c-url', m).value }); if (r.error) toast(r.error, 'warn'); fill(r.fields); } catch (e) { toastErr(e); } };
    $('#c-parse-text', m).onclick = async () => { try { const r = await post('/api/campaigns/parse', { text: $('#c-rules', m).value, url: $('#c-url', m).value }); fill(r.fields); } catch (e) { toastErr(e); } };
  }
  $$('#f-plats .chip', m).forEach((ch) => ch.onclick = () => ch.classList.toggle('on'));
  $('#f-bans', m).onchange = (e) => { const n = $('#f-narr', m); n.disabled = e.target.checked; if (e.target.checked) n.checked = false; $('#f-narr-why', m).textContent = e.target.checked ? 'Locked off: this campaign bans AI-generated content.' : 'Off by default. Narrated clips carry the altered/synthetic content disclosure on YouTube and TikTok.'; };
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
      bans_ai: $('#f-bans', m).checked ? 1 : 0, ai_narration: $('#f-narr', m).checked && !$('#f-bans', m).checked ? 1 : 0,
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

// ---------------------------------------------------------------- Sources (channels, single videos, and each video's moments)
VIEWS.sources = async (el, arg) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  const pickId = arg && arg.startsWith('pick/') ? Number(arg.slice(5)) : null;   // #/sources/pick/<video id> opens the section picker
  if (arg && !pickId) S.openSources[Number(arg)] = true;
  const [srcs, sugg, vids, skipped] = await Promise.all([get(`/api/campaigns/${c.id}/sources`), get('/api/suggestions'), get(`/api/videos?campaign_id=${c.id}`), get(`/api/videos/skipped?campaign_id=${c.id}`)]);
  const bySource = (id) => vids.filter((v) => v.source_id === id);
  const loose = vids.filter((v) => !v.source_id || !srcs.some((s) => s.id === v.source_id));
  el.innerHTML = `
    <h1>Sources · ${esc(c.name)}</h1><p class="sub">Channels are watched for new GTA VI videos; a single video URL is scanned once. Open a source to see its videos and the moments found in each.</p>
    ${c.workspace ? `<div class="callout teal"><b>Streamer clips (no campaign).</b> Only creators who publicly allow clipping (a !clipping program, clip-friendly terms). Clips here follow no campaign rules and have no submission tracker; captions credit the streamer by name and handle. A creator who has an active campaign in this app must be clipped inside that campaign.</div>` : ''}
    <div class="panel">
      <div class="field"><label>Add a YouTube channel (youtube.com/@name), Twitch channel (twitch.tv/name), or a single video URL (a stream still in progress works too)</label>
        <div class="row"><input type="text" id="src-url" placeholder="https://www.twitch.tv/lacy  ·  https://www.youtube.com/@IShowSpeed  ·  https://www.twitch.tv/videos/…" style="flex:1;min-width:260px"><button class="btn primary" id="src-add" ${c.workspace ? 'disabled' : ''}>Add</button></div></div>
      ${c.workspace ? `<label class="check"><input type="checkbox" id="src-consent"> This creator publicly allows clipping (e.g. a !clipping program or clip-friendly terms)</label>
        <div class="row" style="margin-top:6px"><input type="text" id="src-terms" placeholder="Link to their clipping terms / program page (optional)" style="flex:1"></div>` : ''}
      <div class="row small muted" style="margin-top:8px">Suggestions:
        ${sugg.map((s) => `${s.youtube ? `<span class="chip" data-url="https://www.youtube.com/${s.youtube}">▶ ${esc(s.name)} (YouTube)</span>` : ''}${s.twitch ? `<span class="chip" data-url="https://www.twitch.tv/${s.twitch}">⌁ ${esc(s.name)} (Twitch)</span>` : ''}`).join('')}</div>
      <div class="small muted" style="margin-top:8px">Channels are checked every ${esc(String(S.watchMin || 10))} minutes, live streams included; new videos appear on Today. A video is searched only after you pick a section of it. Videos published before the campaign's footage start (${esc((c.source_start || 'not set').slice(0, 10))}) are skipped unless you override.</div>
    </div>
    <div id="src-list" style="margin-top:14px">${srcs.length || loose.length ? '' : '<div class="empty">No sources yet. Paste a channel or video URL above, or pick a suggestion.</div>'}</div>
    ${skipped.length ? `<div style="margin-top:10px"><button class="btn sm ghost" id="v-toggle-skipped">${S.showSkipped ? 'Hide' : 'Show'} skipped (${skipped.length})</button>
      <div class="panel ${S.showSkipped ? '' : 'hidden'}" id="v-skipped" style="margin-top:8px"><div class="small muted" style="margin-bottom:6px">Published outside the campaign's footage window, so not scanned. "Scan anyway" overrides for that video.</div></div></div>` : ''}`;
  const tog = $('#v-toggle-skipped'); if (tog) tog.onclick = () => { S.showSkipped = !S.showSkipped; render(); };
  const consent = () => !c.workspace || ($('#src-consent') && $('#src-consent').checked);
  if (c.workspace) $('#src-consent').onchange = () => { $('#src-add').disabled = !consent(); };
  const add = async (url) => {
    if (!url) return;
    if (!consent()) return toast('Tick the clipping-allowed box first', 'warn');
    const btn = $('#src-add'); btn.disabled = true; btn.textContent = 'Resolving…';
    try {
      const r = await post('/api/sources', { campaign_id: c.id, url, clip_terms_ok: consent(), terms_url: c.workspace ? $('#src-terms').value.trim() : undefined });
      toast(r.video ? 'Video added; pick the section to search' : `Added ${r.source.name}`); $('#src-url').value = ''; await render();
      if (r.video) openSectionPicker(r.video.id);
    } catch (e) { if (e.status === 409 && e.data && e.data.campaign_id) { toast(e.data.message, 'warn'); } else toastErr(e); }
    finally { btn.disabled = !consent(); btn.textContent = 'Add'; }
  };
  $('#src-add').onclick = () => add($('#src-url').value.trim());
  $('#src-url').onkeydown = (e) => { if (e.key === 'Enter') add(e.target.value.trim()); };
  $$('.chip[data-url]').forEach((ch) => ch.onclick = () => { $('#src-url').value = ch.dataset.url; add(ch.dataset.url); });
  const list = $('#src-list');
  for (const src of srcs) {
    const d = document.createElement('div'); d.className = 'panel source-card';
    const svids = bySource(src.id);
    const busy = svids.filter((v) => ['queued', 'scanning', 'rendering'].includes(v.status)).length;
    const pending = svids.reduce((n, v) => n + ((v.clip_counts || {}).pending || 0), 0);
    const open = S.openSources[src.id] ?? (srcs.length <= 2);
    d.innerHTML = `
      <div class="row between">
        <div><b>${esc(src.name || src.url)}</b> <span class="badge">${src.kind.replace('_', ' ')}</span> ${svids.some((v) => v.live) ? '<span class="badge live-badge">LIVE</span>' : ''}</div>
        <label class="check small"><input type="checkbox" ${src.watch ? 'checked' : ''} data-watch> watch for new videos</label></div>
      <div class="small muted"><a href="${esc(src.url)}" target="_blank">${esc(src.url)}</a> · ${svids.length} videos${busy ? ` · ${busy} in progress` : ''}${pending ? ` · <a href="#/review">${pending} to review</a>` : ''} · last checked ${src.last_checked ? fmtDate(src.last_checked) : 'never'}${src.clip_terms_ok ? ' · <span class="ok-text">clipping allowed ✓</span>' : ''}${src.terms_url ? ` · <a href="${esc(src.terms_url)}" target="_blank">clipping terms ↗</a>` : ''}</div>
      ${c.workspace ? `<div class="row" style="margin-top:8px"><input type="text" data-terms value="${esc(src.terms_url || '')}" placeholder="Clipping terms URL" style="width:260px"><input type="text" data-template value="${esc(src.caption_template || '')}" placeholder="Caption template: {hook} — clip from {creator}'s stream ({handle})" style="flex:1"><button class="btn sm" data-save-ws>Save</button></div>` : ''}
      <div class="row" style="margin-top:10px">
        <button class="btn sm" data-toggle>${open ? 'Hide' : 'Show'} videos & moments (${svids.length})</button>
        <button class="btn sm" data-check>Check for new videos</button>
        <button class="btn sm" data-recent>Browse recent videos</button>
        <button class="btn sm ${src.facecam ? '' : 'yellow'}" data-facecam>${src.facecam ? (src.facecam.none ? 'No facecam (edit)' : 'Facecam set ✓ (edit)') : 'Set facecam crop'}</button>
        <select class="sm" data-layout style="width:auto"><option value="full" ${src.layout === 'full' ? 'selected' : ''}>Layout: gameplay full width</option><option value="stacked" ${src.layout === 'stacked' ? 'selected' : ''}>Layout: facecam top, gameplay below</option></select>
        <span class="grow"></span><button class="btn sm danger" data-del>Remove</button>
      </div>
      <div data-extra></div>
      <div data-videos class="${open ? '' : 'hidden'}" style="margin-top:8px">${svids.length ? '' : '<div class="small muted" style="padding:8px 0">No videos yet. "Check for new videos" looks at the channel now; "Browse recent videos" lets you pick one.</div>'}</div>`;
    $('[data-watch]', d).onchange = (e) => put(`/api/sources/${src.id}`, { watch: e.target.checked ? 1 : 0 }).catch(toastErr);
    $('[data-layout]', d).onchange = (e) => put(`/api/sources/${src.id}`, { layout: e.target.value }).then(() => toast('Layout saved; re-render clips to apply')).catch(toastErr);
    $('[data-check]', d).onclick = () => post(`/api/sources/${src.id}/check`).then(() => toast('Checking in the background')).catch(toastErr);
    $('[data-del]', d).onclick = async () => { if (await confirmDlg(`Remove ${src.name}? Its videos and clips stay.`, 'Remove')) { await del(`/api/sources/${src.id}`); render(); } };
    $('[data-facecam]', d).onclick = () => openFacecamPicker(src);
    $('[data-toggle]', d).onclick = () => { S.openSources[src.id] = !open; render(); };
    const sv = $('[data-save-ws]', d); if (sv) sv.onclick = () => put(`/api/sources/${src.id}`, { terms_url: $('[data-terms]', d).value.trim(), caption_template: $('[data-template]', d).value.trim() }).then(() => toast('Saved; applies to clips rendered from now on')).catch(toastErr);
    $('[data-recent]', d).onclick = async () => {
      const x = $('[data-extra]', d); x.innerHTML = '<div class="muted small">Loading…</div>';
      try {
        const rv = await get(`/api/sources/${src.id}/recent`);
        x.innerHTML = `<table style="margin-top:10px">${rv.map((v) => `<tr><td><a href="${esc(v.url)}" target="_blank">${esc(v.title)}</a><br><span class="small muted">${v.published_at ? fmtDate(v.published_at) : ''} · ${v.duration ? fmtTime(v.duration) : ''} ${v.is_stream ? '· stream' : ''} ${v.gta ? '<span class="badge teal">GTA</span>' : ''}</span></td><td style="width:90px">${v.added ? '<span class="badge ok">added</span>' : `<button class="btn sm" data-add="${esc(v.url)}">Scan</button>`}</td></tr>`).join('')}</table>`;
        $$('[data-add]', x).forEach((b) => b.onclick = () => post('/api/videos', { campaign_id: c.id, url: b.dataset.add, source_id: src.id }).then(() => { toast('Queued'); S.openSources[src.id] = true; render(); }).catch(toastErr));
      } catch (e) { x.innerHTML = `<div class="bad-text small">${esc(e.message)}</div>`; }
    };
    const vbox = $('[data-videos]', d);
    if (open) for (const v of svids) renderVideo(v, vbox, c, svids.length);
    list.appendChild(d);
  }
  if (loose.length) {
    const d = document.createElement('div'); d.className = 'panel source-card';
    d.innerHTML = `<div class="row between"><b>Single videos</b><span class="small muted">added by URL, not from a watched channel</span></div><div data-videos></div>`;
    for (const v of loose) renderVideo(v, $('[data-videos]', d), c, loose.length);
    list.appendChild(d);
  }
  if (S.showSkipped && $('#v-skipped')) for (const v of skipped) renderVideo(v, $('#v-skipped'), c, 99);
  if (pickId && !$('#modal-root').innerHTML) openSectionPicker(pickId);
};

function openFacecamPicker(s) {
  const m = modal(`<div class="modal-head"><h2 style="margin:0">Facecam crop · ${esc(s.name)}</h2><button class="btn ghost" onclick="closeModal()">✕</button></div><div id="fc-root"></div>`);
  facecamPicker(s, $('#fc-root', m), () => { closeModal(); render(); });
}
// Renders the facecam picker into `root`; onDone(saved) is called after Save or "No facecam".
function facecamPicker(s, root, onDone) {
  root.innerHTML = `
    <p class="muted small">Drag a box around the streamer's webcam. It is cropped and placed on top of the gameplay in every clip from this source. If no frame is shown yet, grab one (downloads a 3-second sample).</p>
    <div class="row" style="margin-bottom:10px"><button class="btn sm" data-fc-grab>Grab a frame</button><span class="small muted" data-fc-status></span></div>
    <div class="picker"><img data-fc-img src="/media/frame/${s.id}?t=${Date.now()}" alt=""><canvas data-fc-canvas></canvas></div>
    <div class="row" style="margin-top:12px"><span class="mono" data-fc-box>${s.facecam && !s.facecam.none ? `x ${s.facecam.x.toFixed(3)} y ${s.facecam.y.toFixed(3)} w ${s.facecam.w.toFixed(3)} h ${s.facecam.h.toFixed(3)}` : 'no box yet'}</span><span class="grow"></span>
      <button class="btn" data-fc-clear>No facecam (gameplay only)</button><button class="btn primary" data-fc-save ${s.facecam && !s.facecam.none ? '' : 'disabled'}>Save crop</button></div>`;
  let box = s.facecam && !s.facecam.none ? { ...s.facecam } : null;
  const img = $('[data-fc-img]', root), cv = $('[data-fc-canvas]', root), status = $('[data-fc-status]', root);
  img.onerror = () => { status.textContent = 'No frame yet. Click "Grab a frame".'; img.style.minHeight = '200px'; };
  const draw = () => {
    cv.width = img.clientWidth || 640; cv.height = img.clientHeight || 360;
    const ctx = cv.getContext('2d'); ctx.clearRect(0, 0, cv.width, cv.height);
    if (!box) return;
    ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.clearRect(box.x * cv.width, box.y * cv.height, box.w * cv.width, box.h * cv.height);
    ctx.strokeStyle = '#1fd1d6'; ctx.lineWidth = 2; ctx.strokeRect(box.x * cv.width, box.y * cv.height, box.w * cv.width, box.h * cv.height);
    $('[data-fc-box]', root).textContent = `x ${box.x.toFixed(3)} y ${box.y.toFixed(3)} w ${box.w.toFixed(3)} h ${box.h.toFixed(3)}`;
    $('[data-fc-save]', root).disabled = false;
  };
  img.onload = draw; if (img.complete) setTimeout(draw, 50);
  let start = null;
  const pos = (e) => { const r = cv.getBoundingClientRect(); const t = e.touches ? e.touches[0] : e; return { x: Math.min(1, Math.max(0, (t.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (t.clientY - r.top) / r.height)) }; };
  const down = (e) => { start = pos(e); box = { x: start.x, y: start.y, w: 0, h: 0 }; draw(); e.preventDefault(); };
  const move = (e) => { if (!start) return; const p = pos(e); box = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) }; draw(); e.preventDefault(); };
  const up = () => { start = null; };
  cv.onmousedown = down; cv.onmousemove = move; cv.onmouseup = up; cv.ontouchstart = down; cv.ontouchmove = move; cv.ontouchend = up;
  $('[data-fc-grab]', root).onclick = async () => {
    status.textContent = 'Downloading a sample… (10-30 s)';
    await post(`/api/sources/${s.id}/grab_frame`);
    let tries = 0; const iv = setInterval(async () => {
      tries++; const jobs = await get('/api/jobs?limit=5'); const j = jobs.find((x) => x.type === 'grab_frame' && x.payload.source_id === s.id);
      if (j && j.status === 'done') { clearInterval(iv); img.src = `/media/frame/${s.id}?t=${Date.now()}`; status.textContent = 'Frame ready. Drag a box.'; }
      else if (j && j.status === 'error') { clearInterval(iv); status.textContent = 'Failed: ' + errText(j); }
      else if (tries > 60) clearInterval(iv);
    }, 2000);
  };
  $('[data-fc-save]', root).onclick = async () => { if (!box || box.w < 0.03 || box.h < 0.03) return toast('Box too small', 'warn'); await put(`/api/sources/${s.id}`, { facecam: box, layout: 'stacked' }); toast('Facecam saved. Re-render existing clips to apply it.'); onDone(true); };
  $('[data-fc-clear]', root).onclick = async () => { await put(`/api/sources/${s.id}`, { facecam: { none: true }, layout: 'full' }); onDone(false); };
}

// ---------------------------------------------------------------- Pipeline (videos)
const parseHms = (s) => { const p = String(s || '').trim().split(':').map(Number); if (!p.length || p.some(isNaN)) return null; return p.reduce((a, b) => a * 60 + b, 0); };

// ---------------------------------------------------------------- one video row with its ranked moments (used inside Sources)
function renderVideo(v, container, c, siblings) {
  const d = document.createElement('div'); d.className = 'video-row';
  const cc = v.clip_counts || {};
  const ranges = (v.scanned_ranges || []).map(([s, e]) => `${fmtTime(s)}–${fmtTime(e)}`).join(', ');
  const busy = ['scanning', 'queued'].includes(v.status);
  d.innerHTML = `
    <div>
      <div class="row"><b>${esc(v.title || v.url)}</b> ${badge('video', v.status, v)} ${v.live ? '<span class="badge live-badge">LIVE</span>' : (v.is_stream ? '<span class="badge">stream VOD</span>' : '')} ${v.platform === 'twitch' ? '<span class="badge">twitch</span>' : ''}</div>
      <div class="small muted"><a href="${esc(v.url)}" target="_blank">${esc(v.url)}</a> · ${v.channel ? esc(v.channel) + ' · ' : ''}${v.duration ? fmtTime(v.duration) : ''} ${v.published_at ? '· published ' + fmtDate(v.published_at) : ''}</div>
      <div class="small" style="margin-top:6px">${v.status === 'error' ? `<span class="bad-text">${esc(errText(v))}</span>` : esc(v.stage || '')}</div>
      ${busy ? `<div class="progress" style="margin-top:6px;max-width:420px"><i style="width:${Math.round((v.progress || 0) * 100)}%"></i></div>` : ''}
      <div class="small muted" style="margin-top:6px">${ranges ? 'searched ' + ranges + ' · ' : ''}${v.moment_count} moments${v.signals.signals_used ? ' from ' + v.signals.signals_used.join(' + ') : ''}${v.signals.chat_messages ? ` · ${fmtNum(v.signals.chat_messages)} chat messages` : ''}${v.signals.chat_error ? ` · <span class="warn-text">chat: ${esc(v.signals.chat_error.slice(0, 120))}</span>` : ''} · clips: ${Object.entries(cc).map(([k, n]) => `${n} ${label('clip', k).toLowerCase()}`).join(', ') || 'none'}</div>
      <div data-moments></div>
    </div>
    <div class="row" style="align-items:flex-start">
      <button class="btn sm ${v.status === 'new' ? 'primary' : ''}" data-pick ${busy ? 'disabled' : ''}>${v.status === 'skipped' ? 'Pick a section (overrides date window)' : 'Pick a section'}</button>
      ${v.moment_count ? `<button class="btn sm" data-show>Show / hide moments</button>` : ''}
      ${cc.pending ? `<a class="btn sm primary" href="#/review/${v.id}">Review ${cc.pending}</a>` : ''}
      ${v.status === 'error' ? `<button class="btn sm yellow" data-retry-scan>Retry</button>` : ''}
      <button class="btn sm danger" data-del>Delete</button>
    </div>`;
  $('[data-pick]', d).onclick = () => openSectionPicker(v.id);
  const rs = $('[data-retry-scan]', d); if (rs) rs.onclick = () => post(`/api/videos/${v.id}/rescan`, { window: v.scan_window || 'auto' }).then(render).catch(toastErr);
  $('[data-del]', d).onclick = async () => { if (await confirmDlg('Delete this video and all its clips?', 'Delete')) { await del(`/api/videos/${v.id}`); render(); } };
  const momentsBox = $('[data-moments]', d);
  const showBtn = $('[data-show]', d);
  if (showBtn) showBtn.onclick = () => { if (momentsBox.innerHTML) momentsBox.innerHTML = ''; else renderMoments(v, momentsBox); };
  if (v.moment_count && (S.openMoments === v.id || siblings <= 3)) renderMoments(v, momentsBox);
  container.appendChild(d);
}

// Ranked moments of one video: score next to the plain label, a bar for relative strength, "why" on hover.
// Nothing renders until Render is clicked. opts.clipLen / opts.leadIn come from the section picker.
async function renderMoments(v, box, opts = {}) {
  const ms = await get(`/api/videos/${v.id}/moments`);
  if (!ms.length) { box.innerHTML = opts.empty ? `<div class="muted small" style="padding:8px 0">${esc(opts.empty)}</div>` : ''; return; }
  const byScore = (a, b) => b.score - a.score || a.t - b.t;
  const skippedMs = ms.filter((m) => m.flags?.length).sort(byScore), shown = ms.filter((m) => !m.flags?.length).sort(byScore);
  const maxScore = Math.max(0.01, ...ms.map((m) => Number(m.raw_score ?? m.score) || 0));
  S.showSkippedMoments = S.showSkippedMoments || {};
  const openSk = !!S.showSkippedMoments[v.id];
  const renderBody = () => opts.clipLen ? { clip_len: opts.clipLen(), lead_in: opts.leadIn() } : {};
  const chipFor = (m) => m.clip
    ? (m.clip.status === 'rendering'
      ? `<span class="badge rendering">${esc(m.render_job?.message || 'Making clip…')}</span><button class="btn sm danger" data-cancel="${m.clip.id}">Cancel</button>`
      : `${badge('clip', m.clip.status, m.clip)}${['pending', 'approved'].includes(m.clip.status) ? `<a class="btn sm" href="#/review/${v.id}">Review</a>` : ''}${m.clip.status === 'error' ? `<button class="btn sm yellow" data-retry-clip="${m.clip.id}">Retry</button>` : ''}`)
    : `<button class="btn sm primary" data-render="${m.id}">Render</button>`;
  const rowFor = (m) => {
    const sc = Number(m.score) || 0;
    const why = (m.reasons || []).join(' · ') + (m.raw_score != null && m.raw_score !== m.score ? ` · raw score ${m.raw_score}` : '');
    return `<div class="moment ${m.flags?.length ? 'flagged' : ''}" title="${esc(why)}">
        ${m.thumb_url ? `<div class="mthumb" style="background-image:url('${m.thumb_url}')"></div>` : `<div class="mthumb none">${m.ts}</div>`}
        <span class="t">${m.ts}</span>
        <div class="grow"><div class="row" style="gap:8px"><b>${m.flags?.length ? esc(flagLabel(m.flags[0])) : esc(momentStrength(m))}</b><span class="score">${sc.toFixed(1)}</span><span class="sbar" title="${esc(why)}"><i style="width:${Math.round(sc / maxScore * 100)}%"></i></span></div>
          ${m.chat_words.length ? `<div class="reason">chat was saying: ${m.chat_words.map(esc).join(', ')}</div>` : `<div class="reason">${esc((m.reasons || [])[0] || '')}</div>`}</div>
        ${chipFor(m)}
        <a class="small" href="${esc(v.url)}${v.platform === 'youtube' ? '&t=' + Math.round(m.t) : '?t=' + Math.round(m.t) + 's'}" target="_blank">open ↗</a></div>`;
  };
  box.innerHTML = `<div style="margin-top:10px"><div class="row between"><b class="small">Ranked moments <span class="muted">(score, highest first; hover for why)</span></b></div>
      ${shown.map(rowFor).join('')}
      ${skippedMs.length ? `<a class="small" href="#" data-toggle-sk style="display:block;margin-top:6px">${openSk ? 'hide' : 'show'} skipped (${skippedMs.length})</a>${openSk ? skippedMs.map(rowFor).join('') : ''}` : ''}
      <div class="row" style="margin-top:8px"><span class="small muted">Render at a time of your own:</span><input type="text" placeholder="hh:mm:ss" style="width:110px" data-custom-t><button class="btn sm" data-custom>Render</button></div></div>`;
  const reload = () => renderMoments(v, box, opts);
  $$('[data-render]', box).forEach((b) => b.onclick = () => post(`/api/moments/${b.dataset.render}/render`, renderBody()).then(() => { toast('Making the clip'); reload(); }).catch(toastErr));
  $$('[data-retry-clip]', box).forEach((b) => b.onclick = () => post(`/api/clips/${b.dataset.retryClip}/rerender`).then(() => { toast('Retrying'); reload(); }).catch(toastErr));
  $$('[data-cancel]', box).forEach((b) => b.onclick = () => post(`/api/clips/${b.dataset.cancel}/cancel`).then(() => { toast('Cancelled'); setTimeout(reload, 800); }).catch(toastErr));
  const tsk = $('[data-toggle-sk]', box); if (tsk) tsk.onclick = (e) => { e.preventDefault(); S.showSkippedMoments[v.id] = !openSk; reload(); };
  $('[data-custom]', box).onclick = () => { const t = parseHms($('[data-custom-t]', box).value); if (t == null) return toast('Enter hh:mm:ss', 'warn'); post(`/api/videos/${v.id}/render_custom`, { t, ...renderBody() }).then(() => { toast('Making the clip'); reload(); }).catch(toastErr); };
}

// ---------------------------------------------------------------- Pick a section
// One panel per video: a timeline of the whole VOD with a chat-activity heat strip, drag handles or typed
// start/end for the section to search, presets, a clip-length picker and lead-in, then "Find moments".
const CLIP_LENS = [61, 90, 120];
async function openSectionPicker(videoId) {
  let sec;
  const m = modal(`<div class="modal-head"><h2 style="margin:0">Pick a section</h2><button class="btn ghost" onclick="closeModal()">✕</button></div><div class="muted" id="sp-wait">Reading the video…</div>`);
  try { sec = await get(`/api/videos/${videoId}/section?refresh=1`); } catch (e) { $('#sp-wait', m).innerHTML = `<span class="bad-text">${esc(e.message)}</span>`; return; }
  const dur = sec.duration || 0;
  if (!dur) { $('#sp-wait', m).innerHTML = `<span class="bad-text">This video has no length yet (a stream that just started, or an unavailable video). Try again in a few minutes.</span>`; return; }
  let a = sec.last_section && sec.last_section[0] != null ? Number(sec.last_section[0]) : Math.max(0, dur - 7200), b = sec.last_section && sec.last_section[1] != null ? Math.min(dur, Number(sec.last_section[1])) : dur;
  if (b - a < 60) { a = Math.max(0, dur - 7200); b = dur; }
  let clipLen = Number(sec.clip_len) || 90, leadIn = Number(sec.lead_in) ?? 25;
  const custom = !CLIP_LENS.includes(clipLen);
  m.innerHTML = `<div class="modal-head"><div><h2 style="margin:0">Pick a section · ${esc(sec.title || '')}</h2><span class="small muted">${fmtTime(dur)} long${sec.live ? ' · <b class="live-dot">LIVE</b> still growing' : ''} · searching reads only this section's chat and audio, so a 2-hour section takes about half a minute.</span></div><button class="btn ghost" onclick="closeModal()">✕</button></div>
    <div class="panel">
      <div class="row between"><div class="chips" id="sp-presets">${[['last30m', 'Last 30 min'], ['last1h', 'Last 1 h'], ['last2h', 'Last 2 h'], ['whole', 'Whole VOD']].map(([k, l]) => `<span class="chip" data-preset="${k}">${l}</span>`).join('')}</div>
        <span class="small muted" id="sp-heat-status"></span></div>
      <div class="tl" id="tl"><canvas id="tl-heat"></canvas><div id="tl-overlays"></div><div class="tl-sel" id="tl-sel"></div><div class="tl-handle" id="tl-a" title="drag: section start"></div><div class="tl-handle" id="tl-b" title="drag: section end"></div></div>
      <div class="row between small muted"><span>0:00</span><span>chat activity: brighter = busier · grey = not loaded yet · ticks = moments already found</span><span>${fmtTime(dur)}</span></div>
      <div class="row" style="margin-top:10px"><label class="small muted">From</label><input type="text" id="sp-a" style="width:110px"><label class="small muted">to</label><input type="text" id="sp-b" style="width:110px"><span class="small" id="sp-len"></span></div>
    </div>
    <div class="panel" style="margin-top:12px"><div class="row" style="gap:18px;align-items:flex-end">
      <div class="field" style="margin:0"><label>Clip length</label><div class="chips" id="sp-lens">${CLIP_LENS.map((n) => `<span class="chip ${!custom && clipLen === n ? 'on' : ''}" data-len="${n}">${n} s</span>`).join('')}<span class="chip ${custom ? 'on' : ''}" data-len="custom">custom</span><input type="number" id="sp-custom" min="10" max="600" value="${clipLen}" style="width:80px" class="${custom ? '' : 'hidden'}"> </div></div>
      <div class="field" style="margin:0"><label>Starts this many seconds before the moment</label><input type="number" id="sp-lead" min="0" max="300" value="${leadIn}" style="width:90px"></div>
      <span class="grow"></span>
      <button class="btn primary" id="sp-find">Find moments</button></div>
      <div class="small muted" style="margin-top:6px">Each clip is cut from <span id="sp-lead-txt"></span>; nothing renders until you click Render on a moment.</div>
      <div id="sp-progress" class="hidden" style="margin-top:10px"><div class="small" id="sp-stage"></div><div class="progress" style="margin-top:4px"><i id="sp-bar" style="width:0%"></i></div></div></div>
    <div id="sp-moments"></div>`;
  const tl = $('#tl', m), heat = $('#tl-heat', m), sel = $('#tl-sel', m), ha = $('#tl-a', m), hb = $('#tl-b', m), ia = $('#sp-a', m), ib = $('#sp-b', m);
  const pct = (t) => (t / dur * 100);
  const clamp = () => { a = Math.min(Math.max(0, a), dur); b = Math.min(Math.max(0, b), dur); if (b < a) [a, b] = [b, a]; if (b - a < 60) { if (a > dur - 60) a = Math.max(0, dur - 60); b = Math.min(dur, a + 60); } };
  const drawSel = () => { clamp(); sel.style.left = pct(a) + '%'; sel.style.width = pct(b - a) + '%'; ha.style.left = pct(a) + '%'; hb.style.left = pct(b) + '%'; ia.value = fmtTime(a); ib.value = fmtTime(b); $('#sp-len', m).textContent = `${fmtTime(b - a)} selected`; $$('#sp-presets .chip', m).forEach((ch) => ch.classList.remove('on')); };
  const leadTxt = () => { const li = Number($('#sp-lead', m).value) || 0; $('#sp-lead-txt', m).textContent = `${li} s before the moment to ${Math.max(0, clipLen - li)} s after it (${clipLen} s)`; };
  const drawHeat = (dens) => {
    const W = heat.width = tl.clientWidth || 800, H = heat.height = 40; const ctx = heat.getContext('2d');
    ctx.fillStyle = '#26262f'; ctx.fillRect(0, 0, W, H);
    const bins = dens.bins || [], bs = dens.bin_s || 60;
    (dens.covered || []).forEach(([s0, e0]) => { ctx.fillStyle = '#121217'; ctx.fillRect(s0 / dur * W, 0, Math.max(1, (e0 - s0) / dur * W), H); });
    if (bins.length) {
      const sorted = bins.filter((x) => x > 0).slice().sort((x, y) => x - y), ref = sorted[Math.floor(sorted.length * 0.95)] || 1;
      bins.forEach((n, i) => { if (!n) return; const x = i * bs / dur * W, w = Math.max(1, bs / dur * W), v = Math.min(1, n / ref); ctx.fillStyle = `rgba(31,209,214,${0.15 + 0.85 * v})`; ctx.fillRect(x, H - Math.max(2, v * H), w, Math.max(2, v * H)); });
    }
    const ov = $('#tl-overlays', m);
    ov.innerHTML = (sec.scanned_ranges || []).map(([s0, e0]) => `<div class="tl-scanned" style="left:${pct(s0)}%;width:${pct(e0 - s0)}%" title="already searched ${fmtTime(s0)}–${fmtTime(e0)}"></div>`).join('')
      + (sec.moments || []).map((mm) => `<div class="tl-moment ${mm.flags?.length ? 'flagged' : ''}" style="left:${pct(mm.t)}%" title="${fmtTime(mm.t)} · score ${mm.score}"></div>`).join('');
    const st = $('#sp-heat-status', m);
    st.innerHTML = dens.loading ? `<span class="warn-text">⟳ ${esc(dens.loading_message || 'Loading chat activity…')}</span>` : (dens.complete ? (dens.source === 'heatmap' ? "YouTube 'most replayed'" : (dens.source === 'none' ? 'no chat replay for this video' : 'chat activity loaded')) : `<a href="#" id="sp-load">Load chat activity</a>`);
    const ld = $('#sp-load', m); if (ld) ld.onclick = (e) => { e.preventDefault(); startDensity(); };
  };
  const startDensity = async () => { try { await post(`/api/videos/${videoId}/density`); pollDensity(); } catch (e) { toastErr(e); } };
  const pollDensity = () => { clearInterval(S.densPoll); S.densPoll = setInterval(async () => { if (!document.body.contains(m)) return clearInterval(S.densPoll); try { const fresh = await get(`/api/videos/${videoId}/section`); sec.density = fresh.density; sec.moments = fresh.moments; sec.scanned_ranges = fresh.scanned_ranges; drawHeat(fresh.density); if (!fresh.density.loading) clearInterval(S.densPoll); } catch (e) { clearInterval(S.densPoll); } }, 3000); };
  drawSel(); drawHeat(sec.density); leadTxt();
  if (!sec.density.complete && !sec.density.loading) startDensity(); else if (sec.density.loading) pollDensity();
  window.addEventListener('resize', () => { if (document.body.contains(m)) drawHeat(sec.density); }, { once: true });
  // drag handles (mouse and touch), click on the bar moves the nearest handle
  const frac = (e) => { const r = tl.getBoundingClientRect(); const x = (e.touches ? e.touches[0] : e).clientX; return Math.min(1, Math.max(0, (x - r.left) / r.width)) * dur; };
  let drag = null;
  const down = (which) => (e) => { drag = which; e.preventDefault(); };
  const move = (e) => { if (!drag) return; const t = frac(e); if (drag === 'a') a = Math.min(t, b - 60); else b = Math.max(t, a + 60); drawSel(); e.preventDefault(); };
  const up = () => { drag = null; };
  ha.onpointerdown = down('a'); hb.onpointerdown = down('b');
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  tl.onclick = (e) => { if (e.target === ha || e.target === hb) return; const t = frac(e); if (Math.abs(t - a) < Math.abs(t - b)) a = Math.min(t, b - 60); else b = Math.max(t, a + 60); drawSel(); };
  ia.onchange = () => { const t = parseHms(ia.value); if (t != null) { a = t; drawSel(); } else ia.value = fmtTime(a); };
  ib.onchange = () => { const t = parseHms(ib.value); if (t != null) { b = t; drawSel(); } else ib.value = fmtTime(b); };
  $$('#sp-presets .chip', m).forEach((ch) => ch.onclick = () => { const k = ch.dataset.preset; const len = { last30m: 1800, last1h: 3600, last2h: 7200 }[k]; a = len ? Math.max(0, dur - len) : 0; b = dur; drawSel(); ch.classList.add('on'); });
  $$('#sp-lens .chip', m).forEach((ch) => ch.onclick = () => { $$('#sp-lens .chip', m).forEach((x) => x.classList.toggle('on', x === ch)); const ci = $('#sp-custom', m); if (ch.dataset.len === 'custom') { ci.classList.remove('hidden'); clipLen = Number(ci.value) || 90; } else { ci.classList.add('hidden'); clipLen = Number(ch.dataset.len); } leadTxt(); });
  $('#sp-custom', m).oninput = (e) => { clipLen = Math.max(10, Number(e.target.value) || 10); leadTxt(); };
  $('#sp-lead', m).oninput = leadTxt;
  const getClipLen = () => clipLen, getLeadIn = () => Number($('#sp-lead', m).value) || 0;
  const v = { id: videoId, url: sec.url, platform: sec.platform };
  const momentsBox = $('#sp-moments', m);
  const showMoments = () => renderMoments(v, momentsBox, { clipLen: getClipLen, leadIn: getLeadIn, empty: 'No moments found in that section. Try a wider section or a different one.' });
  if (sec.moments.length) showMoments();
  const pollScan = () => { clearInterval(S.scanPoll); S.scanPoll = setInterval(async () => { if (!document.body.contains(m)) return clearInterval(S.scanPoll); try { const fresh = await get(`/api/videos/${videoId}/section`); $('#sp-stage', m).textContent = fresh.stage || ''; $('#sp-bar', m).style.width = Math.round((fresh.progress || 0) * 100) + '%'; if (!['queued', 'scanning'].includes(fresh.status)) { clearInterval(S.scanPoll); $('#sp-progress', m).classList.add('hidden'); $('#sp-find', m).disabled = false; sec.moments = fresh.moments; sec.scanned_ranges = fresh.scanned_ranges; drawHeat(sec.density); if (fresh.status === 'error') toast(fresh.stage || 'The search failed', 'err'); showMoments(); refreshState(); } } catch (e) { clearInterval(S.scanPoll); } }, 2000); };
  $('#sp-find', m).onclick = async () => {
    const btn = $('#sp-find', m); btn.disabled = true;
    try {
      await post(`/api/videos/${videoId}/rescan`, { window: { start: a, end: b }, clip_len: getClipLen(), lead_in: getLeadIn(), override: sec.status === 'skipped' });
      $('#sp-progress', m).classList.remove('hidden'); $('#sp-stage', m).textContent = 'Waiting to search…'; $('#sp-bar', m).style.width = '2%';
      pollScan();
    } catch (e) { toastErr(e); btn.disabled = false; }
  };
  if (['queued', 'scanning'].includes(sec.status)) { $('#sp-find', m).disabled = true; $('#sp-progress', m).classList.remove('hidden'); $('#sp-stage', m).textContent = sec.stage || ''; pollScan(); }
}

// ---------------------------------------------------------------- Review
VIEWS.review = async (el, arg) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  const filter = S.reviewFilter || 'pending';
  const openId = arg && arg.startsWith('clip/') ? Number(arg.slice(5)) : null;
  const videoArg = arg && !openId ? arg : '';
  const clips = await get(`/api/clips?campaign_id=${c.id}${filter === 'all' ? '' : '&status=' + filter}${videoArg ? '&video_id=' + videoArg : ''}`);
  S.clipOrder = clips.filter((x) => x.status !== 'rendering').map((x) => x.id);
  el.innerHTML = `
    <div class="row between"><div><h1>Review · ${esc(c.name)}</h1><p class="sub">${c.workspace ? 'No campaign rules here: only the 9:16 format check and clean text apply. Captions credit the streamer; edit the template per source.' : "Watch every clip. Trim it to open on something worth watching, fix the hook, tick the checklist, then approve or reject. Clips that fail a campaign rule can't be approved."}</p></div>
      <div class="chips">${[['pending', 'To review'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['posted', 'Posted'], ['rendering,error', 'Rendering / failed'], ['all', 'All']].map(([k, l]) => `<span class="chip ${filter === k ? 'on' : ''}" data-f="${k}">${l}</span>`).join('')}</div></div>
    <div class="clip-grid" id="clip-grid">${clips.length ? '' : `<div style="grid-column:1/-1">${({
      pending: emptyState('No clips waiting. Pick a moment to render', 'Sources', '#/sources'),
      approved: emptyState('Nothing approved yet. Review a clip and approve it', 'Review', '#/review'),
      rejected: emptyState('Nothing rejected.', '', ''),
      posted: emptyState('Nothing posted yet. Approve a clip, then publish it', 'Publish', '#/publish'),
      'rendering,error': emptyState('Nothing is being made and nothing failed.', '', ''),
      all: emptyState('No clips yet. Pick a moment to render', 'Sources', '#/sources'),
    })[filter] || emptyState('No clips yet. Pick a moment to render', 'Sources', '#/sources')}</div>`}</div>`;
  $$('.chip[data-f]').forEach((ch) => ch.onclick = () => { S.reviewFilter = ch.dataset.f; render(); });
  const g = $('#clip-grid');
  for (const cl of clips) {
    const d = document.createElement('div'); d.className = 'clip-card ' + cl.status;
    const fails = (cl.rule_results || []).filter((r) => !r.ok).length;
    d.innerHTML = `<div class="thumb ${cl.thumb_url ? '' : 'none'}" style="background-image:url('${cl.thumb_url || ''}')">${cl.thumb_url ? '' : `<span class="thumb-ph">${cl.status === 'rendering' ? 'Making clip…' : 'no preview'}<br>from ${fmtTime(cl.source_start)}</span>`}
        <div class="overlay-tag">${badge('clip', cl.status, cl)}${fails ? `<span class="badge bad">${fails} rule${fails > 1 ? 's' : ''} fail</span>` : (cl.status === 'pending' ? '<span class="badge ok">rules ok</span>' : '')}</div>
        ${cl.effective_duration ? `<span class="overlay-dur">${fmtTime(cl.effective_duration)}</span>` : ''}
        ${cl.status === 'rendering' ? '<div class="progress" style="position:absolute;left:10px;right:10px;bottom:12px"><i style="width:50%"></i></div>' : ''}</div>
      <div class="meta"><b>${esc(cl.hook || cl.title || 'Untitled')}</b>${esc((cl.video?.title || '').slice(0, 60))}<br>from ${fmtTime(cl.source_start)}${cl.status === 'error' ? `<br><span class="bad-text">${esc(errText(cl))}</span> <button class="btn sm yellow" data-retry="${cl.id}">Retry</button>` : ''}</div>`;
    const rb = $('[data-retry]', d); if (rb) rb.onclick = (e) => { e.stopPropagation(); post(`/api/clips/${cl.id}/rerender`).then(() => { toast('Retrying'); render(); }).catch(toastErr); };
    d.onclick = () => { if (cl.status !== 'rendering') openClip(cl.id); };
    g.appendChild(d);
  }
  if (openId && !$('#modal-root').innerHTML) openClip(openId);
};

async function openClip(id) {
  let cl = await get(`/api/clips/${id}`);
  const camp = S.campaigns.find((x) => x.id === cl.campaign_id) || campaign();
  const plats = camp.platforms || Object.keys(cl.post_text || {});
  let tab = plats[0] || 'tiktok';
  const order = S.clipOrder || [];
  const nextId = order[order.indexOf(cl.id) + 1] || null;
  const noFacecam = cl.source && !cl.source.facecam_decided;
  const m = modal(`<div class="modal-head"><div><h2 style="margin:0">${esc(cl.video?.title || 'Clip')}</h2><span class="small muted">from ${fmtTime(cl.source_start)} · ${cl.moment ? esc(momentStrength(cl.moment)) : ''}${cl.moment?.chat_words?.length ? ' · chat was saying: ' + cl.moment.chat_words.map(esc).join(', ') : ''}</span></div>
      <div class="row"><span class="small muted kbd-hint">space play · A approve · R reject · → next</span>${nextId ? `<button class="btn sm" id="next-clip">Next clip →</button>` : ''}<button class="btn ghost" onclick="closeModal()">✕</button></div></div>
    <div class="review-layout">
      <div class="review-video"><video id="rv" src="${cl.video_url}" controls playsinline></video></div>
      <div class="review-side">
        ${noFacecam ? `<div class="callout" id="fc-banner"><b>Facecam not set for this streamer — set it once.</b> <span class="small muted">Clips use the gameplay-only layout until then.</span> <button class="btn sm yellow" id="fc-open" style="margin-left:6px">Set it now</button><div id="fc-inline" class="hidden" style="margin-top:10px"></div></div>` : ''}
        <div class="panel">
          <div class="field"><label>Hook (a real sentence from the clip; this becomes the title)</label><input type="text" id="hook" value="${esc(cl.hook)}">
            <div class="chips" id="hook-cands"></div></div>
          <b>Before approving, check</b>
          <div id="checks">${cl.checklist_items.map((t, i) => `<label class="check"><input type="checkbox" data-i="${i}" ${cl.checklist && cl.checklist[i] ? 'checked' : ''}> ${esc(t)}</label>`).join('')}</div>
          <div class="row" style="margin-top:12px">
            <button class="btn primary" id="approve">Approve</button>
            <button class="btn danger" id="reject" ${cl.status === 'posted' ? 'disabled' : ''}>Reject</button>
            <span class="small" id="approve-why" style="flex:1"></span>
          </div>
        </div>
        <details class="panel sect"><summary>Trim <span class="small muted" id="trim-len"></span></summary>
          <div class="trim" style="margin-top:8px">
            <label class="small muted">Start <span id="ts-v"></span></label><input type="range" id="ts" min="0" max="${cl.duration}" step="0.1" value="${cl.trim_start}">
            <label class="small muted">End <span id="te-v"></span></label><input type="range" id="te" min="0" max="${cl.duration}" step="0.1" value="${cl.trim_end || cl.duration}">
            <div class="row"><button class="btn sm" id="ts-now">Start = here</button><button class="btn sm" id="te-now">End = here</button><button class="btn sm" id="trim-play">▶ Play trimmed</button><span class="grow"></span><button class="btn sm primary" id="trim-save">Save trim</button></div>
            <div class="small muted">Need more lead-in/out? <button class="btn sm ghost" id="shift-back">Re-cut 15 s earlier</button> <button class="btn sm ghost" id="shift-fwd">Re-cut 15 s later</button></div>
          </div></details>
        <details class="panel sect"><summary>Post text per platform</summary>
          <div class="tabs" id="pt-tabs" style="margin-top:8px">${plats.map((p) => `<button data-p="${p}" class="${p === tab ? 'on' : ''}">${p}</button>`).join('')}</div>
          <div id="pt-body"></div>
          <div class="row" style="margin-top:8px"><button class="btn sm" id="pt-regen">Rebuild from hook</button><button class="btn sm primary" id="pt-save">Save text</button></div></details>
        <details class="panel sect" id="rules-sect"><summary id="rules-summary"></summary><div id="rules" style="margin-top:8px"></div></details>
        <details class="panel sect" id="narr-sect"><summary>Narrated intro <span class="small muted" id="narr-sum"></span></summary><div id="narr-panel" style="margin-top:8px"></div></details>
        <details class="panel sect"><summary>More</summary>
          <div class="row" style="margin-top:8px"><button class="btn sm" id="rerender">Re-render</button><button class="btn sm" id="reveal">Reveal file</button><span class="grow"></span><button class="btn sm danger" id="delete">Delete</button></div>
          <div class="small muted" style="margin-top:8px">Approving triggers the final trimmed export. Posting happens on the Publish page.</div></details>
      </div>
    </div>`);
  const v = $('#rv', m), ts = $('#ts', m), te = $('#te', m);
  const minLen = camp.min_len || 61, maxLen = camp.max_len || 0;
  const trimOk = () => { const len = Number(te.value) - Number(ts.value); return len >= minLen && (!maxLen || len <= maxLen); };
  const updTrim = () => {
    let a = Number(ts.value), b = Number(te.value); if (b < a + 1) { b = a + 1; te.value = b; }
    $('#ts-v', m).textContent = fmtTime(a) + '.' + Math.round(a % 1 * 10); $('#te-v', m).textContent = fmtTime(b) + '.' + Math.round(b % 1 * 10);
    const len = b - a;
    $('#trim-len', m).innerHTML = `<span class="${trimOk() ? 'ok-text' : 'bad-text'}">${len.toFixed(1)} s</span> (needs ${minLen}${maxLen ? '–' + maxLen : '+'} s)`;
    gate();
  };
  const checks = () => Object.fromEntries($$('#checks input', m).map((i) => [i.dataset.i, i.checked]));
  const gate = () => {   // Approve is enabled only when every reason to block it is gone; the first reason shows inline
    const fails = (cl.rule_results || []).filter((r) => !r.ok);
    const n = cl.narration || {};
    let why = '';
    if (cl.status === 'posted') why = 'Already posted.';
    else if (!trimOk()) why = `The clip is ${(Number(te.value) - Number(ts.value)).toFixed(0)} s; it needs ${minLen}${maxLen ? '–' + maxLen : '+'} s. Open Trim.`;
    else if (fails.length) why = 'Fix first: ' + fails[0].detail;
    else if (n.enabled && n.status !== 'ready') why = 'Wait for the narrated intro to finish (or switch it off).';
    else if (!Object.values(checks()).every(Boolean) || $$('#checks input', m).length !== Object.values(checks()).filter(Boolean).length) why = 'Tick every item above.';
    $('#approve', m).disabled = !!why;
    $('#approve-why', m).textContent = why;
    $('#approve-why', m).className = 'small ' + (why ? 'warn-text' : 'ok-text');
    if (!why) $('#approve-why', m).textContent = 'Ready to approve.';
  };
  $$('#checks input', m).forEach((i) => i.onchange = gate);
  ts.oninput = () => { updTrim(); v.currentTime = Number(ts.value); };
  te.oninput = () => { updTrim(); v.currentTime = Math.max(0, Number(te.value) - 1); };
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
    const rr = cl.rule_results || [], fails = rr.filter((r) => !r.ok);
    const word = camp.workspace ? 'format checks' : 'campaign rules';
    $('#rules-summary', m).innerHTML = rr.length ? (fails.length ? `<span class="bad-text">${fails.length} of ${rr.length} ${word} fail</span>` : `<span class="ok-text">Passes all ${rr.length} ${word}</span>`) : 'Rules not checked yet';
    $('#rules', m).innerHTML = fails.length ? fails.map((r) => `<div class="rule bad"><span class="dot"></span><span>${esc(r.detail)}</span></div>`).join('') + `<details class="small muted" style="margin-top:6px"><summary>Show the ${rr.length - fails.length} that pass</summary>${rr.filter((r) => r.ok).map((r) => `<div class="rule"><span class="dot"></span><span>${esc(r.detail)}</span></div>`).join('')}</details>`
      : rr.map((r) => `<div class="rule"><span class="dot"></span><span>${esc(r.detail)}</span></div>`).join('') || '<div class="muted small">Not checked yet</div>';
    $('#rules-sect', m).open = fails.length > 0;
    gate();
  };
  renderRules(); updTrim();
  get(`/api/clips/${cl.id}/hooks`).then((cands) => {
    const alts = cands.filter((h) => h.text !== cl.hook).slice(0, 3);
    $('#hook-cands', m).innerHTML = alts.map((h) => `<span class="chip" title="said at ${fmtTime(h.t)}">${esc(h.text)}</span>`).join('');
    $$('#hook-cands .chip', m).forEach((ch) => ch.onclick = () => { $('#hook', m).value = ch.textContent; save({ hook: ch.textContent }).then(() => { ptBody(); toast('Hook set; post text rebuilt'); }); });
  });
  const ptBody = () => {
    const pt = (cl.post_text || {})[tab] || { title: '', text: '' };
    $('#pt-body', m).innerHTML = `${tab === 'youtube' ? `<div class="field"><label>Title (≤100)</label><input type="text" id="pt-title" value="${esc(pt.title)}"></div>` : ''}
      <div class="field"><label>${tab === 'youtube' ? 'Description' : 'Caption'}${camp.workspace ? '' : ` · must mention ${(camp.required_names || []).join(', ')} and include ${(camp.required_hashtags || []).join(' ')}`}</label><textarea id="pt-text">${esc(pt.text)}</textarea></div>`;
  };
  ptBody();
  $$('#pt-tabs button', m).forEach((b) => b.onclick = () => { collectPt(); tab = b.dataset.p; $$('#pt-tabs button', m).forEach((x) => x.classList.toggle('on', x === b)); ptBody(); });
  const collectPt = () => { const pt = { ...(cl.post_text || {}) }; pt[tab] = { title: $('#pt-title', m) ? $('#pt-title', m).value : '', text: $('#pt-text', m).value }; cl.post_text = pt; };
  $('#pt-save', m).onclick = async () => { collectPt(); if (await save({ post_text: cl.post_text, hook: $('#hook', m).value, title: (cl.post_text.youtube || {}).title || $('#hook', m).value })) toast('Post text saved'); };
  $('#pt-regen', m).onclick = async () => { if (await save({ hook: $('#hook', m).value })) { ptBody(); toast('Rebuilt'); } };
  $('#hook', m).onchange = () => save({ hook: $('#hook', m).value }).then(ptBody);
  const renderNarr = () => {
    const n = cl.narration || {};
    const box = $('#narr-panel', m);
    $('#narr-sum', m).textContent = !cl.narration_allowed ? '(not allowed here)' : (n.enabled ? `(${n.status === 'ready' ? 'on' : n.status === 'error' ? 'failed' : 'rendering…'})` : '(off)');
    if (!cl.narration_allowed) { box.innerHTML = `<div class="small muted">${esc(cl.narration_reason)}</div>`; gate(); return; }
    box.innerHTML = `<div class="small muted" style="margin:0 0 8px">A synthetic voice reads a one-sentence cliffhanger over the frozen first frame (3–6 s), then the clip plays. The hook is written by a model from this clip's transcript; edit it freely. Posts get the altered/synthetic content disclosure automatically.</div>
      <div class="field"><label>Hook (under 15 words, factual, no profanity)</label><input type="text" id="n-hook" value="${esc(n.chosen || '')}"></div>
      <div class="chips" id="n-alts">${(n.hooks || []).map((h) => `<span class="chip ${h === n.chosen ? 'on' : ''}">${esc(h)}</span>`).join('')}</div>
      <div class="row" style="margin-top:8px"><button class="btn sm" id="n-gen">Generate 3 hooks</button>
        <select id="n-engine" style="width:auto"><option value="">Voice: auto (Piper → system)</option><option value="piper" ${n.engine === 'piper' ? 'selected' : ''}>Piper (local)</option><option value="system" ${n.engine === 'system' ? 'selected' : ''}>System voice</option><option value="elevenlabs" ${n.engine === 'elevenlabs' ? 'selected' : ''}>ElevenLabs</option></select>
        <span class="grow"></span>
        ${n.enabled ? `<button class="btn sm" id="n-off">Turn narration off</button><button class="btn sm primary" id="n-on">Re-render intro</button>` : `<button class="btn sm primary" id="n-on">Enable narration & render intro</button>`}</div>
      ${n.status === 'error' ? `<div class="small bad-text" style="margin-top:6px">${esc(n.error_text || reasonOf(n.error))}</div>` : ''}
      ${n.enabled && n.status === 'ready' ? `<div class="row" style="margin-top:8px"><span class="small muted">Intro ${(n.intro_duration || 0).toFixed(1)} s (${esc(n.engine || '')} voice) · total ${fmtTime(cl.effective_duration)}</span><button class="btn sm" id="n-preview">▶ Preview with intro</button><button class="btn sm ghost" id="n-plain">▶ Original</button></div>` : ''}`;
    $$('#n-alts .chip', m).forEach((ch) => ch.onclick = () => { $('#n-hook', m).value = ch.textContent; });
    $('#n-gen', m).onclick = async () => { const b = $('#n-gen', m); b.disabled = true; b.textContent = 'Generating…'; try { const r = await post(`/api/clips/${cl.id}/narration/hooks`); cl.narration = r.narration; renderNarr(); toast('Hooks ready; pick or edit one'); } catch (e) { toastErr(e); b.disabled = false; b.textContent = 'Generate 3 hooks'; } };
    $('#n-on', m).onclick = async () => { try { cl = await put(`/api/clips/${cl.id}/narration`, { hook: $('#n-hook', m).value, engine: $('#n-engine', m).value, enabled: true }); renderRules(); renderNarr(); toast('Rendering the intro in the background'); } catch (e) { toastErr(e); } };
    const off = $('#n-off', m); if (off) off.onclick = async () => { try { cl = await put(`/api/clips/${cl.id}/narration`, { enabled: false }); renderRules(); renderNarr(); } catch (e) { toastErr(e); } };
    const pv = $('#n-preview', m); if (pv) pv.onclick = () => { v.src = cl.narration_preview_url; v.play(); };
    const pl = $('#n-plain', m); if (pl) pl.onclick = () => { v.src = cl.video_url; v.play(); };
    gate();
  };
  renderNarr();
  if ((cl.narration || {}).enabled && (cl.narration || {}).status === 'rendering') { const iv = setInterval(async () => { if (!$('#narr-panel')) return clearInterval(iv); const fresh = await get(`/api/clips/${cl.id}`); if ((fresh.narration || {}).status !== 'rendering') { clearInterval(iv); cl = fresh; renderRules(); renderNarr(); } }, 4000); }
  const fcOpen = $('#fc-open', m);
  if (fcOpen) fcOpen.onclick = () => { const box = $('#fc-inline', m); box.classList.toggle('hidden'); if (!box.innerHTML) facecamPicker(cl.source, box, (saved) => { $('#fc-banner', m).innerHTML = saved ? '<b class="ok-text">Facecam saved.</b> <span class="small muted">Use Re-render (under More) to apply it to this clip; new clips use it automatically.</span>' : '<span class="small muted">Gameplay-only layout kept for this streamer.</span>'; }); };
  const approve = async () => {
    if ($('#approve', m).disabled) { toast($('#approve-why', m).textContent, 'warn'); return; }
    collectPt();
    if (!(await save({ post_text: cl.post_text, hook: $('#hook', m).value, trim_start: Number(ts.value), trim_end: Number(te.value), checklist: checks() }))) return;
    try { await post(`/api/clips/${cl.id}/approve`, { checklist: checks() }); toast('Approved. Final export is being prepared.'); if (nextId) openClip(nextId); else { closeModal(); render(); } }
    catch (e) { const f = e.data && e.data.failed; toast((e.data && e.data.message) || e.message + (f && f.length ? ': ' + f.map((x) => x.detail).join('; ') : ''), 'err'); cl = await get(`/api/clips/${cl.id}`); renderRules(); }
  };
  const reject = () => { if (cl.status === 'posted') return; post(`/api/clips/${cl.id}/reject`).then(() => { toast('Rejected'); if (nextId) openClip(nextId); else { closeModal(); render(); } }).catch(toastErr); };
  $('#approve', m).onclick = approve;
  $('#reject', m).onclick = reject;
  const nb = $('#next-clip', m); if (nb) nb.onclick = () => openClip(nextId);
  $('#rerender', m).onclick = () => post(`/api/clips/${cl.id}/rerender`).then(() => { closeModal(); toast('Re-rendering'); render(); }).catch(toastErr);
  $('#reveal', m).onclick = () => post(`/api/clips/${cl.id}/reveal`).catch(toastErr);
  $('#delete', m).onclick = async () => { try { await del(`/api/clips/${cl.id}`); closeModal(); render(); } catch (e) { if (e.status === 409 && await confirmDlg(e.data.message, 'Delete anyway')) { await del(`/api/clips/${cl.id}?force=true`); closeModal(); render(); } else toastErr(e); } };
  // keyboard: space play/pause · A approve · R reject · → next clip (ignored while typing)
  const onKey = (e) => {
    if (!document.body.contains(m)) { document.removeEventListener('keydown', onKey); return; }
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
    if (e.key === ' ') { e.preventDefault(); if (v.paused) v.play(); else v.pause(); }
    else if (e.key === 'a' || e.key === 'A') { e.preventDefault(); approve(); }
    else if (e.key === 'r' || e.key === 'R') { e.preventDefault(); reject(); }
    else if (e.key === 'ArrowRight' && nextId) { e.preventDefault(); openClip(nextId); }
    else if (e.key === 'Escape') closeModal();
  };
  document.addEventListener('keydown', onKey);
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
    ${usable.length ? '' : `<div class="callout pink">No accounts for this campaign's platforms yet. <a href="#/settings/accounts">Add accounts</a> (assisted posting needs no setup).</div>`}
    <h2>Approved clips</h2>
    <div class="clip-grid" id="pub-grid">${clips.length ? '' : `<div style="grid-column:1/-1">${emptyState('No approved clips. Review a clip and approve it', 'Review', '#/review')}</div>`}</div>
    <h2>Queue</h2>
    <div class="panel" id="queue"></div>`;
  const g = $('#pub-grid');
  for (const cl of clips) {
    const d = document.createElement('div'); d.className = 'clip-card ' + cl.status;
    const done = cl.posts.filter((p) => !['failed', 'cancelled'].includes(p.status)).map((p) => p.platform);
    d.innerHTML = `<div class="thumb ${cl.thumb_url ? '' : 'none'}" style="background-image:url('${cl.thumb_url || ''}')">${cl.thumb_url ? '' : `<span class="thumb-ph">no preview<br>from ${fmtTime(cl.source_start)}</span>`}<div class="overlay-tag">${done.map((p) => `<span class="badge posted">${p}</span>`).join('')}${cl.final_path ? '' : '<span class="badge warn">exporting…</span>'}${(cl.narration || {}).enabled ? '<span class="badge pink">AI voice</span>' : ''}</div><span class="overlay-dur">${fmtTime(cl.effective_duration)}</span></div>
      <div class="meta"><b>${esc(cl.hook || cl.title)}</b><div class="row" style="margin-top:6px"><button class="btn sm primary" data-post ${usable.length ? '' : 'disabled'}>Post / schedule</button><button class="btn sm" data-view>Preview</button></div></div>`;
    $('[data-post]', d).onclick = () => openPostDialog(cl, usable, c);
    $('[data-view]', d).onclick = () => openClip(cl.id);
    g.appendChild(d);
  }
  renderQueue($('#queue'), posts);
};

function renderQueue(el, posts) {
  if (!posts.length) { el.innerHTML = '<div class="muted">No posts yet. Pick an approved clip above and click Post / schedule.</div>'; return; }
  el.innerHTML = `<table><tr><th>When</th><th>Clip</th><th>Account</th><th>Status</th><th>Link</th><th></th></tr>${posts.map((p) => `<tr>
    <td class="small">${p.scheduled_at ? fmtDate(p.scheduled_at) : (p.posted_at ? fmtDate(p.posted_at) : fmtDate(p.created_at))}${p.tz ? `<br><span class="muted">${esc(p.tz)}</span>` : ''}</td>
    <td class="small">${esc(p.clip?.hook || p.title || '#' + p.clip_id)}</td>
    <td class="small">${esc(p.account?.label || '')}<br><span class="badge ${p.route}">${p.route}</span>${p.options?.synthetic ? ' <span class="badge pink" title="AI voice intro: synthetic-content disclosure set">AI voice</span>' : ''}</td>
    <td class="small">${badge('post', p.status, p)}${p.status === 'failed' ? `<div class="small" style="margin-top:4px">${p.platform_message && p.platform_message !== p.error_text ? `<span class="muted">${esc(p.platform)} said: </span><span class="mono">${esc(p.platform_message)}</span><br>` : ''}<span class="warn-text">Fix: ${esc(p.fix || '')}</span></div>` : `<br><span class="muted">${esc(p.state_detail || '')}</span>`}</td>
    <td class="small">${p.url ? `<a href="${esc(p.url)}" target="_blank">open ↗</a>` : (p.link_state === 'pending' ? '<span class="warn-text">fetching link…</span>' : (['manual', 'private', 'draft', 'live'].includes(p.status) ? `<div class="row"><input type="text" placeholder="paste post link" data-link="${p.id}" style="width:170px"><button class="btn sm" data-mark="${p.id}">${p.status === 'live' ? 'Save link' : 'Live'}</button></div>` : ''))}</td>
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
// A post is 'active' (countdown running), 'missed' (deadline passed, not submitted) or 'done' (submitted).
const subState = (p) => p.submitted_at ? 'done' : (p.submission_deadline && new Date(p.submission_deadline) <= Date.now() ? 'missed' : 'active');
VIEWS.submissions = async (el) => {
  const c = campaign();
  if (!c) { el.innerHTML = noCampaign(); return; }
  if (c.workspace) { el.innerHTML = `<h1>Submissions</h1><p class="sub">The Streamer clips workspace has no campaign, so there is nothing to submit. Pick a campaign in the sidebar.</p>`; return; }
  const order = { active: 0, missed: 1, done: 2 };
  const posts = (await get(`/api/posts?campaign_id=${c.id}&status=live`)).sort((a, b) => order[subState(a)] - order[subState(b)] || (a.submission_deadline || '').localeCompare(b.submission_deadline || ''));
  el.innerHTML = `<h1>Submissions · ${esc(c.name)}</h1><p class="sub">Every live post must be submitted to the campaign within ${c.submit_minutes} minutes. Copy the link, open the submission page, paste, then tick Submitted. Never edit or delete a submitted post.</p>
    ${c.submission_url ? '' : '<div class="callout pink">This campaign has no submission page URL yet. Add it in the campaign rules.</div>'}
    <div id="subs">${posts.length ? '' : '<div class="empty">No live posts yet. <a class="btn sm primary" href="#/publish" style="margin-left:8px">Publish a clip →</a></div>'}</div>`;
  const box = $('#subs');
  if (!posts.length) return;
  box.innerHTML = posts.map((p) => {
    const st = subState(p);
    const link = p.url
      ? `<a href="${esc(p.url)}" target="_blank">${esc(p.url.replace(/^https?:\/\/(www\.)?/, '').slice(0, 60))}</a>`
      : (p.link_state === 'pending'
        ? `<span class="warn-text">Posted, fetching link…</span> <span class="small muted">(the service sends it within a few minutes)</span>`
        : `<div class="row"><span class="warn-text small">No link yet.</span><a class="btn sm" href="${esc(p.profile_url)}" target="_blank">Open on ${esc(p.platform)}</a><input type="text" placeholder="paste the post link" data-link="${p.id}" style="width:230px"><button class="btn sm" data-mark="${p.id}">Save link</button></div>`);
    return `<div class="sub-card ${st}" data-row="${p.id}">
      <div class="sub-cd"><div class="countdown" data-dl="${p.submission_deadline || ''}" data-st="${st}"></div><div class="small muted">live ${fmtDate(p.posted_at)}</div></div>
      <div class="sub-body"><b>${esc(p.clip?.hook || p.title)}</b><div class="small muted">${esc(p.account?.label || '')} · <span class="badge">${p.platform}</span></div><div class="small" style="margin-top:4px">${link}</div>
        ${st === 'missed' ? '<div class="small bad-text" style="margin-top:4px">Deadline passed. If you submitted outside the app, mark it below; the campaign may reject late submissions.</div>' : ''}</div>
      <div class="sub-actions row">${p.url ? `<button class="btn sm" data-copy="${esc(p.url)}">Copy link</button>${c.submission_url ? `<button class="btn sm yellow" data-open>Copy + open submission page</button>` : ''}` : ''}
        ${st === 'done' ? `<span class="badge ok">submitted ${fmtDate(p.submitted_at)}</span>` : (st === 'missed' ? `<button class="btn sm" data-submitted="${p.id}">Mark submitted anyway</button>` : `<button class="btn sm primary" data-submitted="${p.id}" ${p.url ? '' : 'disabled title="Wait for the link first"'}>Submitted ✓</button>`)}</div></div>`;
  }).join('');
  $$('[data-copy]', box).forEach((b) => b.onclick = () => navigator.clipboard.writeText(b.dataset.copy).then(() => toast('Link copied')));
  $$('[data-open]', box).forEach((b) => b.onclick = () => { navigator.clipboard.writeText($('[data-copy]', b.parentElement).dataset.copy); window.open(c.submission_url, '_blank'); });
  $$('[data-submitted]', box).forEach((b) => b.onclick = () => post(`/api/posts/${b.dataset.submitted}/submitted`).then(render).catch(toastErr));
  $$('[data-mark]', box).forEach((b) => b.onclick = () => post(`/api/posts/${b.dataset.mark}/mark_live`, { url: $(`[data-link="${b.dataset.mark}"]`, box).value }).then(() => { toast('Link saved'); render(); }).catch(toastErr));
  const tick = () => $$('.countdown', box).forEach((d) => {
    if (d.dataset.st === 'done') { d.textContent = 'done'; d.className = 'countdown'; return; }
    if (!d.dataset.dl) { d.textContent = '—'; return; }
    const left = (new Date(d.dataset.dl) - Date.now()) / 1000;
    if (left <= 0) { if (d.textContent !== 'Missed') { d.textContent = 'Missed'; d.className = 'countdown late'; if (d.dataset.st === 'active') render(); } return; }
    d.textContent = fmtTime(left); d.className = 'countdown ' + (left < 600 ? 'soon' : '');
  });
  tick(); clearInterval(S.tick); S.tick = setInterval(tick, 1000);
  if (posts.some((p) => p.link_state === 'pending')) { clearInterval(S.linkPoll); S.linkPoll = setInterval(() => { if (S.view === 'submissions' && !$('#modal-root').innerHTML) render(); else clearInterval(S.linkPoll); }, 15000); }
};

// ---------------------------------------------------------------- Settings (tabs: Tuning · Setup · Accounts · Stats)
const PANES = {};
const SETTINGS_TABS = [['tuning', 'Tuning'], ['setup', 'Setup'], ['accounts', 'Accounts'], ['stats', 'Stats']];
VIEWS.settings = async (el, arg) => {
  const tab = SETTINGS_TABS.some(([k]) => k === arg) ? arg : 'tuning';
  el.innerHTML = `<h1>Settings</h1><div class="tabs" style="margin-bottom:16px">${SETTINGS_TABS.map(([k, l]) => `<a href="#/settings/${k}" class="${k === tab ? 'on' : ''}">${l}</a>`).join('')}</div><div id="pane"></div>`;
  await PANES[tab]($('#pane'));
};

// ---------------------------------------------------------------- Stats
PANES.stats = async (el) => {
  const c = campaign();
  const st = await get(`/api/stats${c ? '?campaign_id=' + c.id : ''}`);
  el.innerHTML = `<div class="row between"><div><h2 style="margin-top:0">Stats</h2><p class="sub">Views where the platform API provides them (direct and service routes). Assisted posts show no views. Estimated earnings use the campaign's rate and cap; pool campaigns pay by share of approved views, which the app can't know.</p></div><button class="btn" id="refresh">Refresh views</button></div>
    <div class="stat-tiles">${st.campaigns.map((x) => `<div class="tile"><div class="n">${fmtNum(x.views)}</div><div class="l">${esc(x.campaign.name)} views over ${x.posts} live posts</div></div>
      <div class="tile"><div class="n">${x.campaign.pay_model === 'pool' ? '$' + fmtNum(x.campaign.pool_budget) + ' pool' : '$' + x.earnings.toFixed(2)}</div><div class="l">${x.campaign.pay_model === 'pool' ? `pool · ${x.campaign.min_views ? fmtNum(x.views) + ' / ' + fmtNum(x.campaign.min_views) + ' views needed' : 'split by view share'}` : 'estimated earnings'}</div></div>`).join('') || emptyState('No live posts yet. Publish an approved clip', 'Publish', '#/publish')}</div>
    <div class="panel" style="margin-top:14px"><table><tr><th>Post</th><th>Platform</th><th>Views</th><th>Est.</th><th>Checked</th></tr>${st.posts.map((p) => `<tr><td class="small"><a href="${esc(p.url || '#')}" target="_blank">${esc(p.clip?.hook || p.title)}</a></td><td><span class="badge">${p.platform}</span> <span class="badge ${p.route}">${p.route}</span></td><td>${p.views == null ? '<span class="muted">n/a</span>' : fmtNum(p.views)}</td><td>${p.earnings_estimate ? '$' + p.earnings_estimate.toFixed(2) : ''}</td><td class="small muted">${p.views_checked_at ? fmtDate(p.views_checked_at) : ''}</td></tr>`).join('')}</table></div>`;
  $('#refresh').onclick = () => post(`/api/stats/refresh${c ? '?campaign_id=' + c.id : ''}`).then(() => toast('Refreshing in the background')).catch(toastErr);
};

// ---------------------------------------------------------------- Accounts
PANES.accounts = async (el) => {
  const [accounts, routes] = await Promise.all([get('/api/accounts'), get('/api/routes')]);
  el.innerHTML = `<div class="row between"><div><h2 style="margin-top:0">Accounts</h2><p class="sub">Each account posts through one route. Direct = your own developer app on the platform's official API. Service = an audited third-party posting API. Assisted = you upload, the app does the rest.</p></div><button class="btn primary" id="add-acc">Add account</button></div>
    <div class="grid cols-2" id="acc-grid">${accounts.length ? '' : '<div class="empty">No accounts yet. Assisted posting works immediately. <button class="btn sm primary" id="add-acc-empty" style="margin-left:8px">Add account →</button></div>'}</div>
    <h2>What each route can do</h2><div class="panel"><table><tr><th>Route</th><th>Public posts</th><th>Scheduling</th><th>Notes</th></tr>${Object.values(routes).map((r) => `<tr><td><span class="badge">${r.platform}</span> <span class="badge ${r.route}">${r.route}</span></td><td>${r.public ? '<span class="ok-text">yes</span>' : '<span class="warn-text">private only until audited</span>'}</td><td>${r.native_schedule ? 'on the platform/service' : 'this app (must be running)'}</td><td class="small muted">${esc(r.notes)}</td></tr>`).join('')}</table></div>`;
  const g = $('#acc-grid');
  for (const a of accounts) {
    const d = document.createElement('div'); d.className = 'panel';
    d.innerHTML = `<div class="row between"><div><b>${esc(a.label)}</b> ${a.handle ? `<span class="muted">${esc(a.handle)}</span>` : ''}</div><div><span class="badge">${a.platform}</span> <span class="badge ${a.route}">${a.route}</span> ${badge('account', a.status, a.status_detail)}</div></div>
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
  const ae = $('#add-acc-empty'); if (ae) ae.onclick = () => $('#add-acc').click();
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
PANES.setup = async (el) => {
  const s = await get('/api/setup/checks');
  const allOk = s.checks.filter((c) => c.name !== 'TwitchDownloaderCLI').every((c) => c.ok);
  el.innerHTML = `<h2 style="margin-top:0">Setup</h2><p class="sub">First run: check the tools, then connect accounts. Everything downloads into the app's data folder; nothing needs a terminal after install.</p>
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
    <div class="panel" style="margin-top:14px"><h3 style="margin-top:0">Narration (optional AI hook intro)</h3>
      <div class="form-grid">
        <div class="field"><label>Hook writer (LLM)</label><select id="set-llm"><option value="anthropic" ${s.settings.llm_provider !== 'openai' ? 'selected' : ''}>Anthropic Claude</option><option value="openai" ${s.settings.llm_provider === 'openai' ? 'selected' : ''}>OpenAI</option></select></div>
        <div class="field"><label>Anthropic API key ${s.secrets.anthropic_api_key ? `<span class="ok-text">(saved ${esc(s.secrets.anthropic_api_key)})</span>` : ''}</label><input type="password" id="sec-anth"></div>
        <div class="field"><label>OpenAI API key ${s.secrets.openai_api_key ? `<span class="ok-text">(saved ${esc(s.secrets.openai_api_key)})</span>` : ''}</label><input type="password" id="sec-oai"></div>
        <div class="field"><label>Voice engine</label><select id="set-tts"><option value="auto" ${s.settings.tts_engine === 'auto' ? 'selected' : ''}>Auto (Piper, then system voice)</option><option value="piper" ${s.settings.tts_engine === 'piper' ? 'selected' : ''}>Piper (local, free)</option><option value="system" ${s.settings.tts_engine === 'system' ? 'selected' : ''}>System voice</option><option value="elevenlabs" ${s.settings.tts_engine === 'elevenlabs' ? 'selected' : ''}>ElevenLabs</option></select></div>
        <div class="field"><label>ElevenLabs API key ${s.secrets.elevenlabs_api_key ? '<span class="ok-text">(saved)</span>' : ''}</label><input type="password" id="sec-el"></div>
      </div>
      <div>${s.tts.map((t) => `<div class="rule ${t.ok ? '' : 'bad'}"><span class="dot"></span><b style="min-width:200px">${esc(t.label)}</b><span class="grow">${esc(t.detail)}</span>${t.id === 'piper' && !t.ok ? '<button class="btn sm" data-action="download_piper_voice">Download voice</button>' : ''}</div>`).join('')}
        <div class="rule ${s.llm.ok ? '' : 'bad'}"><span class="dot"></span><b style="min-width:200px">Hook writer</b><span class="grow">${esc(s.llm.ok ? 'key saved for ' + s.llm.provider : s.llm.detail)}</span></div></div>
      <div class="small muted" style="margin-top:6px">Narration is off per campaign by default and locked off where the rules ban AI content. Hooks are always shown to you before anything renders.</div></div>
    <div class="row" style="margin-top:14px"><button class="btn primary" id="sec-save">Save credentials</button><span class="grow"></span>${allOk ? '<button class="btn yellow" id="setup-done">Setup complete → go to Today</button>' : '<span class="muted small">Fix the red items above to finish setup.</span>'}</div>
    <p class="small muted" style="margin-top:14px">Credentials are stored in <code>data/secrets.json</code> (gitignored, user-only permissions), never in the repository. See <code>.env.example</code> for the list.</p>`;
  $$('[data-action]').forEach((b) => b.onclick = () => post('/api/setup/action', { action: b.dataset.action }).then(() => { toast('Downloading in the background'); b.disabled = true; }).catch(toastErr));
  $('#sec-save').onclick = async () => {
    const body = {}; const take = (id, key) => { const v = $(id).value.trim(); if (v) body[key] = v; };
    take('#sec-yt', 'youtube_client_secret'); take('#sec-ttk', 'tiktok_client_key'); take('#sec-tts', 'tiktok_client_secret'); take('#sec-fbid', 'facebook_app_id'); take('#sec-fbs', 'facebook_app_secret'); take('#sec-svc', 'service_api_key');
    take('#sec-anth', 'anthropic_api_key'); take('#sec-oai', 'openai_api_key'); take('#sec-el', 'elevenlabs_api_key');
    try { await put('/api/settings', { llm_provider: $('#set-llm').value, tts_engine: $('#set-tts').value }); } catch (e) { toastErr(e); }
    if (!Object.keys(body).length) { toast('Settings saved'); render(); return; }
    try { await post('/api/setup/secrets', body); toast('Saved'); render(); } catch (e) { toastErr(e); }
  };
  const done = $('#setup-done'); if (done) done.onclick = () => post('/api/setup/done').then(() => { S.state.setup_done = true; location.hash = '#/'; });
};

// ---------------------------------------------------------------- Settings
PANES.tuning = async (el) => {
  const s = await get('/api/settings');
  const F = [
    ['clip_len', 'Default clip length in seconds (the section picker offers 61 / 90 / 120 / custom)', 'number'], ['lead_in', 'Default lead-in: seconds before the moment the clip starts', 'number'],
    ['max_moments', 'Max moments per search', 'number'], ['auto_render_top', 'Auto-render the top N moments after a search (default 0: nothing renders until you click Render)', 'number'], ['spam_fraction', 'Flag a moment as chat spam when this share of messages repeat one thing', 'number'], ['min_words_per_min', 'Flag a clip as low speech below this many words per minute', 'number'], ['min_gap', 'Minimum gap between moments (s)', 'number'],
    ['skip_start', 'Ignore the first N seconds of a stream', 'number'], ['chat_spike_factor', 'Chat spike factor (× usual rate = score 1)', 'number'],
    ['audio_spike_db', 'Audio spike (dB above usual = score 1)', 'number'], ['whisper_model', 'Whisper model (tiny / base / small / medium)', 'text'],
    ['facecam_fraction', 'Facecam height as a fraction of the frame', 'number'], ['cache_cap_mb', 'Cache cap (MB)', 'number'], ['watch_interval_min', 'Check sources for new videos and live streams every N minutes (default 10)', 'number'],
  ];
  el.innerHTML = `<h2 style="margin-top:0">Tuning</h2><p class="sub">Moment detection and rendering. Defaults match the prototype that worked on real footage.</p>
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
