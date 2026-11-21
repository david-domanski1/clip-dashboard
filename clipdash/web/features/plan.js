/* Plan (licence) and version/update UI. Backend: clipdash/features/plan.py and clipdash/features/updates.py.
   Adds Settings → Plan, hides the Stats tab on the free plan, and two Today cards: a limit that was hit in the
   last day (free plan only) and an available update. Uses only the core helpers documented in clipdash/features/__init__.py. */
'use strict';
(() => {
  const PL = { plan: null, updates: null, statsTab: SETTINGS_TABS.find(([k]) => k === 'stats') || ['stats', 'Stats'] };

  const style = document.createElement('style');
  style.textContent = `
    .plan-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:14px}
    .plan-cols .panel{margin:0}
    .plan-cols .panel.on{border-color:var(--line-2);box-shadow:inset 0 0 0 1px var(--line-2)}
    .plan-cols ul{margin:10px 0 0;padding-left:18px;color:var(--text-2);font-size:13px}
    .plan-cols li{padding:2px 0}
    .plan-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
    .plan-head h3{margin:0}
    .plan-key{max-width:560px}
    .plan-key input{font-family:var(--mono)}
    .plan-state{font-size:13px;color:var(--muted);margin-top:8px}
    .plan-state b{color:var(--text-2);font-weight:600}`;
  document.head.appendChild(style);

  // The Stats tab (views and earnings) is part of Pro; on the free plan it is not shown.
  function applyGates() {
    const free = PL.plan && PL.plan.plan === 'free';
    const i = SETTINGS_TABS.findIndex(([k]) => k === 'stats');
    if (free && i >= 0) SETTINGS_TABS.splice(i, 1);
    if (!free && i < 0) { const p = SETTINGS_TABS.findIndex(([k]) => k === 'plan'); SETTINGS_TABS.splice(p < 0 ? SETTINGS_TABS.length : p, 0, PL.statsTab); }
  }
  async function loadPlan() {
    const before = JSON.stringify((PL.plan && PL.plan.limit_hit) || null);
    try { PL.plan = await get('/api/plan'); applyGates(); } catch (e) { /* server away; keep the last state */ }
    // A limit was hit (or cleared) since the last look: redraw Today so the card appears without a reload.
    if (PL.plan && JSON.stringify(PL.plan.limit_hit || null) !== before && S.view === 'home' && !$('#modal-root').innerHTML) render();
    return PL.plan;
  }
  async function loadUpdates(force) { try { PL.updates = await get('/api/updates' + (force ? '?force=true' : '')); } catch (e) { /* offline */ } return PL.updates; }

  SETTINGS_TABS.push(['plan', 'Plan']);

  const dateOnly = (iso) => iso ? new Date(iso).toLocaleDateString([], { dateStyle: 'medium' }) : '';
  function licenceState(p) {
    const l = p.license;
    if (!l) return 'No licence key entered. The free plan is active.';
    if (l.dev) return `Developer key <b>${esc(l.key_masked)}</b>: Pro is active on this computer without contacting the licence server.`;
    if (p.plan === 'free') return `Key <b>${esc(l.key_masked)}</b> is no longer valid${l.last_error ? ': ' + esc(l.last_error) : ''}. ${l.last_validated_at ? 'Last confirmed ' + dateOnly(l.last_validated_at) + '. ' : ''}The free plan is active until a check succeeds.`;
    const left = p.grace_days_left;
    return `Key <b>${esc(l.key_masked)}</b>${l.email_masked ? ' · ' + esc(l.email_masked) : ''} · last confirmed ${dateOnly(l.last_validated_at) || 'never'}`
      + (l.valid_until ? ` · renews ${dateOnly(l.valid_until)}` : '')
      + (left != null && left <= 5 ? ` · <span class="warn-text">works offline for ${left} more day${left === 1 ? '' : 's'}; connect to the internet so it can be confirmed</span>` : '');
  }

  PANES.plan = async (el) => {
    const [p, u] = await Promise.all([loadPlan(), PL.updates || loadUpdates()]);
    if (!p) { el.innerHTML = '<div class="callout pink">Could not load the plan. Is the server running?</div>'; return; }
    const lim = p.limits;
    const plans = p.plans;
    const col = (key) => `<div class="panel ${p.plan === key ? 'on' : ''}"><div class="plan-head"><h3>${esc(plans[key].name)}</h3>${p.plan === key ? '<span class="badge ok">Your plan</span>' : ''}</div>
      <ul>${plans[key].includes.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      ${key === 'pro' && p.plan !== 'pro' ? `<div class="row" style="margin-top:14px"><a class="btn primary" href="${esc(p.upgrade_url)}" target="_blank" rel="noopener">Get Pro</a><span class="small muted">Opens the checkout page. You get a licence key by email.</span></div>` : ''}
      ${key === 'free' ? `<div class="plan-state">Campaigns used: <b>${lim.campaigns.used}</b> of ${lim.campaigns.allowed == null ? 'unlimited' : lim.campaigns.allowed} (the Streamer clips workspace does not count)</div>` : ''}</div>`;
    el.innerHTML = `<h2 style="margin-top:0">Plan</h2><p class="sub">You are on the <b>${esc(plans[p.plan].name)}</b> plan. Your licence and your data stay on this computer; only the key itself is sent to the licence server.</p>
      <div class="plan-cols">${col('free')}${col('pro')}</div>
      <div class="panel" style="margin-top:14px"><h3 style="margin-top:0">Licence key</h3>
        <div class="plan-state" id="plan-state">${licenceState(p)}</div>
        ${p.license ? `<div class="row" style="margin-top:12px"><button class="btn" id="plan-validate">Check now</button><button class="btn danger" id="plan-deactivate">Deactivate on this computer</button></div>`
          : `<div class="plan-key" style="margin-top:12px"><div class="field"><label>Paste the key from your purchase email</label><div class="row"><input type="text" id="plan-key" placeholder="XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX" spellcheck="false"><button class="btn primary" id="plan-activate">Activate</button></div></div>
             <div class="small muted">One key works on a limited number of computers; deactivate here before moving to a new one. It is checked online about once a day and keeps working offline for ${p.grace_days} days.</div></div>`}
      </div>
      <div class="panel" style="margin-top:14px"><h3 style="margin-top:0">Version</h3>
        <div class="plan-state" id="upd-state">${updateState(u)}</div>
        <div class="row" style="margin-top:12px"><button class="btn" id="upd-check">Check now</button>${u && u.available ? `<a class="btn primary" href="${esc(u.url)}" target="_blank" rel="noopener">Download v${esc(u.latest)}</a>` : ''}</div>
      </div>`;
    const act = $('#plan-activate', el);
    if (act) act.onclick = async () => {
      act.disabled = true;
      try { await post('/api/plan/activate', { key: $('#plan-key', el).value.trim() }); toast('Pro is active'); await loadPlan(); render(); }
      catch (e) { toastErr(e); act.disabled = false; }
    };
    const val = $('#plan-validate', el);
    if (val) val.onclick = async () => {
      val.disabled = true;
      try { const r = await post('/api/plan/validate'); toast(r.validation === 'ok' ? 'Licence confirmed' : r.validation === 'offline' ? 'Offline: could not reach the licence server' : 'The licence is no longer valid', r.validation === 'ok' ? '' : 'warn'); await loadPlan(); render(); }
      catch (e) { toastErr(e); val.disabled = false; }
    };
    const deact = $('#plan-deactivate', el);
    if (deact) deact.onclick = async () => {
      if (!(await confirmDlg('Deactivate the licence on this computer? Pro features switch off here and the key can be used on another computer.', 'Deactivate'))) return;
      try { await post('/api/plan/deactivate'); toast('Deactivated'); await loadPlan(); render(); } catch (e) { toastErr(e); }
    };
    $('#upd-check', el).onclick = async () => {
      const b = $('#upd-check', el); b.disabled = true;
      const r = await loadUpdates(true);
      $('#upd-state', el).innerHTML = updateState(r);
      toast(r && r.error ? r.error : r && r.available ? `Version ${r.latest} is available` : 'You have the latest version', r && r.error ? 'warn' : '');
      b.disabled = false; if (r && r.available) render();
    };
  };

  function updateState(u) {
    const cur = (S.state && S.state.version) || (u && u.current) || '';
    if (!u) return `Clip Dashboard <b>v${esc(cur)}</b>. Updates have not been checked yet.`;
    if (u.error) return `Clip Dashboard <b>v${esc(cur)}</b>. ${esc(u.error)}`;
    if (u.available) return `Clip Dashboard <b>v${esc(cur)}</b>. <b>Version ${esc(u.latest)}</b> is available${u.notes_short ? ': ' + esc(u.notes_short) : '.'}`;
    return `Clip Dashboard <b>v${esc(cur)}</b> is the latest version${u.checked_at ? ' (checked ' + fmtDate(u.checked_at) + ')' : ''}.`;
  }

  // Today: a limit hit in the last day (free plan only).
  TODAY_EXTRAS.push(() => {
    const p = PL.plan;
    if (!p || p.plan !== 'free' || !p.limit_hit) return '';
    return `<div class="step warn"><div class="step-n">★</div>
      <div class="step-body"><div class="step-title">Free plan limit reached</div><div class="step-sub">${esc(p.limit_hit.message)}</div></div>
      <div class="row"><a class="btn primary" href="${esc(p.upgrade_url)}" target="_blank" rel="noopener">Upgrade</a><a class="btn" href="#/settings/plan">Plan</a></div></div>`;
  });

  // Today: a newer release exists.
  TODAY_EXTRAS.push(() => {
    const u = PL.updates;
    if (!u || !u.available) return '';
    return `<div class="step"><div class="step-n">↑</div>
      <div class="step-body"><div class="step-title">Update available: v${esc(u.latest)}</div><div class="step-sub">${u.notes_short ? esc(u.notes_short) : 'See what changed and download the new version.'}</div></div>
      <a class="btn" href="${esc(u.url)}" target="_blank" rel="noopener">What's new</a></div>`;
  });

  loadPlan(); loadUpdates();
  setInterval(loadPlan, 60 * 1000);                 // picks up a limit hit or an activation made in another tab
  setInterval(() => loadUpdates(false), 60 * 60 * 1000);   // the server itself only asks GitHub every 12 h
  window.addEventListener('hashchange', () => { if ((location.hash.replace(/^#\/?/, '') || 'home') === 'home') loadPlan(); });
})();
