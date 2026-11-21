/* Feature: duplicate guard. The same footage must not be posted twice for the same campaign and platform.
   Review modal: a banner above the checklist lists clips cut from the same VOD (yellow = clipped before, red = already
   posted for this campaign); when blocked, Approve stays disabled with the reason. The core approval endpoint is not
   changed: this UI guard plus the banner are the protection.
   Moment list: a small "clipped before" badge on affected rows (one request per video).
   Today: a "Duplicates" card when a pending or approved clip in this campaign is already posted. */
(() => {
  'use strict';
  if (typeof REVIEW_EXTRAS === 'undefined') return;

  const matchLine = (x) => {
    const where = x.same_campaign ? 'this campaign' : esc(x.campaign_name || 'another campaign');
    const posted = x.posted_platforms.length ? ` · posted to ${x.posted_platforms.map(esc).join(', ')}` : '';
    const ov = x.overlap_s ? ` · ${Math.round(x.overlap_s)} s overlap` : '';
    return `<div class="small"><a href="#/review/clip/${x.clip_id}">Clip ${x.clip_id}</a> for ${where} (${esc(x.status_label)})${posted}${ov}</div>`;
  };

  // ---- Review modal ----
  REVIEW_EXTRAS.push((ctx) => {
    const m = ctx.m;
    const checks = $('#checks', m);
    if (!checks) return;
    const box = document.createElement('div');
    box.id = 'dupes-banner';
    const anchor = checks.previousElementSibling && checks.previousElementSibling.tagName === 'B' ? checks.previousElementSibling : checks;
    anchor.parentNode.insertBefore(box, anchor);
    let blocked = '';
    const enforce = () => {          // after the core gate has written its state, keep Approve shut while blocked
      if (!blocked) return;
      const btn = $('#approve', m), why = $('#approve-why', m);
      if (btn && !btn.disabled) btn.disabled = true;
      if (why && why.textContent !== blocked) { why.textContent = blocked; why.className = 'small bad-text'; }
    };
    const coreGate = ctx.gate;
    ctx.gate = (...a) => { const r = coreGate(...a); enforce(); return r; };
    // The core also calls its own gate internally (trim, checklist, rules); re-assert after any of those writes.
    const obs = new MutationObserver(() => { if (!document.body.contains(m)) { obs.disconnect(); return; } enforce(); });
    const btn = $('#approve', m), why = $('#approve-why', m);
    if (btn) obs.observe(btn, { attributes: true, attributeFilter: ['disabled'] });
    if (why) obs.observe(why, { childList: true, characterData: true, subtree: true });
    get(`/api/dupes/clip/${ctx.cl.id}`).then((r) => {
      if (!r || !r.severity || !document.body.contains(m)) return;
      const red = r.severity === 'blocked';
      box.className = 'callout' + (red ? ' pink' : '');
      box.style.margin = '0 0 10px';
      if (red) box.style.borderLeftColor = 'var(--red)';
      box.innerHTML = `<b class="${red ? 'bad-text' : 'warn-text'}">${esc(r.message)}</b>
        <div class="small muted" style="margin:2px 0 4px">${red ? 'Posting the same footage twice for one campaign inflates numbers, so Approve is off for this clip.' : 'The same footage was clipped before. Make sure this one is not posted twice for the same campaign and platform.'}</div>
        ${r.matches.map(matchLine).join('')}`;
      $$('a[href^="#/review/clip/"]', box).forEach((a) => a.onclick = (e) => { e.preventDefault(); openClip(Number(a.getAttribute('href').split('/').pop())); });
      if (red) { blocked = r.message; ctx.gate(); }
    }).catch((e) => console.error(e));
  });

  // ---- moment rows ----
  window.MOMENT_EXTRAS = window.MOMENT_EXTRAS || [];
  window.MOMENT_EXTRAS.push((v, ms, box) => {
    get(`/api/dupes/video/${v.id}`).then((byMoment) => {
      if (!byMoment || !Object.keys(byMoment).length) return;
      // Rows render in the core's order: unflagged by score, then (when open) flagged by score.
      const byScore = (a, b) => b.score - a.score || a.t - b.t;
      const order = ms.filter((m) => !m.flags?.length).sort(byScore).concat(ms.filter((m) => m.flags?.length).sort(byScore));
      const rows = $$('.moment', box);
      rows.forEach((row, i) => {
        const m = order[i];
        if (!m || ($('.t', row) && $('.t', row).textContent !== m.ts)) return;
        const hits = byMoment[String(m.id)];
        if (!hits || !hits.length || $('.dupe-badge', row)) return;
        const tip = hits.map((x) => `Clip ${x.clip_id} · ${x.same_campaign ? 'this campaign' : x.campaign_name} · ${x.status_label}${x.posted_platforms.length ? ' · posted to ' + x.posted_platforms.join(', ') : ''}`).join('\n');
        const posted = hits.some((x) => x.same_campaign && x.posted_platforms.length);
        const b = document.createElement('span');
        b.className = 'badge dupe-badge ' + (posted ? 'bad' : 'warn');
        b.textContent = 'clipped before';
        b.title = tip;
        const line = $('.grow .row', row) || row;
        line.appendChild(b);
      });
    }).catch((e) => console.error(e));
  });

  // ---- Today ----
  if (typeof TODAY_EXTRAS !== 'undefined') {
    TODAY_EXTRAS.push((t, c) => {
      const slot = `dupes-today-${c.id}`;
      get(`/api/dupes/campaign/${c.id}`).then((r) => {
        const el = document.getElementById(slot);
        if (!el || !r || !r.blocked.length) return;
        el.outerHTML = `<div class="step warn">
          <div class="step-n">2×</div>
          <div class="step-body"><div class="step-title">Duplicates <b class="count">${r.blocked.length}</b></div>
            <div class="step-sub">${r.blocked.map((x) => `<div class="attn"><span><b>${esc(x.hook || 'Clip ' + x.clip_id)}</b> — ${esc(x.message)}</span><a class="btn sm" href="#/review/clip/${x.clip_id}">Open</a></div>`).join('')}</div></div>
        </div>`;
      }).catch((e) => console.error(e));
      return `<div id="${slot}"></div>`;
    });
  }
})();
