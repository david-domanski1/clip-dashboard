// Health: tool versions, one-click yt-dlp update, a self-test, and a Today card when downloads fail because yt-dlp is behind.
(() => {
  const css = document.createElement('style');
  css.textContent = `
    .hx-verdict{display:flex;gap:12px;align-items:center;padding:12px 16px;border:1px solid var(--line);border-left:3px solid var(--muted);border-radius:12px;background:var(--surface);margin:0 0 14px}
    .hx-verdict.warn{border-left-color:var(--yellow)}.hx-verdict.ok{border-left-color:var(--green)}
    .hx-verdict p{margin:0;flex:1;color:var(--text-2)}
    .hx-table{width:100%;border-collapse:collapse;font-size:13px}
    .hx-table th{text-align:left;font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);padding:0 10px 8px 0}
    .hx-table td{padding:8px 10px 8px 0;border-top:1px solid var(--line);vertical-align:middle}
    .hx-table td.hx-act{text-align:right;width:1%;white-space:nowrap}
    .hx-table .hx-role{color:var(--muted);font-size:12px}
    .hx-progress{font-size:12px;color:var(--muted);margin-top:8px;font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .hx-steps .rule b{min-width:230px}
    .hx-events{list-style:none;margin:0;padding:0}
    .hx-events li{display:flex;gap:12px;padding:7px 0;border-top:1px solid var(--line);font-size:13px;color:var(--text-2)}
    .hx-events li:first-child{border-top:0}
    .hx-events time{color:var(--faint);white-space:nowrap;min-width:150px;font-size:12px}
    .hx-events .hx-kind{font-weight:700;min-width:110px}
    .hx-card .step-n{font-size:16px}
  `;
  document.head.appendChild(css);

  SETTINGS_TABS.push(['health', 'Health']);
  let H = null;                 // last /api/health answer, shared by the Today card and the pane
  const pollers = {};

  async function refreshHealth() {
    try { H = await get('/api/health'); } catch (e) { console.error('health', e); }
    return H;
  }

  function cardHtml() {
    if (!H || !H.actionable) return '';
    const running = H.update_job;
    return `<div class="step warn hx-card">
      <div class="step-n">↑</div>
      <div class="step-body"><div class="step-title">Downloads need a tool update</div><div class="step-sub">${esc(H.verdict)}${running ? ' <span class="muted">(updating now…)</span>' : ''}</div></div>
      <button class="btn primary" data-hx-update ${running ? 'disabled' : ''}>Update yt-dlp</button>
    </div>`;
  }
  TODAY_EXTRAS.push(() => cardHtml());

  // Follow one job until it finishes, showing its progress line in `out` (polled from /api/jobs).
  function follow(jobId, out, onDone) {
    clearInterval(pollers[jobId]);
    const tick = async () => {
      let job = null;
      try { job = (await get('/api/jobs?limit=40')).find((j) => j.id === jobId); } catch (e) { return; }
      if (!job) return;
      if (out) out.textContent = (job.status === 'queued' ? 'Waiting for a free worker… ' : '') + (job.message || '');
      if (['done', 'error', 'cancelled'].includes(job.status)) {
        clearInterval(pollers[jobId]); delete pollers[jobId];
        if (job.status === 'done') toast(job.message || 'Done');
        else toast(job.error_text || job.message || 'It failed', 'err');
        onDone && onDone(job);
      }
    };
    pollers[jobId] = setInterval(tick, 1500);
    tick();
  }

  async function startUpdate(btn, out) {
    if (btn) btn.disabled = true;
    try {
      const r = await post('/api/health/update_yt_dlp');
      toast(r.already_running ? 'An update is already running' : 'Updating yt-dlp in the background');
      follow(r.job_id, out, async () => { await refreshHealth(); render(); });
    } catch (e) { toastErr(e); if (btn) btn.disabled = false; }
  }
  // The Today card is plain HTML from TODAY_EXTRAS, so its button is wired here once for the whole page.
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-hx-update]');
    if (b) startUpdate(b, $('#hx-update-progress'));
  });

  const stepRow = (s) => `<div class="rule ${s.status === 'failed' ? 'bad' : ''}"><span class="dot" ${s.status === 'skipped' ? 'style="background:var(--faint);box-shadow:none"' : ''}></span><b>${esc(s.label)}</b><span class="grow">${esc(s.status === 'ok' ? 'passed' : s.status === 'skipped' ? 'skipped' : 'failed')}${s.detail ? ` <span class="muted">· ${esc(s.detail)}</span>` : ''}${s.seconds != null ? ` <span class="muted small">(${s.seconds} s)</span>` : ''}</span></div>`;
  const KIND = { download_failure: 'Download failed', update: 'Updated', selftest: 'Self-test' };
  const STEP_SHORT = { video_info: 'video info', ffmpeg: 'ffmpeg', whisper: 'Whisper' };
  const eventRow = (ev) => {
    let detail = ev.detail;
    if (ev.kind === 'selftest' && ev.steps) detail = (ev.steps.ok ? 'passed' : 'failed') + ' · ' + ev.steps.steps.map((s) => `${STEP_SHORT[s.name] || s.name}: ${s.status}`).join(', ');
    return `<li><time>${esc(fmtDate(ev.t))}</time><span class="hx-kind">${esc(KIND[ev.kind] || ev.kind)}</span><span class="grow">${esc(detail)}</span></li>`;
  };

  PANES.health = async (el) => {
    const h = await refreshHealth();
    if (!h) { el.innerHTML = '<div class="callout pink">Could not load the health check.</div>'; return; }
    const st = h.selftest;
    el.innerHTML = `<h2 style="margin-top:0">Health</h2><p class="sub">When YouTube or Twitch change something, downloads start failing until yt-dlp is updated. This screen tells you when that is the case and updates it with one click.</p>
      <div class="hx-verdict ${h.actionable ? 'warn' : h.level}"><p>${esc(h.verdict)}</p>${h.actionable || h.update_available ? `<button class="btn primary" data-hx-update ${h.update_job ? 'disabled' : ''}>Update yt-dlp</button>` : ''}</div>
      <div class="panel"><h3 style="margin-top:0">Tools</h3>
        <table class="hx-table"><thead><tr><th>Tool</th><th>Installed</th><th>Latest</th><th></th></tr></thead><tbody>
        ${h.versions.map((v) => `<tr><td><b>${esc(v.name)}</b><div class="hx-role">${esc(v.role)}</div></td>
          <td>${v.installed ? esc(v.installed) : `<span class="muted">${esc(v.detail || 'not installed')}</span>`}</td>
          <td>${v.latest ? esc(v.latest) + (v.update_available ? ' <span class="warn-text">(newer)</span>' : ' <span class="ok-text">(up to date)</span>') : v.id === 'yt_dlp' ? `<span class="muted small">${esc(h.yt_dlp.error ? 'could not check PyPI' : 'not checked')}</span>` : '<span class="muted">—</span>'}</td>
          <td class="hx-act">${v.updatable ? `<button class="btn sm ${v.update_available ? 'primary' : ''}" data-hx-update ${h.update_job ? 'disabled' : ''}>${v.update_available ? 'Update' : 'Reinstall latest'}</button>` : ''}</td></tr>`).join('')}
        </tbody></table>
        <div class="hx-progress" id="hx-update-progress"></div>
        <div class="small muted" style="margin-top:8px">Updating runs <code>pip install --upgrade yt-dlp</code> (or <code>uv pip</code> when the app's Python has no pip) inside the app's own Python; new downloads use it straight away. The other tools are updated by running the installer again.${h.yt_dlp.checked_at ? ` Latest version checked ${esc(fmtDate(h.yt_dlp.checked_at))}.` : ''}</div>
      </div>
      <div class="panel"><div class="row between"><h3 style="margin:0">Self-test</h3><button class="btn" id="hx-selftest" ${h.selftest_job ? 'disabled' : ''}>Run self-test</button></div>
        <p class="small muted" style="margin:6px 0 10px">Fetches the details of a short public YouTube video, decodes three seconds of audio with ffmpeg and runs Whisper on two seconds of silence (skipped until the caption model is downloaded). Nothing is posted anywhere.</p>
        <div class="hx-steps" id="hx-steps">${st && st.steps ? `<div class="small muted" style="margin-bottom:6px">Last run ${esc(fmtDate(st.t))}</div>` + st.steps.steps.map(stepRow).join('') : '<div class="muted small">Not run yet.</div>'}</div>
        <div class="hx-progress" id="hx-selftest-progress"></div>
      </div>
      <div class="panel"><h3 style="margin-top:0">Recent events</h3>
        ${h.events.length ? `<ul class="hx-events">${h.events.map(eventRow).join('')}</ul>` : '<div class="muted small">Nothing recorded yet. Download failures that look like an outdated yt-dlp, updates and self-tests show up here.</div>'}
      </div>`;
    $('#hx-selftest').onclick = async (e) => {
      e.target.disabled = true;
      try {
        const r = await post('/api/health/selftest');
        toast(r.already_running ? 'A self-test is already running' : 'Self-test started');
        follow(r.job_id, $('#hx-selftest-progress'), () => render());
      } catch (err) { toastErr(err); e.target.disabled = false; }
    };
    if (h.update_job) follow(h.update_job, $('#hx-update-progress'), async () => { await refreshHealth(); render(); });
    if (h.selftest_job) follow(h.selftest_job, $('#hx-selftest-progress'), () => render());
  };

  // Load once at start so the Today card can appear, then keep it current without hammering PyPI (the server caches it).
  refreshHealth().then(() => { if (H && H.actionable && S.view === 'home' && !$('.hx-card')) render(); });
  setInterval(async () => { const was = !!(H && H.actionable); await refreshHealth(); if (was !== !!(H && H.actionable) && S.view === 'home') render(); }, 5 * 60 * 1000);
})();
