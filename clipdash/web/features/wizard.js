/* First-run wizard at #/welcome: four steps, one screen each, one primary button. Tools → first campaign (demo or
   your own) → first clip (the demo video is searched while you watch) → what Review does. "Skip" on every step.
   Backend: clipdash/features/wizard.py. Styles are injected here; the core stylesheet is untouched. */
'use strict';
(() => {
  const DEMO_NAME = 'Demo campaign (practice)';
  const STEPS = [['tools', 'Tools'], ['campaign', 'Your first campaign'], ['clip', 'First clip'], ['review', 'Review']];
  const idx = (k) => STEPS.findIndex(([s]) => s === k);
  const W = { mode: null, waitingForCampaign: false, polls: [], lastKey: null, clipKey: null };

  const style = document.createElement('style');
  style.textContent = `
    .wiz{max-width:760px;margin:0 auto;padding-top:18px}
    .wiz-progress{display:flex;gap:8px;margin-bottom:26px;flex-wrap:wrap}
    .wiz-dot{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;color:var(--faint);padding:5px 12px 5px 5px;border-radius:999px;border:1px solid var(--line)}
    .wiz-dot span{width:22px;height:22px;border-radius:50%;background:var(--surface-3);display:inline-flex;align-items:center;justify-content:center;font-size:11px;color:var(--text-2)}
    .wiz-dot.on{color:var(--text);border-color:var(--line-2)}
    .wiz-dot.on span{background:var(--text);color:var(--bg)}
    .wiz-dot.done{color:var(--muted)}
    .wiz-dot.done span{background:rgba(61,220,151,.18);color:var(--green)}
    .wiz .sub{margin-bottom:0}
    .wiz-body{margin-top:18px}
    .wiz-actions{display:flex;gap:10px;align-items:center;margin-top:18px;flex-wrap:wrap}
    .wiz-foot{margin-top:34px;font-size:12px}
    .wiz-foot a{color:var(--muted);border-bottom-color:transparent}
    .wiz-foot a:hover{color:var(--text)}
    .wiz-choice{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
    .wiz-choice .panel{display:flex;flex-direction:column;gap:8px;margin:0}
    .wiz-choice .panel p{color:var(--muted);font-size:13px;margin:0 0 8px}
    .wiz-choice .panel .btn{align-self:flex-start;margin-top:auto}
    .wiz-copy p{color:var(--text-2)}
    .wiz-copy ul,.wiz-copy ol{padding-left:20px;margin:0}
    .wiz-copy li{margin:7px 0;color:var(--text-2)}
    .wiz .rule{padding:8px 0;border-top:1px solid var(--line);align-items:flex-start}
    .wiz .rule:first-child{border-top:0}
    .wiz .rule .dot{margin-top:6px}
    .wiz .rule.optional{opacity:.7}
    .wiz .rule.optional .dot{background:var(--faint);box-shadow:none}
    .wiz .rule b{min-width:150px}
    .wiz-note{font-size:13px;color:var(--muted);margin-top:10px}`;
  document.head.appendChild(style);

  // ---- helpers
  const stopPolls = () => { W.polls.forEach(clearInterval); W.polls = []; };
  const poll = (fn, ms) => {
    const id = setInterval(async () => { if (S.view !== 'welcome') return clearInterval(id); try { await fn(id); } catch (e) { console.error(e); } }, ms);
    W.polls.push(id); return id;
  };
  const at = (step) => location.hash.replace(/^#\/?/, '') === 'welcome/' + step;
  const go = (step) => { stopPolls(); location.hash = '#/welcome/' + step; };
  const finish = async (to) => {
    stopPolls();
    try { await post('/api/setup/done'); await put('/api/settings', { wizard_done: true }); } catch (e) { toastErr(e); }
    if (S.state) S.state.setup_done = true;
    location.hash = to;
  };
  const pick = (id) => { S.campaignId = id; localStorage.setItem('campaignId', id); };
  const frame = (step, title, sub, body) => `
    <div class="wiz">
      <div class="wiz-progress">${STEPS.map(([k, l], i) => `<div class="wiz-dot ${k === step ? 'on' : ''} ${i < idx(step) ? 'done' : ''}"><span>${i < idx(step) ? '✓' : i + 1}</span>${esc(l)}</div>`).join('')}</div>
      <div class="eyebrow">Step ${idx(step) + 1} of ${STEPS.length}</div><h1>${esc(title)}</h1><p class="sub">${sub}</p>
      <div class="wiz-body">${body}</div>
      <div class="wiz-foot"><a href="#" data-skip>Skip, I know what I'm doing</a></div>
    </div>`;
  const wire = (el) => {
    const sk = $('[data-skip]', el); if (sk) sk.onclick = (e) => { e.preventDefault(); finish('#/'); };
    $$('[data-next]', el).forEach((b) => b.onclick = () => go(b.dataset.next));
  };

  // ---- step 1: tools
  const TOOL_WORDS = {
    'Python': ['Python', 'runs the app'],
    'ffmpeg': ['Video tools', 'cuts the clips, burns in captions and lays them out vertically'],
    'yt-dlp': ['Downloader', 'fetches only the parts of a video that are needed'],
    'Whisper captions': ['Caption model', 'turns speech into captions; about 500 MB, downloaded once'],
    'TwitchDownloaderCLI': ['Twitch chat tool', 'only needed for Twitch streams; download it from Settings → Setup if you add one later'],
  };
  const toolsStep = async (el) => {
    W.lastKey = null;
    const draw = async (id) => {
      const s = await get('/api/setup/checks');
      const running = (S.state?.jobs_running || []).find((j) => ['download_whisper', 'install_tdcli'].includes(j.type));
      const key = JSON.stringify(s.checks) + (running ? running.message : '');
      if (key === W.lastKey) return;
      W.lastKey = key;
      const required = s.checks.filter((c) => c.name !== 'TwitchDownloaderCLI');
      const ok = required.every((c) => c.ok);
      const rows = s.checks.map((c) => {
        const [name, what] = TOOL_WORDS[c.name] || [c.name, ''];
        const optional = c.name === 'TwitchDownloaderCLI';
        const dl = c.action && !c.ok && !optional ? (running && running.type === c.action ? `<span class="small warn-text">⟳ ${esc(running.message || 'Downloading…')}</span>` : `<button class="btn sm primary" data-action="${c.action}">Download</button>`) : '';
        return `<div class="rule ${c.ok ? '' : 'bad'} ${optional ? 'optional' : ''}"><span class="dot"></span><b>${esc(name)}</b>
          <span class="grow"><span>${esc(c.ok ? 'Ready' : (optional ? 'Not needed yet' : 'Not ready'))}</span> <span class="muted">· ${esc(what)}</span>
            ${c.ok || optional ? '' : `<br><span class="small muted">${esc(c.fix || c.detail)}</span>`}</span>${dl}</div>`;
      }).join('');
      el.innerHTML = frame('tools', 'Tools', 'What the app needs on this computer. Green means ready; this step moves on by itself.', `
        <div class="panel">${rows}</div>
        <div class="wiz-actions">${ok ? '<button class="btn primary" data-next="campaign">Continue</button><span class="muted small">All set, moving on…</span>'
          : '<span class="muted small">Fix the red items to continue. The caption model downloads in the background; this page updates on its own.</span>'}</div>`);
      wire(el);
      $$('[data-action]', el).forEach((b) => b.onclick = () => post('/api/setup/action', { action: b.dataset.action }).then(() => { toast('Downloading in the background'); b.disabled = true; b.textContent = 'Downloading…'; refreshState(); }).catch(toastErr));
      if (ok) { if (id) clearInterval(id); setTimeout(() => { if (S.view === 'welcome' && at('tools')) go('campaign'); }, 1500); }
    };
    await draw();
    poll(draw, 3000);
  };

  // ---- step 2: first campaign
  const campaignStep = async (el) => {
    await loadCampaigns();
    const cur = campaign();
    const isOwn = (c) => c && !c.workspace && c.name !== DEMO_NAME;
    if (W.waitingForCampaign && isOwn(cur)) { W.waitingForCampaign = false; W.mode = 'own'; go('clip'); return; }
    const own = S.campaigns.filter(isOwn);
    el.innerHTML = frame('campaign', 'Your first campaign', 'Clips are made for a campaign: a streamer, their rules, and where the links go.', `
      <div class="wiz-choice">
        <div class="panel"><b>Try the demo</b><p>A practice campaign with one short public video (the GTA VI trailer, 1½ minutes). You will search it, make a clip and review it. Nothing from it should be posted; you delete it afterwards.</p><button class="btn primary" data-demo>Try the demo</button></div>
        <div class="panel"><b>I have a campaign link</b><p>Paste the campaign page or its rules. The app fills in the rules it can check (names, hashtags, length, dates) and keeps the rest as a checklist for you.</p><button class="btn" data-own>Enter my campaign</button></div>
      </div>
      ${own.length ? `<div class="wiz-note">You already have <b>${esc(own[0].name)}</b>. <a href="#" data-use="${own[0].id}">Continue with it →</a></div>` : ''}`);
    wire(el);
    $('[data-demo]', el).onclick = async (e) => {
      const b = e.currentTarget; b.disabled = true; b.textContent = 'Setting up…';
      try { const r = await post('/api/wizard/demo'); pick(r.campaign.id); W.mode = 'demo'; await loadCampaigns(); go('clip'); }
      catch (err) { toastErr(err); b.disabled = false; b.textContent = 'Try the demo'; }
    };
    $('[data-own]', el).onclick = () => { W.waitingForCampaign = true; W.mode = 'own'; openCampaignEditor(); };
    const use = $('[data-use]', el); if (use) use.onclick = (e) => { e.preventDefault(); pick(Number(use.dataset.use)); W.mode = 'own'; go('clip'); };
  };

  // ---- step 3: first clip
  const ownClipStep = (el) => {
    el.innerHTML = frame('clip', 'First clip', 'Clips come from moments the app finds in a video; you decide which ones get made.', `
      <div class="panel wiz-copy"><ol>
        <li>Under <b>Sources</b>, add the streamer's channel or one video link. Channels are checked every few minutes and new videos show up on Today.</li>
        <li>Click <b>Pick a section</b> on a video, choose the part to search and the clip length, then <b>Find moments</b>.</li>
        <li>The moments are ranked. Click <b>Render</b> on one; a minute later the clip is waiting on the Review page.</li>
      </ol></div>
      <div class="wiz-actions"><a class="btn" href="#/sources">Open Sources</a><button class="btn primary" data-next="review">Continue</button></div>`);
    wire(el);
  };
  const demoClipStep = async (el) => {
    el.innerHTML = frame('clip', 'First clip', 'The app searches the demo video for its strongest moment while you watch.', `<div class="panel"><div class="muted" id="wz-wait">Preparing the demo…</div></div>`);
    wire(el);
    let r;
    try { r = await post('/api/wizard/demo'); } catch (e) {
      $('#wz-wait', el).innerHTML = `<span class="bad-text">${esc(e.message)}</span> <button class="btn sm" id="wz-again">Try again</button>`;
      $('#wz-again', el).onclick = () => demoClipStep(el); return;
    }
    pick(r.campaign.id); if (!S.campaigns.find((c) => c.id === r.campaign.id)) await loadCampaigns();
    const vid = r.video.id;
    let sec;
    try { sec = await get(`/api/videos/${vid}/section?refresh=1`); } catch (e) { $('#wz-wait', el).innerHTML = `<span class="bad-text">${esc(e.message)}</span>`; return; }
    const v = { id: vid, url: sec.url, platform: sec.platform };
    el.innerHTML = frame('clip', 'First clip', 'The app searches the demo video for its strongest moment while you watch.', `
      <div class="panel">
        <div class="row between"><div><b>${esc(sec.title || 'Grand Theft Auto VI trailer')}</b><div class="small muted">${sec.duration ? fmtTime(sec.duration) + ' long · ' : ''}looking for ${r.clip_len}-second clips · <a href="${esc(sec.url)}" target="_blank">open on YouTube ↗</a></div></div></div>
        <div id="wz-progress" class="hidden" style="margin-top:12px"><div class="small" id="wz-stage">Waiting to search…</div><div class="progress" style="margin-top:4px"><i id="wz-bar" style="width:2%"></i></div>
          <div class="wiz-note">It reads YouTube's "most replayed" curve, the audio and the words, then ranks the strongest moments. About half a minute.</div></div>
        <div id="wz-error" class="hidden callout pink"></div>
        <div id="wz-moments"></div>
        <div id="wz-hint" class="hidden callout" style="margin-top:14px"><b>Click Render on one.</b> Making the clip takes about a minute: that part of the video is downloaded, captions are written from what is said, and it is laid out vertically. Then it waits for you on the Review page.</div>
      </div>
      <div class="wiz-actions"><button class="btn primary hidden" id="wz-next" data-next="review">Continue to Review</button><button class="btn hidden" id="wz-retry">Search again</button></div>`);
    wire(el);
    const box = $('#wz-moments', el), prog = $('#wz-progress', el), err = $('#wz-error', el);
    const show = (sel, on) => $(sel, el).classList.toggle('hidden', !on);
    const showMoments = async () => {
      await renderMoments(v, box, { clipLen: () => r.clip_len, leadIn: () => (sec.lead_in != null ? Number(sec.lead_in) : (r.lead_in ?? 10)), empty: 'No moment stood out this time. Search again, or carry on to Review and come back later.' });
      show('#wz-hint', true); show('#wz-next', true); show('#wz-retry', true);
    };
    const pollClips = () => poll(async () => {
      const ms = await get(`/api/videos/${vid}/moments`);
      const key = ms.map((m) => m.clip ? `${m.clip.id}:${m.clip.status}:${m.render_job?.message || ''}` : '').join('|');
      if (W.clipKey !== null && key !== W.clipKey) {
        await showMoments();
        if (ms.some((m) => m.clip && m.clip.status === 'pending') && !/pending/.test(W.clipKey)) { toast('Your first clip is ready to review'); $('#wz-next', el).textContent = 'Review it'; }
        refreshState();
      }
      W.clipKey = key;
    }, 3000);
    const pollScan = () => poll(async (id) => {
      const f = await get(`/api/videos/${vid}/section`);
      $('#wz-stage', el).textContent = f.stage || ''; $('#wz-bar', el).style.width = Math.max(2, Math.round((f.progress || 0) * 100)) + '%';
      if (['queued', 'scanning'].includes(f.status)) return;
      clearInterval(id); show('#wz-progress', false); sec = f;
      if (f.status === 'error') { err.textContent = f.stage || 'The search failed.'; show('#wz-error', true); show('#wz-retry', true); return; }
      await showMoments(); refreshState(); W.clipKey = null; pollClips();
    }, 2000);
    const startSearch = async () => {
      show('#wz-error', false); show('#wz-retry', false);
      try { await post(`/api/videos/${vid}/rescan`, { window: r.window, clip_len: r.clip_len, lead_in: r.lead_in ?? 10 }); }
      catch (e) { err.textContent = e.message; show('#wz-error', true); show('#wz-retry', true); return; }
      $('#wz-stage', el).textContent = 'Waiting to search…'; $('#wz-bar', el).style.width = '2%'; show('#wz-progress', true); pollScan();
    };
    $('#wz-retry', el).onclick = startSearch;
    if (['queued', 'scanning'].includes(sec.status)) { $('#wz-stage', el).textContent = sec.stage || ''; show('#wz-progress', true); pollScan(); }
    else if (sec.moments.length) { await showMoments(); W.clipKey = null; pollClips(); }
    else if (!sec.duration) { err.textContent = 'The demo video could not be read (no internet, or YouTube is not reachable). Try again in a moment.'; show('#wz-error', true); show('#wz-retry', true); }
    else if (sec.status === 'error') { err.textContent = sec.stage || 'The last search failed.'; show('#wz-error', true); show('#wz-retry', true); }
    else if ((sec.scanned_ranges || []).length) { await showMoments(); }
    else await startSearch();
  };
  const clipStep = async (el) => {
    const cur = campaign();
    const mode = W.mode || (cur && cur.name === DEMO_NAME ? 'demo' : (cur && !cur.workspace ? 'own' : null));
    if (!mode) { go('campaign'); return; }
    W.mode = mode;
    if (mode === 'own') ownClipStep(el); else await demoClipStep(el);
  };

  // ---- step 4: review
  const reviewStep = async (el) => {
    let st = null; try { st = await get('/api/wizard/state'); } catch (e) { st = null; }
    const n = st ? st.clips_waiting : 0;
    const demo = W.mode === 'demo' || (campaign() && campaign().name === DEMO_NAME);
    el.innerHTML = frame('review', 'Review', 'Every clip is watched and approved by you. Nothing is posted on its own.', `
      <div class="panel wiz-copy">
        <p>${n ? `<b>${n} clip${n === 1 ? ' is' : 's are'} waiting</b> on the Review page.` : 'Your first clip appears on the Review page as soon as it is made (about a minute after you clicked Render; the sidebar shows what is running).'}</p>
        <ul>
          <li><b>Hook</b> — the sentence the clip opens on, taken from what was actually said. Choose one of the alternatives or edit it.</li>
          <li><b>Checklist</b> — the things only a person can judge. Tick each one after watching.</li>
          <li><b>Approve</b> stays off until the checklist is ticked and every campaign rule passes; the reason is shown next to it. <b>Reject</b> drops the clip.</li>
          <li><b>Keys</b> — space plays and pauses, <b>A</b> approves, <b>R</b> rejects, <b>→</b> opens the next clip.</li>
        </ul>
        ${demo ? '<p class="wiz-note">Today shows a <b>Practice</b> card while the demo campaign is selected. Nothing from it should be posted; delete it there when you are done.</p>' : ''}
      </div>
      <div class="wiz-actions"><button class="btn primary" id="wz-open">Open Review</button></div>`);
    wire(el);
    $('#wz-open', el).onclick = () => finish('#/review');
  };

  const STEP_VIEWS = { tools: toolsStep, campaign: campaignStep, clip: clipStep, review: reviewStep };
  VIEWS.welcome = async (el, arg) => {
    stopPolls();
    let step = STEPS.some(([k]) => k === arg) ? arg : null;
    if (!step) {
      try { const st = await get('/api/wizard/state'); step = st.step; if (st.demo_video_id && !W.mode) W.mode = 'demo'; } catch (e) { step = 'tools'; }
      history.replaceState(null, '', '#/welcome/' + step);
    }
    await STEP_VIEWS[step](el);
  };

  // ---- Today: the practice campaign is recognisable and easy to remove
  TODAY_EXTRAS.push((t, c) => c && c.name === DEMO_NAME && !c.workspace ? `
    <div class="step">
      <div class="step-n">P</div>
      <div class="step-body"><div class="step-title">Practice</div><div class="step-sub">This is the practice campaign: nothing here should be posted. Delete it when you are done.</div></div>
      <button class="btn danger" onclick="wizardDeleteDemo(${Number(c.id)})">Delete</button>
    </div>` : '');
  window.wizardDeleteDemo = async (id) => {
    if (!(await confirmDlg('Delete the practice campaign with its video and clips?', 'Delete'))) return;
    try { await del(`/api/campaigns/${id}`); toast('Practice campaign deleted'); await loadCampaigns(); render(); } catch (e) { toastErr(e); }
  };
})();
