/* Feature: notifications. A Settings tab to turn on desktop, Discord and phone (ntfy) notifications, send a test on
   each, pick which events notify, and see the last 50 sent. A Today card appears only while no channel is on. */
(() => {
  'use strict';
  if (typeof PANES === 'undefined' || typeof SETTINGS_TABS === 'undefined') return;

  const css = document.createElement('style');
  css.textContent = `
    .nt-ch{display:grid;grid-template-columns:1fr auto;gap:8px 12px;align-items:center;padding:12px 0;border-top:1px solid var(--line)}
    .nt-ch:first-of-type{border-top:0;padding-top:0}
    .nt-ch .field{margin:0;grid-column:1 / -1}
    .nt-res{grid-column:1 / -1;font-size:12px;min-height:0}
    .nt-res:empty{display:none}
    .nt-howto{margin:6px 0 0 18px;padding:0;color:var(--text-2);font-size:13px;line-height:1.6}
    .nt-recent{display:flex;flex-direction:column;gap:6px}
    .nt-item{display:flex;gap:10px;align-items:baseline;font-size:13px}
    .nt-item .t{color:var(--muted);font-size:12px;white-space:nowrap;min-width:64px}
    .nt-item .x{flex:1;min-width:0;overflow-wrap:anywhere}`;
  document.head.appendChild(css);

  let state = null;            // last /api/notify/settings, used by the Today card
  const load = async () => { try { state = await get('/api/notify/settings'); } catch (e) { state = null; } return state; };

  const EVENTS = [
    ['notify_on_new_video', 'A new video or live stream is found'],
    ['notify_on_clip_ready', 'A clip is ready to review'],
    ['notify_on_failure', 'Something fails (at most one a minute)'],
    ['notify_on_post_live', 'A post goes live (with the submission window)'],
    ['notify_on_submission_due', 'A submission is due in 10 minutes, or was missed'],
  ];
  const EVENT_WORDS = { 'video.new': 'New video', 'clip.ready': 'Clip ready', 'clip.failed': 'Failure', 'job.failed': 'Failure',
    'post.live': 'Posted', 'submission.due': 'Deadline', 'submission.missed': 'Missed', test: 'Test' };
  const CH_WORDS = { desktop: 'Desktop', discord: 'Discord', ntfy: 'Phone' };

  const recentHtml = (items) => items.length ? items.map((n) => {
    const status = n.ok === null ? '<span class="muted">sending…</span>'
      : n.ok ? `<span class="ok-text">sent</span> <span class="muted">· ${n.channels.map((c) => CH_WORDS[c] || c).join(', ')}</span>`
        : `<span class="bad-text">${esc(n.error || 'not sent')}</span>`;
    return `<div class="nt-item"><span class="t">${new Date(n.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
      <span class="x"><b>${esc(EVENT_WORDS[n.event] || n.event)}</b> · ${esc(n.text)}<br><span class="small">${status}</span></span></div>`;
  }).join('') : '<div class="muted small">Nothing sent yet since the app started.</div>';

  PANES.notify = async (el) => {
    const [st, items] = await Promise.all([load(), get('/api/notify/recent')]);
    const chk = (k) => (st[k] ? 'checked' : '');
    const topic = st.notify_ntfy_topic || st.suggested_topic;
    el.innerHTML = `<h2 style="margin-top:0">Notifications</h2>
      <p class="sub">Get told when a clip is ready and before a submission window closes. Everything is sent straight from this computer; nothing goes anywhere you did not set up.</p>
      <div class="panel">
        <div class="nt-ch">
          <label class="check"><input type="checkbox" id="nt-desktop" ${chk('notify_desktop')}> Desktop notifications on this computer</label>
          <button class="btn sm" data-test="desktop">Send test</button>
          ${st.desktop_available ? '' : '<div class="nt-res warn-text">No desktop notifier found here (on Linux, install notify-send).</div>'}
          <div class="nt-res" id="nt-res-desktop"></div>
        </div>
        <div class="nt-ch">
          <label class="check"><input type="checkbox" id="nt-discord" ${chk('notify_discord')}> Discord</label>
          <button class="btn sm" data-test="discord">Send test</button>
          <div class="field"><label>Webhook URL (Discord channel → Edit channel → Integrations → Webhooks → Copy webhook URL)</label>
            <input type="text" id="nt-webhook" value="${esc(st.notify_discord_webhook || '')}" placeholder="https://discord.com/api/webhooks/…" autocomplete="off" spellcheck="false"></div>
          <div class="nt-res" id="nt-res-discord"></div>
        </div>
        <div class="nt-ch">
          <label class="check"><input type="checkbox" id="nt-ntfy" ${chk('notify_ntfy')}> Phone push (ntfy, free)</label>
          <button class="btn sm" data-test="ntfy">Send test</button>
          <div class="field"><label>Topic</label><input type="text" id="nt-topic" value="${esc(topic)}" autocomplete="off" spellcheck="false"></div>
          <ol class="nt-howto">
            <li>Install the <b>ntfy</b> app on your phone (App Store or Google Play).</li>
            <li>Tap <b>+</b> and subscribe to the topic <b class="mono" id="nt-topic-show">${esc(topic)}</b> (server ntfy.sh).</li>
            <li>Keep the topic private: anyone who knows it can read these notifications. The random one above is fine.</li>
          </ol>
          <div class="nt-res" id="nt-res-ntfy"></div>
        </div>
        <div class="row" style="margin-top:12px"><button class="btn primary" id="nt-save">Save</button><span class="small muted" id="nt-saved"></span></div>
      </div>
      <div class="panel">
        <b>Notify me when</b>
        <div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">
          ${EVENTS.map(([k, l]) => `<label class="check"><input type="checkbox" data-ev="${k}" ${chk(k)}> ${esc(l)}</label>`).join('')}
        </div>
      </div>
      <div class="panel">
        <div class="row between"><b>Recent</b><button class="btn sm ghost" id="nt-refresh">Refresh</button></div>
        <div class="nt-recent" id="nt-recent" style="margin-top:10px">${recentHtml(items)}</div>
      </div>`;

    const values = () => ({
      notify_desktop: $('#nt-desktop').checked, notify_discord: $('#nt-discord').checked, notify_ntfy: $('#nt-ntfy').checked,
      notify_discord_webhook: $('#nt-webhook').value.trim(), notify_ntfy_topic: $('#nt-topic').value.trim(),
    });
    const refreshRecent = async () => { try { $('#nt-recent').innerHTML = recentHtml(await get('/api/notify/recent')); } catch (e) { /* the tab may be gone */ } };
    $('#nt-topic').oninput = (e) => { $('#nt-topic-show').textContent = e.target.value.trim(); };
    $('#nt-save').onclick = async () => {
      const v = values();
      if (v.notify_discord && !v.notify_discord_webhook) { toast('Paste the Discord webhook URL first', 'warn'); return; }
      if (v.notify_ntfy && !v.notify_ntfy_topic) { toast('Choose an ntfy topic first', 'warn'); return; }
      try { await put('/api/settings', v); await load(); toast('Notifications saved'); $('#nt-saved').textContent = 'Saved.'; } catch (e) { toastErr(e); }
    };
    $$('[data-ev]', el).forEach((cb) => cb.onchange = async () => {
      try { await put('/api/settings', { [cb.dataset.ev]: cb.checked }); await load(); } catch (e) { toastErr(e); cb.checked = !cb.checked; }
    });
    $$('[data-test]', el).forEach((b) => b.onclick = async () => {
      const ch = b.dataset.test, res = $(`#nt-res-${ch}`), body = { channel: ch };
      if (ch === 'discord') body.webhook = $('#nt-webhook').value.trim();
      if (ch === 'ntfy') body.topic = $('#nt-topic').value.trim();
      b.disabled = true; res.className = 'nt-res muted'; res.textContent = 'Sending…';
      try {
        const r = await post('/api/notify/test', body);
        res.className = 'nt-res ' + (r.ok ? 'ok-text' : 'bad-text'); res.textContent = r.detail;
      } catch (e) { res.className = 'nt-res bad-text'; res.textContent = e.message || String(e); }
      b.disabled = false; refreshRecent();
    });
    $('#nt-refresh').onclick = refreshRecent;
  };
  SETTINGS_TABS.push(['notify', 'Notifications']);

  TODAY_EXTRAS.push(() => {
    if (!state || (state.enabled_channels || []).length) return '';
    return `<div class="step">
      <div class="step-n">◉</div>
      <div class="step-body"><div class="step-title">Notifications are off</div>
        <div class="step-sub">Turn on notifications so you never miss a 30-minute submission window.</div></div>
      <a class="btn primary" href="#/settings/notify">Turn on</a>
    </div>`;
  });

  // The Today card needs the settings; re-render Today once they arrive.
  load().then(() => { if (state && !(state.enabled_channels || []).length && S.view === 'home') render(); });
})();
